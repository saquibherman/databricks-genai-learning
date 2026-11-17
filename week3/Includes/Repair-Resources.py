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
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath('.'))
config_path = "./config/config-workspace-setup.yaml"

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam
from databricks.sdk.service.apps import ApplicationState, ComputeState

from _lib.config_loader import load_config
from _lib.model_service_config import _get_service

ws = WorkspaceClient()
admin_user = ws.current_user.me().user_name

# --- Same names as Workspace-Setup ---
SHARED_CATALOG = "workspace"  # this repo: course default "dbacademy" (Academy lab catalog)
SHARED_SCHEMA  = "agent_eval"
SHARED         = f"{SHARED_CATALOG}.{SHARED_SCHEMA}"

LISTINGS_TABLE = f"{SHARED}.sf_airbnb_listings"
AIRBNB_SHARE   = "databricks_airbnb_sample_data"  # Marketplace share name outside Academy labs   # Vocareum: dbacademy_ + manifest dataset name

setup_cfg = load_config(config_path)
MODEL_SERVICES = setup_cfg.get("model_services") or {}
AGENT_MODEL_SERVICE = f"{SHARED}.agent-model"

SHARED_TOOLS = ["avg_neigh_price", "cnt_by_room_type"]

AGENT_DIR               = Path(os.path.abspath("./shared_agent"))
AGENT_UC_MODEL          = f"{SHARED}.airbnb"
AGENT_ALIAS             = "champion"
REGISTRATION_EXPERIMENT = "/Shared/agent-eval-agent-registration"

APP_NAME       = "agent-airbnb-assistant"
APP_EXPERIMENT = "/Shared/agent-eval-app"

LEARNER_GROUP = "users"

UC_GRANTS = [   # (securable type, securable name, privilege) granted to `account users`
    ("CATALOG", SHARED_CATALOG, "USE CATALOG"),
    ("SCHEMA", SHARED, "USE SCHEMA"),
    ("TABLE", LISTINGS_TABLE, "SELECT"),
    ("SCHEMA", SHARED, "EXECUTE"),
]

notebook_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
course_dir = os.path.dirname(os.path.dirname(notebook_path))  # .../<course>/Includes/Repair-Resources -> .../<course>
agent_app_path = f"{course_dir}/agent-app"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Check Resources

# COMMAND ----------

def _has_grant(sec_type, sec_name, privilege):
    rows = spark.sql(f"SHOW GRANTS ON {sec_type} {sec_name}").collect()
    return any(r["Principal"] == "account users" and r["ActionType"] == privilege for r in rows)


def _app_running():
    status = ws.apps.get(APP_NAME).app_status
    return bool(status and status.state == ApplicationState.RUNNING)


def _app_can_use():
    perms = ws.permissions.get(request_object_type="apps", request_object_id=APP_NAME)
    allowed = {iam.PermissionLevel.CAN_USE, iam.PermissionLevel.CAN_MANAGE}
    return any(
        acl.group_name == LEARNER_GROUP and any(p.permission_level in allowed for p in (acl.all_permissions or []))
        for acl in (perms.access_control_list or [])
    )


def find_missing():
    """Check every resource. Returns [(kind, name, details)] for anything missing.

    kind is one of: namespace, table, model_service:<name>, tool:<name>, agent,
    grant, app, app_permission.
    """
    checks = [
        ("namespace", f"Catalog {SHARED_CATALOG}", lambda: ws.catalogs.get(SHARED_CATALOG)),
        ("namespace", f"Schema {SHARED}", lambda: ws.schemas.get(SHARED)),
        ("table", f"Table {LISTINGS_TABLE} (has rows)", lambda: spark.table(LISTINGS_TABLE).limit(1).count() > 0),
    ]
    checks += [
        (f"model_service:{n}", f"Model service {SHARED}.{n}", lambda n=n: _get_service(ws, f"{SHARED}.{n}"))
        for n in MODEL_SERVICES
    ]
    checks += [
        (f"tool:{t}", f"Function {SHARED}.{t}", lambda t=t: ws.functions.get(f"{SHARED}.{t}"))
        for t in SHARED_TOOLS
    ]
    checks.append(("agent", f"Agent {AGENT_UC_MODEL}@{AGENT_ALIAS}",
                   lambda: ws.model_versions.get_by_alias(AGENT_UC_MODEL, AGENT_ALIAS)))
    checks += [
        ("grant", f"{p} ON {t} {n} -> account users", lambda t=t, n=n, p=p: _has_grant(t, n, p))
        for t, n, p in UC_GRANTS
    ]
    checks.append(("app", f"App {APP_NAME} RUNNING", _app_running))
    checks.append(("app_permission", f"App {APP_NAME} CAN_USE -> {LEARNER_GROUP}", _app_can_use))

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
# MAGIC Each repair runs only if its resource is missing. Steps run in dependency order: namespace, table, model services, tools, agent, grants, then the app. A failed repair is recorded and the remaining repairs still run.
# MAGIC
# MAGIC A model service is only created when it is missing; an existing service is never deleted or recreated here. The agent is only re-registered when the `champion` alias is missing, and the app is redeployed when it is missing, not running, or the agent was re-registered.

# COMMAND ----------

def create_schema():
    # This repo: only create the catalog if it is missing. CREATE CATALOG IF NOT EXISTS fails on
    # metastores without a storage root even when the catalog already exists.
    if SHARED_CATALOG not in [r[0] for r in spark.sql("SHOW CATALOGS").collect()]:
        spark.sql(f"CREATE CATALOG IF NOT EXISTS {SHARED_CATALOG}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SHARED}")
    print(f"  Schema present: {SHARED}")


def load_listings():
    """Same load as Workspace-Setup Section 2 (overwrites the table)."""
    from _lib.process_data import process_csv_data
    try:
        process_csv_data(AIRBNB_SHARE, LISTINGS_TABLE)
        print(f"  Created {LISTINGS_TABLE} from /Volumes/{AIRBNB_SHARE}")
    except Exception:
        process_csv_data("databricks_airbnb_sample_data", LISTINGS_TABLE)
        print(f"  Created {LISTINGS_TABLE} from /Volumes/databricks_airbnb_sample_data")


def create_model_service(service_name):
    """Create one missing model service. recreate_on_drift=False: never deletes an existing service."""
    from _lib.model_service_config import setup_model_service
    service = MODEL_SERVICES[service_name]
    served_by = service["served_by"]
    setup_model_service(
        catalog_name=SHARED_CATALOG,
        schema_name=SHARED_SCHEMA,
        service_name=service_name,
        served_by=served_by,
        comment=f"{service.get('comment', 'Routes to ' + served_by)} (GenAI Agent Evaluation on Databricks)",
        recreate_on_drift=False,
    )


def create_tools(tools):
    from _lib.tool_manager import ToolManager
    spark.sql(f"USE CATALOG {SHARED_CATALOG}")
    spark.sql(f"USE SCHEMA {SHARED_SCHEMA}")
    tools_dir = os.path.abspath("./tools/sql")
    ToolManager(catalog_name=SHARED_CATALOG, schema_name=SHARED_SCHEMA).create_tools(
        {(tool, tools_dir) for tool in tools}
    )


def register_agent():
    """Same registration as Workspace-Setup Section 4."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.models.resources import DatabricksFunction
    from _lib.config_renderer import ConfigRenderer

    rendered_config = Path(os.path.abspath("./artifacts/shared_agent/airbnb_agent_config.yaml"))
    renderer = ConfigRenderer(artifacts_dir=rendered_config.parent)
    renderer.update_yaml_config(
        renderer.render_text_template(
            template_path=AGENT_DIR / "airbnb_agent_config.yaml",
            substitutions={
                "LLM_ENDPOINT_NAME": AGENT_MODEL_SERVICE,
                "CATALOG_NAME": SHARED_CATALOG,
                "SCHEMA_NAME": SHARED_SCHEMA,
                "TOOL1": SHARED_TOOLS[0],
                "TOOL2": SHARED_TOOLS[1],
            },
        ),
        rendered_config,
    )

    pip_requirements = [
        line.strip()
        for line in (AGENT_DIR / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(REGISTRATION_EXPERIMENT)

    with mlflow.start_run(run_name="register-airbnb-agent", tags={
        "training_type": "db_academytraining",
        "model": AGENT_MODEL_SERVICE,
        "agent_type": "TOOL-CALLING",
        "agent_id": "airbnb",
    }):
        logged = mlflow.pyfunc.log_model(
            name="airbnb",
            python_model=str(AGENT_DIR / "airbnb_agent.py"),
            model_config=str(rendered_config),
            input_example={"input": [{"role": "user", "content": "What is the average listing price in Mission?"}]},
            pip_requirements=pip_requirements,
            resources=[DatabricksFunction(function_name=f"{SHARED}.{tool}") for tool in SHARED_TOOLS],
            registered_model_name=AGENT_UC_MODEL,
        )

    version = logged.registered_model_version
    MlflowClient().set_registered_model_alias(AGENT_UC_MODEL, AGENT_ALIAS, version)
    print(f"  Registered {AGENT_UC_MODEL} version {version} with alias '{AGENT_ALIAS}'")


def apply_uc_grants():
    for sec_type, sec_name, priv in UC_GRANTS:
        spark.sql(f"GRANT {priv} ON {sec_type} {sec_name} TO `account users`")
        print(f"  Granted {priv} ON {sec_type} {sec_name}")


def set_app_permissions():
    """Same as Workspace-Setup Section 6: admin manages, learners can only use (replaces the ACL)."""
    ws.permissions.set(
        request_object_type="apps",
        request_object_id=APP_NAME,
        access_control_list=[
            iam.AccessControlRequest(user_name=admin_user, permission_level=iam.PermissionLevel.CAN_MANAGE),
            iam.AccessControlRequest(group_name=LEARNER_GROUP, permission_level=iam.PermissionLevel.CAN_USE),
        ],
    )
    print(f"  Set app '{APP_NAME}' permissions: {admin_user} CAN_MANAGE, {LEARNER_GROUP} CAN_USE")


def deploy_app():
    """Same deploy as Workspace-Setup Section 6. Starts stopped app compute first."""
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.workspace import ObjectType
    from _lib.app_deployer import AppDeployer
    from _lib.create_files_folders import create_file

    try:
        compute = ws.apps.get(APP_NAME).compute_status
        if compute and compute.state == ComputeState.STOPPED:
            print(f"  Starting stopped app compute for '{APP_NAME}'...")
            ws.apps.start_and_wait(APP_NAME)
    except NotFound:
        pass   # app does not exist: AppDeployer creates it

    # Importing the course can turn agent_server/__init__.py into a notebook; replace it with a real file.
    init_notebook = f"{agent_app_path}/agent_server/__init__"
    try:
        if ws.workspace.get_status(init_notebook).object_type == ObjectType.NOTEBOOK:
            ws.workspace.delete(init_notebook)
            print(f"  Removed notebook {init_notebook}")
    except NotFound:
        pass
    fs_app_path = agent_app_path if agent_app_path.startswith("/Workspace") else f"/Workspace{agent_app_path}"
    create_file(f"{fs_app_path}/agent_server/__init__.py", '"""Airbnb assistant app package."""\n')

    result = AppDeployer(
        catalog_name=SHARED_CATALOG,
        schema_name=SHARED_SCHEMA,
        agent_app_path=agent_app_path,
        username=admin_user,
        workspace_client=ws,
        app_name=APP_NAME,
        experiment_path=APP_EXPERIMENT,
        app_description="Airbnb assistant for GenAI Agent Evaluation on Databricks",
        grant_model_service_execute=True,
        extra_grants=[("TABLE", LISTINGS_TABLE, "SELECT")],
        share_with_groups=[LEARNER_GROUP],
        extra_env_vars={
            "AGENT_MODEL": AGENT_UC_MODEL,
            "AGENT_ALIAS": AGENT_ALIAS,
            "MODEL_SERVICE": AGENT_MODEL_SERVICE,
        },
    ).deploy(smoke_test=False)
    print(f"  App '{result['app_name']}' status: {result['app_status']}")
    set_app_permissions()

# COMMAND ----------

repair_errors = []


def _repair(label, fn):
    print(f"Repairing: {label}")
    try:
        fn()
    except Exception as e:
        print(f"  ERROR: {e}")
        repair_errors.append((label, f"{type(e).__name__}: {e}"))


missing_services = [k.split(":", 1)[1] for k in kinds if k.startswith("model_service:")]
missing_tools    = [k.split(":", 1)[1] for k in kinds if k.startswith("tool:")]

if not missing:
    print("Nothing to repair.")
else:
    if "namespace" in kinds:
        _repair("schema", create_schema)
    if "namespace" in kinds or "table" in kinds:
        _repair("listings table", load_listings)
    for service_name in sorted(missing_services):
        _repair(f"model service {service_name}", lambda n=service_name: create_model_service(n))
    if missing_tools:
        _repair(f"tools {', '.join(sorted(missing_tools))}", lambda: create_tools(missing_tools))
    if "agent" in kinds:
        _repair("shared agent", register_agent)
    if kinds & {"namespace", "table", "grant"} or missing_services or missing_tools:
        _repair("UC grants", apply_uc_grants)
    if "app" in kinds or "agent" in kinds:
        _repair("agent app", deploy_app)          # also sets the app permissions
    elif "app_permission" in kinds:
        _repair("app permissions", set_app_permissions)

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

from _lib.slack_alert import send_slack_alert

COURSE_NAME     = "GenAI Agent Evaluation on Databricks"
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