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

import filecmp
import os
import shutil
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam
from databricks.ai_search.client import AISearchClient

ws = WorkspaceClient()
vsc = AISearchClient()

# --- Same names as Workspace-Setup ---
SHARED_CATALOG = "dbacademy"
SHARED_SCHEMA  = "rag_all"
SHARED_VOLUME  = "vol_all"
SCHEMA_FULL_NAME = f"{SHARED_CATALOG}.{SHARED_SCHEMA}"
VOLUME_FULL_NAME = f"{SCHEMA_FULL_NAME}.{SHARED_VOLUME}"

VS_ENDPOINT_DEMO = "vs_all_demo"
endpoint_list    = [VS_ENDPOINT_DEMO]

VOLUME_PATH          = f"/Volumes/{SHARED_CATALOG}/{SHARED_SCHEMA}/{SHARED_VOLUME}"
LISTINGS_SUBFOLDER   = f"{VOLUME_PATH}/listings"
ADDITIONAL_SUBFOLDER = f"{VOLUME_PATH}/additional_listing_info"
INVOICES_SUBFOLDER   = f"{VOLUME_PATH}/invoices"
LISTINGS_PDF_PATH    = f"{LISTINGS_SUBFOLDER}/airbnb_listings.pdf"
HOUSE_RULES_PDF_PATH = f"{ADDITIONAL_SUBFOLDER}/airbnb_house_rules.pdf"

TABLE_NAME = "sf_airbnb_listings"

UC_GRANTS = [   # (securable type, securable name, privilege) granted to `account users`
    ("CATALOG", SHARED_CATALOG, "USE CATALOG"),
    ("SCHEMA", SCHEMA_FULL_NAME, "USE SCHEMA"),
    ("VOLUME", VOLUME_FULL_NAME, "READ VOLUME"),
    ("CATALOG", "system", "USE CATALOG"),
    ("SCHEMA", "system.ai", "USE SCHEMA"),
]

data_dir = os.path.join(os.getcwd(), "data")
invoices_src_dir = os.path.join(data_dir, "invoices")

# (source, destination) for every PDF in the volume
expected_files = [
    (os.path.join(data_dir, "airbnb_listings.pdf"), LISTINGS_PDF_PATH),
    (os.path.join(data_dir, "airbnb_house_rules.pdf"), HOUSE_RULES_PDF_PATH),
]
if os.path.isdir(invoices_src_dir):
    expected_files += [
        (os.path.join(invoices_src_dir, f), f"{INVOICES_SUBFOLDER}/{f}")
        for f in sorted(os.listdir(invoices_src_dir)) if f.endswith(".pdf")
    ]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Check Resources

# COMMAND ----------

def _has_grant(sec_type, sec_name, privilege):
    rows = spark.sql(f"SHOW GRANTS ON {sec_type} {sec_name}").collect()
    return any(r["Principal"] == "account users" and r["ActionType"] == privilege for r in rows)


def _endpoint_online(name):
    return vsc.get_endpoint(name).get("endpoint_status", {}).get("state", "") == "ONLINE"


def _endpoint_can_use(name):
    perms = ws.permissions.get("vector-search-endpoints", str(vsc.get_endpoint(name)["id"]))
    allowed = {iam.PermissionLevel.CAN_USE, iam.PermissionLevel.CAN_MANAGE}
    return any(
        acl.group_name == "users" and any(p.permission_level in allowed for p in (acl.all_permissions or []))
        for acl in (perms.access_control_list or [])
    )


def find_missing():
    """Check every resource. Returns [(kind, name, details)] for anything missing.

    kind is one of: namespace, file, endpoint, endpoint_permission, grant.
    """
    checks = [
        ("namespace", f"Catalog {SHARED_CATALOG}", lambda: ws.catalogs.get(SHARED_CATALOG)),
        ("namespace", f"Schema {SCHEMA_FULL_NAME}", lambda: ws.schemas.get(SCHEMA_FULL_NAME)),
        ("namespace", f"Volume {VOLUME_FULL_NAME}", lambda: ws.volumes.read(VOLUME_FULL_NAME)),
    ]
    checks += [("file", dest, lambda d=dest: os.path.isfile(d)) for _, dest in expected_files]
    for ep in endpoint_list:
        checks.append(("endpoint", f"Endpoint {ep} ONLINE", lambda e=ep: _endpoint_online(e)))
        checks.append(("endpoint_permission", f"Endpoint {ep} CAN_USE -> users", lambda e=ep: _endpoint_can_use(e)))
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
# MAGIC Each repair runs only if its resource is missing. Steps run in dependency order: namespace, then files, then endpoint, then permissions. A failed repair is recorded and the remaining repairs still run.

# COMMAND ----------

def create_schema_and_volume():
    spark.sql(f"USE CATALOG {SHARED_CATALOG}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_FULL_NAME}")
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {VOLUME_FULL_NAME}")
    print(f"  Schema and volume present: {VOLUME_PATH}")


def copy_pdfs():
    """Plain Python copy: on serverless, dbutils.fs.cp can't read file:/Workspace/... paths."""
    for src, dest in expected_files:
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Source PDF not found: {src}")
        if os.path.exists(dest) and filecmp.cmp(src, dest, shallow=False):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        print(f"  Copied: {os.path.basename(src)} -> {dest}")


def ensure_vs_endpoint(name, timeout_s=20 * 60):
    try:
        vsc.get_endpoint(name)
    except Exception:
        print(f"  Creating endpoint '{name}'...")
        vsc.create_endpoint(name=name, endpoint_type="STANDARD")
    deadline = time.time() + timeout_s
    while not _endpoint_online(name):
        if time.time() > deadline:
            raise TimeoutError(f"Endpoint '{name}' not ONLINE after {timeout_s // 60} min")
        print(f"  {name}: waiting for ONLINE...")
        time.sleep(15)
    print(f"  Endpoint '{name}' is ONLINE.")


def grant_endpoint_can_use(name):
    ws.permissions.update(   # PATCH: adds the grant without removing other permissions
        request_object_type="vector-search-endpoints",
        request_object_id=str(vsc.get_endpoint(name)["id"]),
        access_control_list=[iam.AccessControlRequest(group_name="users", permission_level=iam.PermissionLevel.CAN_USE)],
    )
    print(f"  Granted CAN_USE on endpoint '{name}' to users.")


def apply_uc_grants():
    for sec_type, sec_name, priv in UC_GRANTS:
        spark.sql(f"GRANT {priv} ON {sec_type} {sec_name} TO `account users`")
        print(f"  Granted {priv} ON {sec_type} {sec_name}")
    try:   # Marketplace table is optional: never fail on it
        spark.sql(f"GRANT SELECT ON TABLE {SCHEMA_FULL_NAME}.{TABLE_NAME} TO `account users`")
    except Exception as e:
        print(f"  Skipped GRANT on {TABLE_NAME}: {e}")

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
        _repair("schema / volume", create_schema_and_volume)
    if "namespace" in kinds or "file" in kinds:
        _repair("PDFs", copy_pdfs)
    if "endpoint" in kinds:
        for ep in endpoint_list:
            _repair(f"endpoint {ep}", lambda ep=ep: ensure_vs_endpoint(ep))
    if "endpoint" in kinds or "endpoint_permission" in kinds:
        for ep in endpoint_list:
            _repair(f"CAN_USE on {ep}", lambda ep=ep: grant_endpoint_can_use(ep))
    if "namespace" in kinds or "grant" in kinds:
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

COURSE_NAME = "Building RAG Agents with Agent Bricks"

still_missing = find_missing() if missing else []

print(f"\nRepaired {len(missing) - len(still_missing)} of {len(missing)} missing resource(s).")

if repair_errors or still_missing:
    send_slack_alert(
        ws, dbutils,
        title=f"Repair Resources FAILED: {len(still_missing)} of {len(missing)} missing resource(s) not recreated",
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