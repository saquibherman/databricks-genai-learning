# _lib/monitoring_judges.py
#
# Config-driven production monitoring: register and start built-in MLflow
# judges on the course experiment, so a lab can begin with judges already
# running. When traces are stored in Unity Catalog, the monitoring job reads
# them through a SQL warehouse saved on the experiment, so that is set first.
#
# Config shape (under `monitoring:`):
#   judges:
#     - judge: ConversationCompleteness   # built-in class in mlflow.genai.scorers
#       name: conversation_completeness   # optional; defaults to snake_case of judge
#       sample_rate: 0.5                  # 0.0-1.0; 0 registers without starting
#       filter_string: null               # optional search_traces filter
#       model: null                       # optional; defaults to top-level judge_model
#       args: {}                          # optional constructor args, e.g. guidelines

import re
from typing import Any, Dict, List, Optional

import mlflow

# Judges that compare against expectations; production traces usually have none
_GROUND_TRUTH_JUDGES = {"Correctness", "Equivalence", "ExpectationsGuidelines", "RetrievalSufficiency"}


def _default_name(judge: str) -> str:
    """ConversationCompleteness -> conversation_completeness"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", judge).lower()


def _builtin_judges() -> Dict[str, type]:
    """Built-in scorer classes exported by mlflow.genai.scorers, keyed by name."""
    from mlflow.genai import scorers
    from mlflow.genai.scorers.builtin_scorers import BuiltInScorer

    found = {}
    for name in getattr(scorers, "__all__", dir(scorers)):
        obj = getattr(scorers, name, None)
        if isinstance(obj, type) and issubclass(obj, BuiltInScorer) and obj is not BuiltInScorer:
            found[name] = obj
    return found


def _resolve_specs(judges: List[Dict[str, Any]], default_model: Optional[str]) -> List[Dict[str, Any]]:
    """Validate every configured judge before any is registered."""
    available = _builtin_judges()
    specs, errors, seen = [], [], set()

    for i, entry in enumerate(judges or [], 1):
        entry = entry or {}
        judge = entry.get("judge")
        if judge not in available:
            errors.append(
                f"judges[{i}]: unknown judge '{judge}'. Available: {', '.join(sorted(available))}"
            )
            continue

        name = entry.get("name") or _default_name(judge)
        if name in seen:
            errors.append(f"judges[{i}]: name '{name}' is used more than once")
        seen.add(name)

        rate = entry.get("sample_rate")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not 0 <= rate <= 1:
            errors.append(f"judges[{i}] ({name}): sample_rate must be a number from 0 to 1, got {rate!r}")

        cls = available[judge]
        kwargs = dict(entry.get("args") or {})
        model = entry.get("model") or default_model
        if model and "model" in cls.model_fields:
            kwargs.setdefault("model", model)

        missing = [f for f, info in cls.model_fields.items() if info.is_required() and f not in kwargs]
        if missing:
            errors.append(f"judges[{i}] ({name}): {judge} needs args: {', '.join(missing)}")

        specs.append({
            "cls": cls,
            "judge": judge,
            "name": name,
            "sample_rate": rate,
            "filter_string": entry.get("filter_string"),
            "kwargs": kwargs,
        })

    if errors:
        raise ValueError("monitoring.judges config is invalid:\n  " + "\n  ".join(errors))
    return specs


def setup_monitoring_judges(
    judges: List[Dict[str, Any]],
    experiment_path: str,
    default_model: Optional[str] = None,
    sql_warehouse_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Register and start the configured built-in judges on the experiment.

    Registration is an upsert, so rerunning setup updates existing judges
    instead of failing. Returns [{"name", "judge", "sample_rate"}] per judge.
    """
    from mlflow.genai.scorers import ScorerSamplingConfig

    specs = _resolve_specs(judges, default_model)
    mlflow.set_experiment(experiment_path)

    if sql_warehouse_id:
        # The monitoring job runs outside this process and reads the warehouse
        # from the experiment tag, not from MLFLOW_TRACING_SQL_WAREHOUSE_ID.
        from mlflow.tracing import set_databricks_monitoring_sql_warehouse_id
        set_databricks_monitoring_sql_warehouse_id(sql_warehouse_id=sql_warehouse_id)
        print(f"  Monitoring SQL warehouse: {sql_warehouse_id}")

    started = []
    for spec in specs:
        if spec["judge"] in _GROUND_TRUTH_JUDGES:
            print(f"  NOTE: {spec['judge']} compares against expectations, which production traces usually lack")
        scorer = spec["cls"](**spec["kwargs"]).register(name=spec["name"])
        if spec["sample_rate"] > 0:
            scorer = scorer.start(
                sampling_config=ScorerSamplingConfig(
                    sample_rate=spec["sample_rate"],
                    filter_string=spec["filter_string"],
                ),
            )
            print(f"  {spec['name']}: {spec['judge']} running at sample_rate={spec['sample_rate']}")
        else:
            print(f"  {spec['name']}: {spec['judge']} registered (not started)")
        started.append({"name": spec["name"], "judge": spec["judge"], "sample_rate": spec["sample_rate"]})
    return started
