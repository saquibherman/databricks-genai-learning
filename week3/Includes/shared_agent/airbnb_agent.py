import asyncio
import json
import warnings
from typing import Any, Generator
from uuid import uuid4

import mlflow
from databricks.sdk import WorkspaceClient
from databricks_mcp import DatabricksMCPClient
from databricks_openai import DatabricksOpenAI
from mlflow.entities import SpanType
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    to_chat_completions_input,
)

try:
    model_config = mlflow.models.ModelConfig()
except Exception:
    config_file = "configs/airbnb_agent_config.yaml"
    model_config = mlflow.models.ModelConfig(development_config=config_file)

SYSTEM_PROMPT = model_config.get("SYSTEM_PROMPT")
# Unity Gateway model service the agent calls, e.g. workspace.agent_eval.agent-model
MODEL_SERVICE = model_config.get("MODEL_SERVICE")
CATALOG_NAME = model_config.get("CATALOG_NAME")
SCHEMA_NAME = model_config.get("SCHEMA_NAME")
TOOL1 = model_config.get("TOOL1")
TOOL2 = model_config.get("TOOL2")


###############################################################################
# Tools: Unity Catalog SQL functions, called through the Databricks managed
# MCP server for their schema
###############################################################################
def _allow_nested_event_loop() -> None:
    """The MCP client runs its own event loop. Inside a notebook a loop is
    already running, so let the client nest inside it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop (e.g. a worker thread in the deployed app)
    import nest_asyncio
    nest_asyncio.apply()


class UCFunctionTools:
    """The agent's UC function tools, listed and executed via managed MCP."""

    def __init__(self, catalog: str, schema: str, function_names: list[str]):
        self.catalog = catalog
        self.schema = schema
        self.function_names = {f for f in function_names if f}
        self._client = None
        self._specs = None

    def _mcp(self) -> DatabricksMCPClient:
        if self._client is None:
            w = WorkspaceClient()
            url = f"{w.config.host.rstrip('/')}/api/2.0/mcp/functions/{self.catalog}/{self.schema}"
            self._client = DatabricksMCPClient(server_url=url, workspace_client=w)
        return self._client

    def specs(self) -> list[dict]:
        """OpenAI tool specs for the configured functions (fetched once)."""
        if self._specs is None:
            _allow_nested_event_loop()
            self._specs = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                }
                # MCP tool names look like <catalog>__<schema>__<function>
                for tool in self._mcp().list_tools()
                if tool.name.split("__")[-1] in self.function_names
            ]
        return self._specs

    def call(self, name: str, args: dict) -> str:
        _allow_nested_event_loop()
        result = self._mcp().call_tool(name, args)
        return "".join(getattr(part, "text", "") for part in result.content)


###############################################################################
# Agent implementation
###############################################################################
class ToolCallingAgent(ResponsesAgent):
    """Agent that calls tools based on LLM responses"""

    def __init__(self, model_service: str, tools: UCFunctionTools):
        self.model_service = model_service
        self.tools = tools
        self._client = None

    @property
    def client(self) -> DatabricksOpenAI:
        # Route model calls through Unity Gateway, where permissions, rate
        # limits, and usage tracking apply
        if self._client is None:
            self._client = DatabricksOpenAI(use_ai_gateway=True)
        return self._client

    def _responses_tools(self) -> list[dict]:
        """Convert tool specs from chat completions format to Responses API format."""
        return [
            {
                "type": "function",
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get("parameters", {}),
            }
            for t in self.tools.specs()
        ]

    def call_llm(self, messages: list[dict[str, Any]]):
        """Call the LLM via the Responses API (supports reasoning models with tools)."""
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", r"PydanticSerializationUnexpectedValue", category=UserWarning)

        return self.client.responses.create(
            model=self.model_service,
            input=messages,
            tools=self._responses_tools(),
        )

    def execute_tool(self, tool_name: str, args: dict) -> Any:
        with mlflow.start_span(name=tool_name, span_type=SpanType.TOOL) as span:
            span.set_inputs(args)
            result = self.tools.call(tool_name, args)
            span.set_outputs(result)
            return result

    def call_and_run_tools(
        self,
        messages: list[dict[str, Any]],
        max_iter: int = 10,
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        for _ in range(max_iter):
            response = self.call_llm(messages)

            has_tool_calls = False
            for item in response.output:
                if item.type == "function_call":
                    has_tool_calls = True
                    args = json.loads(item.arguments)
                    result_obj = self.execute_tool(item.name, args)
                    result_text = result_obj if isinstance(result_obj, str) else json.dumps(result_obj, ensure_ascii=False)

                    # Append the call and its result for the next API round-trip
                    messages.append({
                        "type": "function_call",
                        "call_id": item.call_id,
                        "name": item.name,
                        "arguments": item.arguments,
                    })
                    messages.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": result_text,
                    })

                    tool_output = self.create_function_call_output_item(item.call_id, result_text)
                    yield ResponsesAgentStreamEvent(type="response.output_item.done", item=tool_output)

                elif item.type == "message":
                    text = "".join(
                        p.text for p in (item.content or [])
                        if hasattr(p, "text")
                    )
                    text_item = self.create_text_output_item(text, str(uuid4()))
                    yield ResponsesAgentStreamEvent(type="response.output_item.done", item=text_item)

            if not has_tool_calls:
                return

        yield ResponsesAgentStreamEvent(
            type="response.output_item.done",
            item=self.create_text_output_item("Max iterations reached. Stopping.", str(uuid4())),
        )

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        outputs = [
            event.item
            for event in self.predict_stream(request)
            if event.type == "response.output_item.done"
        ]
        return ResponsesAgentResponse(output=outputs, custom_outputs=request.custom_inputs)

    def predict_stream(self, request: ResponsesAgentRequest) -> Generator[ResponsesAgentStreamEvent, None, None]:
        messages = to_chat_completions_input([i.model_dump() for i in request.input])
        if SYSTEM_PROMPT:
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        yield from self.call_and_run_tools(messages=messages)

###############################################################################
# Initialize and register the agent with MLflow
###############################################################################

AGENT = ToolCallingAgent(
    model_service=MODEL_SERVICE,
    tools=UCFunctionTools(CATALOG_NAME, SCHEMA_NAME, [TOOL1, TOOL2]),
)
mlflow.models.set_model(AGENT)
