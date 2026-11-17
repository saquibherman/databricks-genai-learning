# Databricks notebook source
import sys
from pathlib import Path

# Locate the course folder (the one containing Includes/_lib), whether the
# calling lesson is in the course root or in a module subfolder.
course_dir = next(
    (p for p in [Path.cwd(), *Path.cwd().parents] if (p / "Includes" / "_lib").is_dir()),
    None,
)
if course_dir is None:
    raise FileNotFoundError("Could not find the course Includes/_lib folder above " + str(Path.cwd()))
sys.path.insert(0, str(course_dir))
config_path = str(course_dir / "Includes" / "config" / "config-02.yaml")

from Includes._lib.library_installer import install_libraries
install_libraries(config_path)

# COMMAND ----------

# Restart Python only on Databricks compute; via Databricks Connect (local VS Code)
# dbutils.library is unavailable and packages already come from the local .venv.
import os
if "DATABRICKS_RUNTIME_VERSION" in os.environ:
    dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

course_dir = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "Includes" / "_lib").is_dir())
sys.path.insert(0, str(course_dir))
config_path = str(course_dir / "Includes" / "config" / "config-02.yaml")

# COMMAND ----------

from Includes import setup_demo_environment

env = setup_demo_environment(
    config_path=config_path,
)
catalog_name = env["catalog_name"]
schema_name  = env["schema_name"]
username     = env["username"]
experiment_path = env["mlflow_experiment_path"]   # /Workspace/Users/<user>/airbnb_experiment

# Shared course resources created by Workspace-Setup
shared_schema = "workspace.agent_eval"

# COMMAND ----------

import mlflow

mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(experiment_path)

# The shared agent, registered in Unity Catalog by Workspace-Setup
alias = "champion"
agent_name = "airbnb"
UC_MODEL_NAME = f"{shared_schema}.{agent_name}"
agent = mlflow.pyfunc.load_model(f"models:/{UC_MODEL_NAME}@{alias}")

print(f"Your username is: {username}")
print(f"The location of your experiment: {experiment_path}")
print(f"Agent: {UC_MODEL_NAME}@{alias}")

# COMMAND ----------

# Judge models (Unity Gateway model services) from the config's judges: section
from Includes._lib.config_loader import load_config

judges_cfg = load_config(config_path).get("judges") or {}
correctness_eval_endpoint = judges_cfg.get("correctness")
safety_eval_endpoint = judges_cfg.get("safety")
guidelines_endpoint = judges_cfg.get("guidelines")
custom_eval_endpoint = judges_cfg.get("custom")
coherent_instructions = judges_cfg.get("coherent_instructions")
tool_usage_instructions = judges_cfg.get("tool_usage_instructions")

print(f"Correctness Endpoint: {correctness_eval_endpoint}")
print(f"Safety Endpoint: {safety_eval_endpoint}")
print(f"Guidelines Endpoint: {guidelines_endpoint}")
print(f"Custom Judge Endpoint: {custom_eval_endpoint}")

# COMMAND ----------

# Used by the trace-based custom judge in 2.6
session_id = "custom-eval-001"
experiment_id = mlflow.get_experiment_by_name(experiment_path).experiment_id