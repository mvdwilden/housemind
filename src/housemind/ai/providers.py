"""AI provider adapters (E-06). Three adapters cover the most used providers:

- ``anthropic``          the Anthropic Messages API
- ``gemini``             the Gemini generateContent API
- ``openai_compatible``  OpenAI, Ollama, OpenRouter, LM Studio, Mistral, Groq … (own address,
                         key and model)

All three can let the AI look things up (tool calling, E-419) and send the answer as it comes
(streaming, E-159); ``converse`` does both, ``complete`` is one plain question. An adapter
never raises into the brain: every problem becomes an :class:`AIResult` with ``ok=False`` and
a short error code. Keys never appear in errors or logs (E-86).
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30
ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"

# Address presets for the OpenAI-compatible adapter; the administrator may type any address.
PRESETS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

# Sensible defaults (E-49: a cheap model for routine, a stronger one for hard questions).
DEFAULT_MODELS: dict[str, tuple[str, str]] = {
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5"),
    # Aliases that follow the newest version, so the defaults do not go stale.
    "gemini": ("gemini-flash-lite-latest", "gemini-flash-latest"),
}

OnText = Callable[[str], Awaitable[None]]  # a piece of the answer, as it comes (E-159)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """The AI asks to look something up (E-419): which lookup, with what."""

    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # what a provider wants back as it was


@dataclass(frozen=True, slots=True)
class Tool:
    """A lookup the AI may use: its name, what it does and its arguments (a JSON schema)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Turn:
    """One turn of a conversation with an AI, the same for every provider: the question
    ("user"), the AI's words and lookups ("assistant"), or what the lookups gave ("tool") –
    with a word of the home after it, such as "no more lookups: the answer now" (E-453)."""

    role: str
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[tuple[ToolCall, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AIResult:
    ok: bool
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    calls: tuple[ToolCall, ...] = ()  # the AI wants to look these up first (E-419)


CUT_OFF = "cut_off"  # the answer ran out of room before it was whole


def _cut(result: AIResult, cut: bool) -> AIResult:
    """An answer that ran out of room is no answer: not ok, with what came and what it cost."""
    return replace(result, ok=False, error=CUT_OFF) if cut and result.ok else result


class AIProvider(Protocol):
    name: str
    base_url: str

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult: ...

    async def close(self) -> None: ...


class Conversing(Protocol):
    """A provider that can let the AI look things up (``tools``) and send the answer as it
    comes (``streams``). One without, such as a test's own AI, gets one plain question."""

    tools: bool
    streams: bool

    async def converse(
        self,
        system: str,
        turns: Sequence[Turn],
        model: str,
        max_tokens: int,
        tools: Sequence[Tool] = (),
        final: bool = False,
        on_text: OnText | None = None,
    ) -> AIResult: ...


def is_local(base_url: str) -> bool:
    """Local AI (on this machine or the home network) never leaves the house (E-15)."""
    host = (urlparse(base_url).hostname or "").lower()
    if host in ("localhost", "homeassistant", "homeassistant.local") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def _args(raw: Any) -> dict[str, Any]:
    """The arguments of a lookup: a JSON object, also when it came as text."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _all_required(form: Any) -> bool:
    """Whether every object of a form asks for all of its fields: only such a form may go
    as "strict" to an OpenAI-compatible service."""
    if isinstance(form, list):
        return all(_all_required(item) for item in form)
    if not isinstance(form, dict):
        return True
    fields = form.get("properties")
    if isinstance(fields, dict) and set(form.get("required", ())) != set(fields):
        return False
    return all(_all_required(v) for k, v in form.items() if k not in ("required", "enum"))


def _result(text: str) -> Any:
    """What a lookup gave, as the JSON it is (Gemini wants an object back)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class _Stream:
    """What a streamed answer adds up to: the text, the lookups and the tokens."""

    def __init__(self, on_text: OnText) -> None:
        self.on_text = on_text
        self.text = ""
        self.input_tokens = 0
        self.output_tokens = 0
        self.error: str | None = None
        self.cut_off = False  # it ran out of room before the answer was whole
        self.calls: dict[int, dict[str, Any]] = {}  # per position: id, name, arguments, extra

    async def add_text(self, piece: str) -> None:
        if piece:
            self.text += piece
            await self.on_text(piece)

    async def event(self, data: dict[str, Any]) -> None:
        raise NotImplementedError

    def result(self) -> AIResult:
        if self.error is not None:
            return AIResult(ok=False, error=self.error)
        calls = tuple(
            ToolCall(
                str(c.get("id") or f"{c.get('name')}_{i}"),
                str(c.get("name") or ""),
                _args(c.get("args", c.get("arguments", ""))),
                dict(c.get("extra") or {}),
            )
            for i, c in sorted(self.calls.items())
            if c.get("name")
        )
        found = AIResult(True, self.text, self.input_tokens, self.output_tokens, None, calls)
        return _cut(found, self.cut_off)


class _HttpProvider:
    name = "base"
    tools = True  # every adapter here lets the AI look things up (E-419) …
    streams = True  # … and sends the answer as it comes (E-159) …
    forms = True  # … and can hold the AI to the form of the answer (E-453)

    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session: aiohttp.ClientSession | None = None

    def __repr__(self) -> str:  # never show the key
        return f"{type(self).__name__}({self.base_url!r})"

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            )
        return self._session

    async def complete(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        form: dict[str, Any] | None = None,
    ) -> AIResult:
        """One question. ``form``: the JSON schema the answer must have, which the service
        holds the AI to (E-453); a service that does not know it answers with an error."""
        return await self.converse(system, (Turn("user", user),), model, max_tokens, form=form)

    async def converse(
        self,
        system: str,
        turns: Sequence[Turn],
        model: str,
        max_tokens: int,
        tools: Sequence[Tool] = (),
        final: bool = False,
        on_text: OnText | None = None,
        form: dict[str, Any] | None = None,
    ) -> AIResult:
        raise NotImplementedError

    async def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> AIResult:
        try:
            async with self._client().post(url, headers=headers, json=body) as response:
                if response.status != 200:
                    return AIResult(ok=False, error=f"http_{response.status}")
                payload = await response.json(content_type=None)
        except TimeoutError:
            return AIResult(ok=False, error="timeout")
        except (aiohttp.ClientError, json.JSONDecodeError, ValueError) as err:
            _LOGGER.debug("AI provider %s unreachable: %s", self.name, type(err).__name__)
            return AIResult(ok=False, error="unreachable")
        return self._parse(payload)

    async def _stream(
        self, url: str, headers: dict[str, str], body: dict[str, Any], stream: _Stream
    ) -> AIResult:
        """A streamed answer (server-sent events): every ``data:`` line is one event."""
        try:
            async with self._client().post(url, headers=headers, json=body) as response:
                if response.status != 200:
                    return AIResult(ok=False, error=f"http_{response.status}")
                async for raw in response.content:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        await stream.event(data)
        except TimeoutError:
            return AIResult(ok=False, error="timeout")
        except (aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("AI provider %s unreachable: %s", self.name, type(err).__name__)
            return AIResult(ok=False, error="unreachable")
        except (KeyError, IndexError, TypeError, AttributeError):
            return AIResult(ok=False, error="bad_response")
        return stream.result()

    def _parse(self, payload: Any) -> AIResult:
        raise NotImplementedError

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class _AnthropicStream(_Stream):
    async def event(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        if kind == "message_start":
            usage = data["message"].get("usage") or {}
            self.input_tokens = int(usage.get("input_tokens", 0))
        elif kind == "content_block_start":
            block = data["content_block"]
            if block.get("type") == "tool_use":
                self.calls[int(data["index"])] = {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": "",
                }
        elif kind == "content_block_delta":
            delta = data["delta"]
            if delta.get("type") == "text_delta":
                await self.add_text(str(delta.get("text", "")))
            elif delta.get("type") == "input_json_delta":
                call = self.calls.setdefault(int(data["index"]), {"arguments": ""})
                call["arguments"] += str(delta.get("partial_json", ""))
        elif kind == "message_delta":
            usage = data.get("usage") or {}
            self.output_tokens = int(usage.get("output_tokens", self.output_tokens))
            if (data.get("delta") or {}).get("stop_reason") == "max_tokens":
                self.cut_off = True
        elif kind == "error":
            self.error = "error"


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = ANTHROPIC_URL) -> None:
        super().__init__(base_url, api_key)

    async def converse(
        self,
        system: str,
        turns: Sequence[Turn],
        model: str,
        max_tokens: int,
        tools: Sequence[Tool] = (),
        final: bool = False,
        on_text: OnText | None = None,
        form: dict[str, Any] | None = None,
    ) -> AIResult:
        headers = {
            "x-api-key": self._api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [self._message(turn) for turn in turns],
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            if final:
                body["tool_choice"] = {"type": "none"}  # no more lookups: the answer now
        if form is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": form}}
        url = f"{self.base_url}/messages"
        if on_text is None:
            return await self._post(url, headers, body)
        return await self._stream(url, headers, {**body, "stream": True}, _AnthropicStream(on_text))

    @staticmethod
    def _message(turn: Turn) -> dict[str, Any]:
        if turn.role == "assistant":
            content: list[dict[str, Any]] = []
            if turn.text:
                content.append({"type": "text", "text": turn.text})
            content += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                for c in turn.calls
            ]
            return {"role": "assistant", "content": content}
        if turn.role == "tool":
            content = [
                {"type": "tool_result", "tool_use_id": call.id, "content": text}
                for call, text in turn.results
            ]
            if turn.text:
                content.append({"type": "text", "text": turn.text})
            return {"role": "user", "content": content}
        return {"role": "user", "content": turn.text}

    def _parse(self, payload: Any) -> AIResult:
        try:
            parts = payload["content"]
            text = "".join(part.get("text", "") for part in parts if part.get("type") == "text")
            calls = tuple(
                ToolCall(str(p["id"]), str(p["name"]), _args(p.get("input")))
                for p in parts
                if p.get("type") == "tool_use"
            )
            usage = payload.get("usage") or {}
            found = AIResult(
                ok=True,
                text=text,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                calls=calls,
            )
            return _cut(found, payload.get("stop_reason") == "max_tokens")
        except (KeyError, TypeError, AttributeError, ValueError):
            return AIResult(ok=False, error="bad_response")


class _OpenAIStream(_Stream):
    async def event(self, data: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        if usage:
            self.input_tokens = int(usage.get("prompt_tokens", self.input_tokens))
            self.output_tokens = int(usage.get("completion_tokens", self.output_tokens))
        if data.get("error"):
            self.error = "error"
        for choice in data.get("choices") or []:
            if choice.get("finish_reason") == "length":
                self.cut_off = True
            delta = choice.get("delta") or {}
            await self.add_text(str(delta.get("content") or ""))
            for piece in delta.get("tool_calls") or []:
                call = self.calls.setdefault(int(piece.get("index", 0)), {"arguments": ""})
                if piece.get("id"):
                    call["id"] = piece["id"]
                function = piece.get("function") or {}
                if function.get("name"):
                    call["name"] = function["name"]
                call["arguments"] += str(function.get("arguments") or "")


class OpenAICompatibleProvider(_HttpProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        super().__init__(base_url, api_key)

    async def converse(
        self,
        system: str,
        turns: Sequence[Turn],
        model: str,
        max_tokens: int,
        tools: Sequence[Tool] = (),
        final: bool = False,
        on_text: OnText | None = None,
        form: dict[str, Any] | None = None,
    ) -> AIResult:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in turns:
            messages += self._messages(turn)
        body: dict[str, Any] = {"model": model, "messages": messages}
        # OpenAI's own API wants the newer field name; the compatible servers the older one.
        limit_field = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
        body[limit_field] = max_tokens
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            if final:
                body["tool_choice"] = "none"
        if form is not None:
            # Strict only for a form a strict service takes: every field asked for (E-453).
            schema = {"name": "answer", "schema": form, "strict": _all_required(form)}
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        url = f"{self.base_url}/chat/completions"
        if on_text is None:
            return await self._post(url, headers, body)
        body |= {"stream": True, "stream_options": {"include_usage": True}}  # tokens at the end
        return await self._stream(url, headers, body, _OpenAIStream(on_text))

    @staticmethod
    def _messages(turn: Turn) -> list[dict[str, Any]]:
        if turn.role == "assistant":
            message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
            if turn.calls:
                message["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.args)},
                    }
                    for c in turn.calls
                ]
            return [message]
        if turn.role == "tool":
            messages: list[dict[str, Any]] = [
                {"role": "tool", "tool_call_id": call.id, "content": text}
                for call, text in turn.results
            ]
            if turn.text:
                messages.append({"role": "user", "content": turn.text})
            return messages
        return [{"role": "user", "content": turn.text}]

    def _parse(self, payload: Any) -> AIResult:
        try:
            message = payload["choices"][0]["message"]
            text = message.get("content") or ""
            calls = tuple(
                ToolCall(
                    str(c.get("id") or f"call_{i}"),
                    str(c["function"]["name"]),
                    _args(c["function"].get("arguments")),
                )
                for i, c in enumerate(message.get("tool_calls") or [])
            )
            usage = payload.get("usage") or {}
            found = AIResult(
                ok=True,
                text=str(text),
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                calls=calls,
            )
            return _cut(found, payload["choices"][0].get("finish_reason") == "length")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return AIResult(ok=False, error="bad_response")


class _GeminiStream(_Stream):
    async def event(self, data: dict[str, Any]) -> None:
        usage = data.get("usageMetadata") or {}
        if usage:
            self.input_tokens = int(usage.get("promptTokenCount", self.input_tokens))
            self.output_tokens = int(usage.get("candidatesTokenCount", self.output_tokens))
        if data.get("error"):
            self.error = "error"
        for candidate in data.get("candidates") or []:
            if candidate.get("finishReason") == "MAX_TOKENS":
                self.cut_off = True
            for part in (candidate.get("content") or {}).get("parts") or []:
                if "functionCall" in part:
                    self.calls[len(self.calls)] = GeminiProvider.call(part, len(self.calls))
                elif not part.get("thought"):
                    await self.add_text(str(part.get("text") or ""))


class GeminiProvider(_HttpProvider):
    name = "gemini"

    def __init__(self, api_key: str, base_url: str = GEMINI_URL) -> None:
        super().__init__(base_url, api_key)

    async def converse(
        self,
        system: str,
        turns: Sequence[Turn],
        model: str,
        max_tokens: int,
        tools: Sequence[Tool] = (),
        final: bool = False,
        on_text: OnText | None = None,
        form: dict[str, Any] | None = None,
    ) -> AIResult:
        # The key goes in a header, never in the address (so it cannot end up in a log).
        headers = {"x-goog-api-key": self._api_key or "", "content-type": "application/json"}
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [self._content(turn) for turn in turns],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if tools:
            declarations = []
            for t in tools:
                found: dict[str, Any] = {"name": t.name, "description": t.description}
                if t.parameters.get("properties"):
                    found["parameters"] = t.parameters  # none at all when it takes nothing
                declarations.append(found)
            body["tools"] = [{"functionDeclarations": declarations}]
            if final:
                body["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        if form is not None:
            body["generationConfig"] |= {
                "responseMimeType": "application/json",
                "responseJsonSchema": form,
            }
        if on_text is None:
            return await self._post(
                f"{self.base_url}/models/{model}:generateContent", headers, body
            )
        url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
        return await self._stream(url, headers, body, _GeminiStream(on_text))

    @staticmethod
    def _content(turn: Turn) -> dict[str, Any]:
        if turn.role == "assistant":
            parts: list[dict[str, Any]] = [{"text": turn.text}] if turn.text else []
            parts += [
                {"functionCall": {"name": c.name, "args": c.args}, **c.extra} for c in turn.calls
            ]
            return {"role": "model", "parts": parts}
        if turn.role == "tool":
            parts = [
                {"functionResponse": {"name": call.name, "response": {"result": _result(text)}}}
                for call, text in turn.results
            ]
            return {"role": "user", "parts": parts + ([{"text": turn.text}] if turn.text else [])}
        return {"role": "user", "parts": [{"text": turn.text}]}

    @staticmethod
    def call(part: dict[str, Any], index: int) -> dict[str, Any]:
        """One lookup the AI asked for; its signature goes back with it as it came."""
        found = part["functionCall"]
        extra = {"thoughtSignature": part["thoughtSignature"]} if "thoughtSignature" in part else {}
        name = str(found.get("name") or "")
        return {
            "id": found.get("id") or f"{name}_{index}",
            "name": name,
            "args": found.get("args") or {},
            "extra": extra,
        }

    def _parse(self, payload: Any) -> AIResult:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(
                str(p.get("text", ""))
                for p in parts
                if isinstance(p, dict) and "functionCall" not in p and not p.get("thought")
            )
            calls = tuple(
                ToolCall(str(found["id"]), str(found["name"]), _args(found["args"]), found["extra"])
                for i, p in enumerate(parts)
                if isinstance(p, dict) and "functionCall" in p
                for found in (self.call(p, i),)
            )
            usage = payload.get("usageMetadata") or {}
            found = AIResult(
                ok=True,
                text=text,
                input_tokens=int(usage.get("promptTokenCount", 0)),
                output_tokens=int(usage.get("candidatesTokenCount", 0)),
                calls=calls,
            )
            return _cut(found, payload["candidates"][0].get("finishReason") == "MAX_TOKENS")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return AIResult(ok=False, error="bad_response")


class HomeAssistantAI:
    """The AI task already set up in Home Assistant (E-422): HouseMind keeps no key, and Home
    Assistant asks the AI with ``ai_task.generate_data``. The same instruction and data go as
    one text. It cannot look things up nor answer word by word: Vesta gets the snapshot of now
    (design 50.5). Tokens and costs HouseMind does not see."""

    name = "home_assistant"
    tools = False
    streams = False

    def __init__(self, ha: Any, entity_id: str) -> None:
        self.ha = ha
        self.entity_id = entity_id
        self.base_url = f"homeassistant://{entity_id}"

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        found = await self.ha.ai_task(self.entity_id, "HouseMind", f"{system}\n\n{user}")
        if found is None:
            return AIResult(ok=False, error="unreachable")
        return AIResult(ok=True, text=found if isinstance(found, str) else json.dumps(found))

    async def close(self) -> None:
        pass


def make_provider(
    kind: str, base_url: str | None, api_key: str | None, ha: Any = None
) -> AIProvider:
    if kind == "home_assistant":
        if ha is None or not (base_url or "").startswith("ai_task."):
            raise ValueError("an AI task of Home Assistant is needed")
        return HomeAssistantAI(ha, str(base_url))
    if kind == "anthropic":
        return AnthropicProvider(api_key or "", base_url or ANTHROPIC_URL)
    if kind == "gemini":
        return GeminiProvider(api_key or "", base_url or GEMINI_URL)
    if kind in PRESETS and not base_url:
        return OpenAICompatibleProvider(PRESETS[kind], api_key)
    if not base_url:
        raise ValueError("an address is needed for an OpenAI-compatible provider")
    return OpenAICompatibleProvider(base_url, api_key)
