import logging
import os
import sys
from pathlib import Path

import mlflow
from dotenv import load_dotenv
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from mlflow.entities import AssessmentSource, AssessmentSourceType
from mlflow.genai.agent_server import AgentServer, setup_mlflow_git_based_version_tracking
from pydantic import BaseModel

# Load env vars from .env (local development only) before importing the agent
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

# Send the agent_server package's INFO logs to stdout so they show in the app logs
_logger = logging.getLogger("agent_server")
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [agent_server] %(levelname)s %(message)s"))
    _logger.addHandler(_handler)
_logger.setLevel(logging.INFO)
_logger.propagate = False

# Importing the agent registers its @invoke / @stream handlers with the server
import agent_server.agent  # noqa: E402
from agent_server.agent import (  # noqa: E402
    AGENT_ALIAS,
    AGENT_MODEL,
    MODEL_SERVICE,
    agent_version,
    current_user,
    load_agent,
    log_resolved_config,
)

log_resolved_config()
# Load the registered agent at startup, so a missing model or permission shows
# up in the app logs right away instead of on the first chat
load_agent()

agent_server = AgentServer("ResponsesAgent")
# Module-level app so the server can run multiple workers
app = agent_server.app
setup_mlflow_git_based_version_tracking()


@app.middleware("http")
async def remember_user(request: Request, call_next):
    """Make the signed-in user's email available to the agent handlers, so
    each trace records who sent the request."""
    token = current_user.set(request.headers.get("X-Forwarded-Email"))
    try:
        return await call_next(request)
    finally:
        current_user.reset(token)


@app.get("/api/app-info")
def app_info():
    """Agent model and version, model service, and the MLflow experiment URL, for the UI header."""
    from databricks.sdk import WorkspaceClient

    exp_id = os.environ.get("MLFLOW_EXPERIMENT_ID")
    experiment_url = None
    if exp_id:
        host = WorkspaceClient().config.host.rstrip("/")
        experiment_url = f"{host}/ml/experiments/{exp_id}"
    return {
        "agentModel": f"{AGENT_MODEL}@{AGENT_ALIAS}",
        "agentVersion": agent_version(),
        "modelService": MODEL_SERVICE,
        "experimentUrl": experiment_url,
    }


class Feedback(BaseModel):
    trace_id: str
    value: bool
    comment: str | None = None


@app.post("/api/feedback")
def feedback(fb: Feedback, request: Request):
    """Attach the user's thumbs up/down (and optional comment) to a trace as
    human feedback. The app's service principal writes it, so users don't need
    edit access to the experiment; the user's email is recorded as the source."""
    user = request.headers.get("X-Forwarded-Email") or "unknown user"
    mlflow.log_feedback(
        trace_id=fb.trace_id,
        name="user_feedback",
        value=fb.value,
        rationale=fb.comment or None,
        source=AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id=user),
    )
    return {"ok": True}


# Serve the chat UI at /. Mounted after the agent server's routes (/invocations,
# /health) so those match first.
_static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


def main():
    agent_server.run(app_import_string="agent_server.start_server:app")
