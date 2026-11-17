# Databricks notebook source
import sys
from pathlib import Path

# Locate the course folder (the one containing Includes/_lib)
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