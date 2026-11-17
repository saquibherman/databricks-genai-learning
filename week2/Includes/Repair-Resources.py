# Databricks notebook source
# MAGIC %md
# MAGIC # Repair Resources (Admin-only)
# MAGIC
# MAGIC Run on a schedule by the **Repair Resources** job, which `Workspace-Setup` deploys.
# MAGIC
# MAGIC 1. Checks every shared resource that `Workspace-Setup` creates
# MAGIC 2. Recreates only the resources that are missing
# MAGIC 3. Re-checks and **raises** if anything is still missing, so the job run fails and the failure is reported
# MAGIC
# MAGIC The names and create logic below are copies of the ones in `Workspace-Setup`. **If you change one, change the other.**

# COMMAND ----------

import sys, os
includes_dir = "."
config_version = "workspace-setup"
sys.path.insert(0, os.path.abspath('.'))
config_path = f"{includes_dir}/config/config-{config_version}.yaml"

from _lib.library_installer import install_libraries
install_libraries(config_path)

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os

from databricks.sdk import WorkspaceClient

ws = WorkspaceClient()

# --- Same names as Workspace-Setup ---
SHARED_CATALOG   = "dbacademy"
SHARED_SCHEMA    = "airbnb_agent"
SCHEMA_FULL_NAME = f"{SHARED_CATALOG}.{SHARED_SCHEMA}"

DATABRICKS_SHARE_NAME = "dbacademy_airbnb"
TABLE_NAME            = "sf_airbnb_listings"
TABLE_FULL_NAME       = f"{SCHEMA_FULL_NAME}.{TABLE_NAME}"
SOURCE_CSV_PATH       = f"/Volumes/{DATABRICKS_SHARE_NAME}/v01/sf-listings/sf-airbnb.csv"

UC_GRANTS = [   # (securable type, securable name, privilege) granted to `account users`
    ("CATALOG", SHARED_CATALOG, "USE CATALOG"),
    ("SCHEMA", SCHEMA_FULL_NAME, "USE SCHEMA"),
    ("TABLE", TABLE_FULL_NAME, "SELECT"),
]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Check Resources

# COMMAND ----------

def _has_grant(sec_type, sec_name, privilege):
    rows = spark.sql(f"SHOW GRANTS ON {sec_type} {sec_name}").collect()
    return any(r["Principal"] == "account users" and r["ActionType"] == privilege for r in rows)


def find_missing():
    """Check every resource. Returns [(kind, name, details)] for anything missing.

    kind is one of: namespace, table, grant.
    """
    checks = [
        ("namespace", f"Catalog {SHARED_CATALOG}", lambda: ws.catalogs.get(SHARED_CATALOG)),
        ("namespace", f"Schema {SCHEMA_FULL_NAME}", lambda: ws.schemas.get(SCHEMA_FULL_NAME)),
        ("table", f"Table {TABLE_FULL_NAME} (has rows)", lambda: spark.table(TABLE_FULL_NAME).limit(1).count() > 0),
    ]
    checks += [
        ("grant", f"{p} ON {t} {n} -> account users", lambda t=t, n=n, p=p: _has_grant(t, n, p))
        for t, n, p in UC_GRANTS
    ]

    result = []
    for kind, name, fn in checks:
        try:
            ok, details = bool(fn()), ""
        except Exception as e:
            ok, details = False, f"{type(e).__name__}: {str(e)[:300]}"
        print(f"  [{'OK' if ok else 'MISSING'}] {name}" + (f" -> {details}" if details else ""))
        if not ok:
            result.append((kind, name, details or "not found"))
    return result

# COMMAND ----------

missing = find_missing()
kinds = {kind for kind, _, _ in missing}
print(f"\n{len(missing)} missing resource(s).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Repair Missing Resources
# MAGIC
# MAGIC Each repair runs only if its resource is missing. Steps run in dependency order: namespace, then table, then permissions. A failed repair is recorded and the remaining repairs still run.

# COMMAND ----------

from pyspark.sql.functions import concat, lit, col, regexp_replace, trim


def create_schema():
    spark.sql(f"USE CATALOG {SHARED_CATALOG}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_FULL_NAME}")
    print(f"  Schema present: {SCHEMA_FULL_NAME}")


def load_listings_table():
    """Same load as Workspace-Setup Section 2: first 50 listings from the Marketplace share."""
    df = (
        spark.read.format("csv")
        .option("header", "true")
        .option("inferSchema", "true")
        .option("multiLine", "true")
        .option("escape", '"')
        .load(SOURCE_CSV_PATH)
        .select("id", "name", "neighbourhood", "neighbourhood_cleansed", "summary", "price", "room_type")
        .withColumn("price", regexp_replace(trim(col("price")), "[$,]", "").cast("double"))
        .withColumn(
            "listing_source_information",
            concat(
                lit("ID of the property: "), col("id"),
                lit("\nName of the property: "), col("name"),
                lit("\nSummary of the property: "), col("summary"),
            ),
        )
        .limit(50)
    )
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLE_FULL_NAME)
    print(f"  Loaded {spark.table(TABLE_FULL_NAME).count()} rows into {TABLE_FULL_NAME}")


def apply_uc_grants():
    for sec_type, sec_name, priv in UC_GRANTS:
        spark.sql(f"GRANT {priv} ON {sec_type} {sec_name} TO `account users`")
        print(f"  Granted {priv} ON {sec_type} {sec_name}")

# COMMAND ----------

repair_errors = []


def _repair(label, fn):
    print(f"Repairing: {label}")
    try:
        fn()
    except Exception as e:
        print(f"  ERROR: {e}")
        repair_errors.append((label, f"{type(e).__name__}: {e}"))


if not missing:
    print("Nothing to repair.")
else:
    if "namespace" in kinds:
        _repair("schema", create_schema)
    if "namespace" in kinds or "table" in kinds:
        _repair("listings table", load_listings_table)
    if "namespace" in kinds or "table" in kinds or "grant" in kinds:
        _repair("UC grants", apply_uc_grants)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Verify and Alert
# MAGIC
# MAGIC Posts one message to the Slack channel (`#training-automation-alert`) only when something is broken: a repair failed or a resource is still missing after the repair step. Nothing is posted when all resources are present or every missing resource was repaired successfully.
# MAGIC
# MAGIC The alert is sent by `_lib/slack_alert.py`, which reads the Slack incoming webhook URL from the Databricks secret `lab-alerts` / `slack-webhook`. If the secret is missing or Slack is unreachable, the alert is skipped with a warning; it never changes the run result.
# MAGIC
# MAGIC Fails the job run if any repair failed or any resource is still missing.

# COMMAND ----------

import sys
sys.path.insert(0, os.path.abspath("."))
from _lib.slack_alert import send_slack_alert

COURSE_NAME     = "Building Agentic Applications on Databricks"
REPAIR_JOB_NAME = "Repair Resources"   # same as Workspace-Setup

still_missing = find_missing() if missing else []

print(f"\nRepaired {len(missing) - len(still_missing)} of {len(missing)} missing resource(s).")

if repair_errors or still_missing:
    send_slack_alert(
        ws, dbutils,
        job_name=REPAIR_JOB_NAME,
        summary=(f"{REPAIR_JOB_NAME} found *{len(missing)}* missing resource(s); "
                 f"*{len(still_missing)}* could not be recreated."),
        course=COURSE_NAME,
        sections=[
            ("Still missing", [f"{name}: {details}" for _, name, details in still_missing]),
            ("Repair errors", [f"{label}: {err}" for label, err in repair_errors]),
        ],
    )
    lines = [f"  - repair '{label}' failed: {err}" for label, err in repair_errors]
    lines += [f"  - still missing: {name}: {details}" for _, name, details in still_missing]
    raise RuntimeError("Repair-Resources failed:\n" + "\n".join(lines))

print("All shared resources present.")