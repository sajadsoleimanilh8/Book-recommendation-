"""LLM provider abstraction and local model backends — F-22 Phase B.

The AI Librarian (prmpt.md section 28) needs a model that can converse and
compose tool calls, not just classify intent. Section 10 (Provider
Abstraction) is explicit that no external dependency — including an LLM —
may become coupled to business logic: "every external dependency must sit
behind an interface." This module is that interface. `ChatbotEngine` (Phase
C, not built yet) will call `LLMProvider.chat()`; it will never import
Ollama's client or know its HTTP shape.

Decided (2026-09-18): local, on this machine's RTX 5070 Ti, via Ollama —
already installed, already holding `qwen2.5:7b` and `qwen2.5:3b`. No API
key, no per-call cost, stays consistent with the app's local-only posture.
Both models were live-tested against section 28's own worked example
("something like Atomic Habits but more philosophical, exclude James
Clear") through Ollama's real tool-calling API before this was written, not
assumed: both correctly chose `search_catalog` and correctly extracted the
exclusion constraint. `qwen2.5:7b` (4.7 GB, confirmed 100% GPU) is primary;
`qwen2.5:3b` (2.2 GB) is the fallback tier within this module.

Section 12 (Demo Resilience) sets the bar this file exists to clear: "every
external AI/API feature must have a graceful fallback... the application
must NOT hard crash." `FallbackLLM` tries each configured model in order and
raises `LLMUnavailable` only once all of them have failed — one thing for a
caller to catch, not N. That exception is *this module's* floor, not the
whole system's: section 12's fallback chain continues below it with a
deterministic/local option, which for this project is `ChatbotEngine`'s
existing classify-and-template behaviour (Phase C's job to wire up, not
this module's — an intent classifier has no "chat" shape to speak of, so
forcing it behind this same interface would be the wrong abstraction).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "qwen2.5:3b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


class LLMUnavailable(RuntimeError):
    """Every provider in the chain failed — connection refused, timeout, or
    the model does not exist on this Ollama instance. The caller decides
    what "graceful" means for it (section 12); this module's only job is to
    make that a single, specific exception rather than N different ones."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Which model actually answered — a fallback firing is not a caller's
    # problem to notice, but it is worth being able to see in a log line,
    # the same reason `content_space`/`embedding_model` are reported elsewhere.
    model: str = ""


class LLMProvider(Protocol):
    """Section 10: the interface every caller talks to. `tools` follows the
    OpenAI/Ollama function-calling shape (a list of `{"type": "function",
    "function": {...}}` dicts) since that is what every model here actually
    implements — adopting a project-invented shape instead would mean
    translating it right back at the one call site that matters.
    """

    name: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse: ...


class OllamaProvider:
    """One model, served by Ollama's local HTTP API.

    Not built on `providers.base.fetch_json`: that helper is GET-only and
    tuned for a paginated metadata backfill (retry ladder, rate limiter,
    QuotaExceeded) that does not apply here — a chat call is a single POST
    with a JSON body, and Ollama has no daily quota to exhaust. Sharing it
    would mean bending its shape to fit, not reusing it as designed.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = OLLAMA_BASE_URL,
        timeout: float = 30.0,
    ):
        self.name = f"ollama:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            # Ollama answers 404 with {"error": "model 'x' not found"} for a
            # model that was never pulled — a real, live failure mode
            # (verified against this exact Ollama instance), not a
            # hypothetical one, and worth surfacing over the generic
            # URLError below.
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise LLMUnavailable(f"{self.name}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"{self.name}: {exc}") from exc

        message = data.get("message") or {}
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments", {}),
            )
            for tc in (message.get("tool_calls") or [])
        ]
        return LLMResponse(
            content=message.get("content", ""), tool_calls=tool_calls, model=self.model
        )


class FallbackLLM:
    """Section 12's fallback chain, made mechanical: try each provider in
    order, move on on `LLMUnavailable`, raise once none are left.

    Deliberately dumb about *why* a provider failed — it does not
    distinguish "model missing" from "connection refused" from "timeout".
    All three mean the same thing to a caller: this tier of the chain is
    gone, try the next one. Distinguishing them would be diagnosing an
    outage mid-request, which is not what a chat reply is for; that
    diagnosis belongs in the log line this class writes, not in control flow.
    """

    name = "fallback"

    def __init__(self, providers: list[LLMProvider]):
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self.providers = providers

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        failures: list[str] = []
        for provider in self.providers:
            try:
                return provider.chat(messages, tools=tools)
            except LLMUnavailable as exc:
                log.warning(f"{provider.name} unavailable, trying next in chain: {exc}")
                failures.append(str(exc))
        raise LLMUnavailable(
            f"every provider in the chain failed: {'; '.join(failures)}"
        )


def get_llm_provider() -> LLMProvider:
    """The chain this project actually runs: qwen2.5:7b, then qwen2.5:3b.

    Both model names are overridable via `LLM_MODEL`/`LLM_FALLBACK_MODEL`
    for anyone running this on a machine without these exact models pulled;
    the defaults match what is already on this one.
    """
    return FallbackLLM([
        OllamaProvider(DEFAULT_MODEL),
        OllamaProvider(FALLBACK_MODEL),
    ])
