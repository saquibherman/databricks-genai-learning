# _lib/sql_warehouse.py
#
# Find the SQL warehouse a course uses and, optionally, point MLflow at it.
# MLflow needs a warehouse to write and search traces stored in Unity Catalog
# (MLFLOW_TRACING_SQL_WAREHOUSE_ID); Genie spaces and SQL tools need one too.

import os
from typing import Dict, Optional

from databricks.sdk import WorkspaceClient

TRACING_ENV_VAR = "MLFLOW_TRACING_SQL_WAREHOUSE_ID"


def get_sql_warehouse(name: Optional[str] = None, w: Optional[WorkspaceClient] = None) -> Dict[str, str]:
    """Return {"id", "name"} for the SQL warehouse to use.

    With a name, match it (case-insensitive) among the warehouses the caller can
    see. Without one, prefer a running serverless warehouse, then any serverless
    warehouse, then any warehouse.
    """
    w = w or WorkspaceClient()
    warehouses = list(w.warehouses.list())
    if not warehouses:
        raise ValueError("No SQL warehouses are visible to the current user.")

    if name:
        matches = [wh for wh in warehouses if (wh.name or "").lower() == name.lower()]
        if not matches:
            visible = ", ".join(sorted(wh.name for wh in warehouses if wh.name))
            raise ValueError(f"No SQL warehouse named '{name}'. Visible warehouses: {visible}")
        if len(matches) > 1:
            raise ValueError(f"Multiple SQL warehouses named '{name}': {[wh.id for wh in matches]}")
        chosen = matches[0]
    else:
        def rank(wh):
            running = getattr(wh.state, "value", wh.state) == "RUNNING"
            return (not wh.enable_serverless_compute, not running)
        chosen = sorted(warehouses, key=rank)[0]

    return {"id": chosen.id, "name": chosen.name}


def setup_sql_warehouse(name: Optional[str] = None, set_tracing_env: bool = False) -> Dict[str, str]:
    """Resolve the warehouse and, when set_tracing_env is True, export its ID as
    MLFLOW_TRACING_SQL_WAREHOUSE_ID for traces stored in Unity Catalog."""
    warehouse = get_sql_warehouse(name)
    print(f"  SQL warehouse : {warehouse['name']} ({warehouse['id']})")
    if set_tracing_env:
        os.environ[TRACING_ENV_VAR] = warehouse["id"]
        print(f"  Set {TRACING_ENV_VAR} for Unity Catalog trace storage")
    return warehouse
