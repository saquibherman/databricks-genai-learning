import json
import uuid
from typing import Dict, List, Optional
from databricks.sdk.runtime import dbutils

from ._rest import notebook_auth_headers, request_with_retry


def _hex_id() -> str:
    return uuid.uuid4().hex


def _get_warehouse_id(host: str, headers: dict, warehouse_name: str) -> str:
    """Return the warehouse ID matching warehouse_name, or raise if not found."""
    url = f"{host}/api/2.0/sql/warehouses"
    resp = request_with_retry("GET", url, headers=headers)
    warehouses = resp.json().get("warehouses", [])
    matches = [w for w in warehouses if w.get("name", "").lower() == warehouse_name.lower()]
    if not matches:
        raise ValueError(f"No warehouse found with name='{warehouse_name}'.")
    if len(matches) > 1:
        raise ValueError(f"Multiple warehouses matched '{warehouse_name}': {[w['id'] for w in matches]}")
    return matches[0]["id"]


def create_genie_space(
    source_table: Optional[str],
    warehouse_name: str,
    space_title: str,
    space_description: str,
    tables: Optional[List[str]] = None,
    sample_questions: Optional[List[str]] = None,
    text_instructions: Optional[List[str]] = None,
    example_sqls: Optional[List[Dict[str, str]]] = None,
    sql_functions: Optional[List[str]] = None,
) -> str:
    """
    Create a Databricks Genie space backed by one or more Delta tables.

    Two modes:
      - Single-table (original behavior): pass ``source_table`` and leave
        ``tables`` as None. Any of sample_questions / text_instructions /
        example_sqls left as None fall back to the built-in Airbnb defaults.
      - Multi-table: pass ``tables`` (fully-qualified names). Content comes only
        from the optional lists below; anything left as None is omitted.

    Parameters
    ----------
    source_table : str, optional
        Fully-qualified table name (catalog.schema.table). Used when ``tables``
        is not given.
    warehouse_name : str
        Display name of the SQL warehouse to use.
    space_title : str
        Title shown in the Genie UI.
    space_description : str
        Description shown in the Genie UI.
    tables : list[str], optional
        Fully-qualified table names to attach.
    sample_questions : list[str], optional
        Sample questions shown to users.
    text_instructions : list[str], optional
        Plain-text instructions for the space.
    example_sqls : list[dict], optional
        Example question/SQL pairs: [{"question": "...", "sql": "..."}].
    sql_functions : list[str], optional
        Fully-qualified UC SQL functions to attach as trusted assets.

    Returns
    -------
    str
        The created Genie space ID.
    """
    host, headers = notebook_auth_headers()

    user_name = dbutils.notebook.entry_point.getDbutils().notebook().getContext().userName().get()
    parent_path = f"/Workspace/Users/{user_name}"

    warehouse_id = _get_warehouse_id(host, headers, warehouse_name)

    if tables is None:
        if not source_table:
            raise ValueError("create_genie_space requires source_table or tables.")
        table_list = [source_table]
        # Original single-table defaults
        if sample_questions is None:
            sample_questions = ["What's the average price of all listings?"]
        if text_instructions is None:
            text_instructions = ["Round monetary values to 2 decimals."]
        if example_sqls is None:
            example_sqls = [{
                "question": "Total price by neighbourhood",
                "sql": f"SELECT AVG(price) as avg_price FROM {source_table}",
            }]
    else:
        table_list = list(tables)

    # Build the serialized space config (lists sorted by id/identifier, as the
    # serialized_space schema requires)
    sample_question_items = sorted(
        [{"id": _hex_id(), "question": [q]} for q in (sample_questions or [])],
        key=lambda q: q["id"],
    )
    table_items = sorted(
        [{"identifier": t} for t in table_list],
        key=lambda t: t["identifier"],
    )
    instructions = {}
    if text_instructions:
        instructions["text_instructions"] = [
            {"id": _hex_id(), "content": list(text_instructions)}
        ]
    if example_sqls:
        instructions["example_question_sqls"] = sorted(
            [
                {"id": _hex_id(), "question": [e["question"]], "sql": [e["sql"]]}
                for e in example_sqls
            ],
            key=lambda e: e["id"],
        )
    if sql_functions:
        instructions["sql_functions"] = sorted(
            [{"id": _hex_id(), "identifier": f} for f in sql_functions],
            key=lambda f: f["id"],
        )

    serialized_space = json.dumps({
        "version": 2,
        "config": {"sample_questions": sample_question_items},
        "data_sources": {"tables": table_items},
        "instructions": instructions,
    })

    # Create the space
    create_payload = {
        "title": space_title,
        "description": space_description,
        "parent_path": parent_path,
        "warehouse_id": warehouse_id,
        "serialized_space": serialized_space,
    }
    resp = request_with_retry(
        "POST", f"{host}/api/2.0/genie/spaces", headers=headers, json=create_payload
    )
    space = resp.json()
    space_id = space.get("space_id") or space.get("id")
    if not space_id:
        raise RuntimeError(f"Create response missing space_id: {space}")
    print(f"  Genie space created: {space_id}")

    return space_id
