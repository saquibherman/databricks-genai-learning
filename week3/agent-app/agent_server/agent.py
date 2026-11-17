"""Airbnb assistant agent.

Serves the course's shared tool-calling agent: the model that Workspace-Setup
registered in Unity Catalog (for example workspace.agent_eval.airbnb@champion).
The app has no agent code of its own. It loads that registered model version,
so the app and the course notebooks always run the same agent. The agent:
  - calls a Unity Gateway model service for reasoning,
  - uses Unity Catalog SQL functions as tools, through the Databricks managed
    MCP server for their schema,
  - logs every request as an MLflow trace.

MLflow AgentServer exposes the two handlers below at /invocations.
"""

import asyncio
import contextvars
import logging
import os
from typing import AsyncGenerator

import mlflow
from mlflow import MlflowClient
from mlflow.entities import SpanType
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration (set as app env vars at deploy time; see app.yaml)
# ---------------------------------------------------------------------------
# Registered agent model in Unity Catalog, e.g. workspace.agent_eval.airbnb
AGENT_MODEL = os.environ["AGENT_MODEL"]
AGENT_ALIAS = os.environ.get("AGENT_ALIAS", "champion")
# Unity Gateway model service the agent calls (display only; the agent reads
# it from its own registered config)
MODEL_SERVICE = os.environ.get("MODEL_SERVICE")

# Email of the signed-in user for the current request, set by the middleware
# in start_server.py from the X-Forwarded-Email header that Databricks Apps adds
current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_user", default=None)

_agent = None
_agent_version: str | None = None


def load_agent():
    """Load the registered agent once, from Unity Catalog by name and alias."""
    global _agent, _agent_version
    if _agent is None:
        mlflow.set_registry_uri("databricks-uc")
        _agent = mlflow.pyfunc.load_model(f"models:/{AGENT_MODEL}@{AGENT_ALIAS}")
        try:
            _agent_version = MlflowClient().get_model_version_by_alias(AGENT_MODEL, AGENT_ALIAS).version
        except Exception:
            logger.warning("Could not read the version of %s@%s", AGENT_MODEL, AGENT_ALIAS, exc_info=True)
        logger.info("Loaded agent %s@%s (version %s)", AGENT_MODEL, AGENT_ALIAS, _agent_version)
    return _agent


def agent_version() -> str | None:
    return _agent_version


def get_session_id(request: ResponsesAgentRequest) -> str | None:
    """Return the conversation ID for the request, if the client sent one."""
    if request.context and request.context.conversation_id:
        return request.context.conversation_id
    if request.custom_inputs and isinstance(request.custom_inputs, dict):
        return request.custom_inputs.get("session_id")
    return None


def _run_agent(request: ResponsesAgentRequest, user: str | None) -> tuple[dict, str]:
    """Run the agent for one request inside a trace; return (output, trace_id).

    Runs in a worker thread (see the handlers below): the agent's MCP client
    starts its own event loop, which can't run inside the server's loop.
    """
    with mlflow.start_span(name="airbnb_assistant", span_type=SpanType.AGENT) as span:
        metadata = {}
        if user:
            metadata["mlflow.trace.user"] = user
        if session_id := get_session_id(request):
            metadata["mlflow.trace.session"] = session_id
        mlflow.update_current_trace(
            metadata=metadata,
            tags={"agent_model": f"{AGENT_MODEL}@{AGENT_ALIAS}", "agent_version": str(_agent_version or "")},
        )
        payload = request.model_dump(exclude_none=True)
        span.set_inputs(payload)
        result = load_agent().predict(payload)
        span.set_outputs(result)
        return result, span.trace_id


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    result, trace_id = await asyncio.to_thread(_run_agent, request, current_user.get())
    custom_outputs = dict(result.get("custom_outputs") or {})
    # The chat UI attaches user feedback to this trace (see /api/feedback)
    custom_outputs["trace_id"] = trace_id
    return ResponsesAgentResponse(output=result.get("output", []), custom_outputs=custom_outputs)


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    result, _ = await asyncio.to_thread(_run_agent, request, current_user.get())
    for item in result.get("output", []):
        yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


def log_resolved_config() -> None:
    """Log the deploy-time configuration once at startup (visible in app logs)."""
    logger.info(
        "Resolved app config: agent=%s@%s model_service=%s experiment_id=%s",
        AGENT_MODEL,
        AGENT_ALIAS,
        MODEL_SERVICE,
        os.environ.get("MLFLOW_EXPERIMENT_ID", "(none)"),
    )
