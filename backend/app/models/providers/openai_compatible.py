"""OpenAI Responses and OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from ..adapter import ModelAdapter
from ..config import ProviderConfig
from ..errors import ModelAdapterError
from ..types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)


class OpenAICompatibleAdapter(ModelAdapter):
    """Serve OpenAI, Qwen, DeepSeek, and compatible custom endpoints."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: Any | None = None,
    ) -> None:
        if config.api_style is ApiStyle.ANTHROPIC_MESSAGES:
            raise ValueError("Anthropic Messages requires AnthropicAdapter")
        super().__init__(config)

        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key_value(),
            "timeout": config.timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self._client = client or AsyncOpenAI(**client_kwargs)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            if self.config.api_style is ApiStyle.RESPONSES:
                return await self._complete_responses(request)
            return await self._complete_chat(request)
        except ModelAdapterError:
            raise
        except Exception as exc:
            raise ModelAdapterError(
                f"{self.provider} model request failed: {exc}"
            ) from exc

    async def close(self) -> None:
        await self._client.close()

    async def _complete_responses(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.default_model,
            "input": _responses_input(request.messages),
        }
        if request.tools:
            kwargs["tools"] = [_responses_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.extra_body:
            kwargs["extra_body"] = request.extra_body

        response = await self._client.responses.create(**kwargs)
        tool_calls = tuple(
            ToolCall(
                id=item.call_id,
                name=item.name,
                arguments=_parse_arguments(item.arguments),
            )
            for item in response.output
            if getattr(item, "type", None) == "function_call"
        )
        finish_reason = _responses_finish_reason(response, tool_calls)

        return ModelResponse(
            id=response.id,
            provider=self.provider,
            model=response.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content=response.output_text or None,
                tool_calls=tool_calls,
            ),
            finish_reason=finish_reason,
            usage=_responses_usage(getattr(response, "usage", None)),
            raw=_model_dump(response),
        )

    async def _complete_chat(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.default_model,
            "messages": [_chat_message(message) for message in request.messages],
        }
        if request.tools:
            kwargs["tools"] = [_chat_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.extra_body:
            kwargs["extra_body"] = request.extra_body

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        response_message = choice.message
        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in (response_message.tool_calls or ())
        )

        return ModelResponse(
            id=response.id,
            provider=self.provider,
            model=response.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content=response_message.content,
                tool_calls=tool_calls,
            ),
            finish_reason=choice.finish_reason,
            usage=_chat_usage(getattr(response, "usage", None)),
            raw=_model_dump(response),
        )


def _arguments_json(arguments: dict[str, Any] | str) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))


def _parse_arguments(arguments: Any) -> dict[str, Any] | str:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return str(arguments)
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    return parsed if isinstance(parsed, dict) else arguments


def _responses_input(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role is MessageRole.TOOL:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content or "",
                }
            )
            continue

        if message.content is not None:
            items.append(
                {
                    "role": message.role.value,
                    "content": message.content,
                }
            )
        for call in message.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": _arguments_json(call.arguments),
                }
            )
    return items


def _chat_message(message: Message) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": _arguments_json(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return result


def _responses_tool(tool: ToolDefinition) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
    if tool.strict is not None:
        result["strict"] = tool.strict
    return result


def _chat_tool(tool: ToolDefinition) -> dict[str, Any]:
    function: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
    if tool.strict is not None:
        function["strict"] = tool.strict
    return {"type": "function", "function": function}


def _responses_finish_reason(
    response: Any,
    tool_calls: tuple[ToolCall, ...],
) -> str | None:
    if tool_calls:
        return "tool_calls"
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        return getattr(details, "reason", None) or "incomplete"
    return getattr(response, "status", None) or "stop"


def _responses_usage(usage: Any | None) -> ModelUsage:
    if usage is None:
        return ModelUsage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(
            getattr(usage, "total_tokens", input_tokens + output_tokens)
            or input_tokens + output_tokens
        ),
    )


def _chat_usage(usage: Any | None) -> ModelUsage:
    if usage is None:
        return ModelUsage()
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(
            getattr(usage, "total_tokens", input_tokens + output_tokens)
            or input_tokens + output_tokens
        ),
    )


def _model_dump(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value if isinstance(value, dict) else None
