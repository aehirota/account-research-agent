import json
import os
from pathlib import Path
from typing import Optional, Type, TypeVar

from anthropic import Anthropic
from pydantic import BaseModel

_client: Optional[Anthropic] = None
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

T = TypeVar("T", bound=BaseModel)


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def call(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
    cache_system: bool = True,
) -> str:
    """Plain text completion. Caches the system prompt by default."""
    system_block = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system
    )
    resp = client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_block,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def call_with_tools(
    model: str,
    system: str,
    user: str,
    tools: list[dict],
    max_tokens: int = 4096,
    cache_system: bool = True,
) -> str:
    """Agentic loop with arbitrary tools (e.g. Anthropic native web_search).

    The model can use the provided tools 0..N times, then emits a final text
    response. We let Anthropic's tool loop handle execution for server-side
    tools like web_search. Returns the concatenated final text content.
    """
    system_block = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system
    )
    resp = client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_block,
        tools=tools,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n\n".join(parts).strip()


def _heal_input(v):
    """Recursively coerce JSON-stringified lists/dicts back to actual types.

    Haiku sometimes emits structured tool-call inputs with list/dict fields
    serialized as JSON strings rather than native objects. This walks the
    input and tries to parse anything that looks like JSON. False positives
    require a string that both starts and ends with [ ] or { } and parses
    cleanly — acceptable risk for our schemas.
    """
    if isinstance(v, dict):
        return {k: _heal_input(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_heal_input(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                return json.loads(s)
            except Exception:
                pass
    return v


def call_structured(
    model: str,
    system: str,
    user: str,
    schema: Type[T],
    max_tokens: int = 4096,
    cache_system: bool = True,
) -> T:
    """Forces a tool call whose input matches `schema`. Returns validated model."""
    tool_name = "emit_" + schema.__name__.lower()
    system_block = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system
    )
    resp = client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_block,
        tools=[
            {
                "name": tool_name,
                "description": f"Emit a {schema.__name__} object.",
                "input_schema": schema.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == tool_name:
            return schema.model_validate(_heal_input(block.input))
    raise RuntimeError(
        f"Model did not emit the {tool_name} tool. Response: {resp.content}"
    )
