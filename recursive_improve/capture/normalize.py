"""Normalize LLM responses into a common message format."""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stringify_content(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(value)


def _normalize_openai_input_message(msg: dict) -> dict:
    normalized = {
        "role": msg.get("role", "user"),
        "content": _stringify_content(msg.get("content", "")),
        "timestamp": _now_iso(),
    }
    if msg.get("tool_call_id"):
        normalized["tool_call_id"] = msg["tool_call_id"]
    return normalized


def _normalize_anthropic_input_message(msg: dict) -> list[dict]:
    role = msg.get("role", "user")
    content = msg.get("content", "")

    if not isinstance(content, list):
        return [{
            "role": role,
            "content": _stringify_content(content),
            "timestamp": _now_iso(),
        }]

    messages = []
    text_parts = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type == "tool_result":
                messages.append({
                    "role": "tool",
                    "content": _stringify_content(block.get("content", "")),
                    "timestamp": _now_iso(),
                    "tool_call_id": block.get("tool_use_id") or block.get("id"),
                })
            elif block_type == "text":
                text_parts.append(str(block.get("text", "")))
            elif block_type == "tool_use":
                pass  # tool calls captured on response message
            else:
                text_parts.append(_stringify_content(block))
            continue

        block_type = getattr(block, "type", None)
        if block_type == "tool_result":
            messages.append({
                "role": "tool",
                "content": _stringify_content(getattr(block, "content", "")),
                "timestamp": _now_iso(),
                "tool_call_id": getattr(block, "tool_use_id", None) or getattr(block, "id", None),
            })
        elif block_type == "text":
            text_parts.append(str(getattr(block, "text", "")))
        elif block_type == "tool_use":
            pass  # tool calls captured on response message
        else:
            text_parts.append(_stringify_content(block))

    if text_parts:
        messages.insert(0, {
            "role": role,
            "content": "\n".join(part for part in text_parts if part),
            "timestamp": _now_iso(),
        })
    return messages


def _extract_usage(usage) -> dict | None:
    """Extract token usage from a usage object (works for openai/anthropic)."""
    if usage is None:
        return None
    # Dict-like
    if isinstance(usage, dict):
        return {
            "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens", 0),
            "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    # Object with attributes (openai/anthropic response objects)
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    completion = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)
    total = getattr(usage, "total_tokens", prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def normalize_openai(kwargs: dict, response) -> list[dict]:
    """Normalize an OpenAI ChatCompletion response."""
    messages = []

    # Include input messages from kwargs
    for msg in kwargs.get("messages", []):
        messages.append(_normalize_openai_input_message(msg))

    # Extract assistant response
    if hasattr(response, "choices") and response.choices:
        choice = response.choices[0]
        msg = choice.message if hasattr(choice, "message") else choice
        content = getattr(msg, "content", None) or ""
        tool_calls = None
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        assistant_msg = {
            "role": "assistant",
            "content": content,
            "timestamp": _now_iso(),
            "model": getattr(response, "model", kwargs.get("model", "unknown")),
            "provider": "openai",
            "usage": _extract_usage(getattr(response, "usage", None)),
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

    return messages


def normalize_anthropic(kwargs: dict, response) -> list[dict]:
    """Normalize an Anthropic Messages response."""
    messages = []

    system_prompt = kwargs.get("system")
    if system_prompt:
        messages.append({
            "role": "system",
            "content": _stringify_content(system_prompt),
            "timestamp": _now_iso(),
        })

    # Include input messages from kwargs
    for msg in kwargs.get("messages", []):
        messages.extend(_normalize_anthropic_input_message(msg))

    # Extract assistant response
    content_parts = []
    tool_calls = []

    if hasattr(response, "content"):
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                content_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "function": {
                        "name": block.name,
                        "arguments": (
                            block.input if isinstance(block.input, str)
                            else _json.dumps(block.input)
                        ),
                    },
                })

    assistant_msg = {
        "role": "assistant",
        "content": "\n".join(content_parts),
        "timestamp": _now_iso(),
        "model": getattr(response, "model", kwargs.get("model", "unknown")),
        "provider": "anthropic",
        "usage": _extract_usage(getattr(response, "usage", None)),
    }
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    messages.append(assistant_msg)

    return messages


def normalize_litellm(kwargs: dict, response) -> list[dict]:
    """Normalize a litellm response (OpenAI-compatible format)."""
    result = normalize_openai(kwargs, response)
    # Override provider label
    for msg in result:
        if msg.get("role") == "assistant":
            msg["provider"] = "litellm"
    return result
