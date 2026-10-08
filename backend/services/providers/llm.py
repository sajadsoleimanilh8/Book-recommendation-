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
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Protocol

import config

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "qwen2.5:3b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# Decided 2026-10-06: Claude when `ANTHROPIC_API_KEY` is configured (see
# `get_llm_provider` below), the local Ollama chain above when it is not —
# local dev and anyone without a key keep today's behaviour exactly.
# claude-sonnet-5-5 primary, claude-haiku-4-5 fallback: the same fallback
# *role* the qwen2.5 7b/3b pair fills (a smaller, faster model for when the
# first is unavailable). Both ids exactly as Anthropic's current model table
# gives them — complete as written, no date suffix appended.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_FALLBACK_MODEL = os.getenv("CLAUDE_FALLBACK_MODEL", "claude-haiku-4-5")
# The Messages API has no server-side default for `max_tokens` and refuses a
# request without one. On claude-sonnet-5-5 thinking is adaptive by default,
# and thinking counts toward this cap *even when its text is not returned*
# (the default `display` is "omitted") — so a cap sized for the visible reply
# alone truncates turns mid-thought. 16000 is Anthropic's recommended default
# for a non-streaming request: room for thinking plus a reply, while keeping
# a single call inside the SDK's HTTP timeouts.
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "16000"))
# A refusal from the primary's safety classifiers (HTTP 200,
# `stop_reason: "refusal"`) is re-run server-side on a model chosen by
# refusal category, inside the same call — `fallbacks: "default"`, beta
# `server-side-fallback-2026-07-01`. Anthropic's guidance is to enable this
# by default for claude-sonnet-5-5 on the Claude API; it is only sent for a
# provider constructed with `refusal_fallback=True` (the primary), because
# the parameter is not part of every model's request surface.
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Found live, not hypothesised: `urllib.request`'s default opener honours
# whatever proxy Windows/WinINET has configured system-wide, and does not
# exempt loopback addresses from it the way curl and most browsers do. On a
# machine with any system proxy active (a corporate VPN, a debugging tool —
# nothing to do with Ollama's own health), every call here was routed
# through that proxy, which reset the connection instead of relaying it to
# Ollama. Ollama is local by this module's own design decision (its
# docstring above) — there is never a legitimate reason to proxy a call to
# it — so this opener is built once, with an empty `ProxyHandler`, to
# bypass system proxy discovery entirely rather than depend on the
# environment where this happens to run.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))



class LLMUnavailable(RuntimeError):
    """Every provider in the chain failed — connection refused, timeout, or
    the model does not exist on this Ollama instance. The caller decides
    what "graceful" means for it (section 12); this module's only job is to
    make that a single, specific exception rather than N different ones."""


# -- how many requests may be in the model at once ---------------------------------
#
# OI-5. A per-caller rate limit bounds how fast one address can ask; it does
# not bound how many addresses ask at once, and each of those requests drives
# up to `MAX_STEPS` round trips against one local model on one GPU. Without a
# cap the queue grows until every request times out — including the ones
# already in flight, so an overload degrades everybody rather than the
# marginal caller.
#
# Two, because concurrency past that buys nothing here: Ollama serialises work
# on a single GPU, so a third simultaneous request adds latency without adding
# throughput. The short wait absorbs a burst that arrives together; past it the
# caller is not refused, they fall through to the classifier — section 12 says
# degrade, and a cheap deterministic answer now beats a good answer after the
# queue drains.
MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "2")))
QUEUE_WAIT = float(os.getenv("LLM_QUEUE_WAIT", "5.0"))

_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)


@contextmanager
def slot() -> Iterator[None]:
    """Hold one of the model's concurrency slots, or raise `LLMUnavailable`.

    Held across a whole tool loop rather than around each `chat()` call: a
    per-call slot could be granted for step 1 and refused for step 2, which
    abandons a half-finished answer *and* has already spent the GPU time that
    produced it. The unit of work is the request.
    """
    if not _slots.acquire(timeout=QUEUE_WAIT):
        raise LLMUnavailable(
            f"all {MAX_CONCURRENCY} model slots busy for {QUEUE_WAIT}s"
        )
    try:
        yield
    finally:
        _slots.release()


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
    # The provider's own form of this assistant turn, opaque to every caller.
    # A caller running a tool loop hands it back on the assistant message it
    # appends (`"native": response.native`) and never looks inside.
    #
    # It exists for Claude, whose thinking blocks must be passed back
    # *unchanged* on the next request of a tool loop — Anthropic's own words:
    # dropping or editing them "breaks the turn", and stripping them can
    # trigger ordering/signature 400s. The OpenAI-shaped history this project
    # uses everywhere has no place for a thinking block, so without this the
    # Librarian would silently strip them on every step. Ollama leaves it
    # `None`. Excluded from equality and repr: it is transport, not content.
    native: Any = field(default=None, compare=False, repr=False)


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
            with _NO_PROXY_OPENER.open(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            # Ollama answers 404 with {"error": "model 'x' not found"} for a
            # model that was never pulled — a real, live failure mode
            # (verified against this exact Ollama instance), not a
            # hypothetical one, and worth surfacing over the generic
            # URLError below.
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise LLMUnavailable(f"{self.name}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            # `URLError` covers a failed connection *attempt* (refused,
            # unresolvable host). It does not cover a connection that
            # succeeded and then failed while the response was being read —
            # that surfaces as a raw `OSError` subclass (`ConnectionResetError`,
            # `TimeoutError`, ...), not wrapped in `URLError`, and was
            # reaching the caller as an unhandled stack trace instead of
            # `LLMUnavailable`. `OSError` is the common base for both, and
            # does not touch `json.JSONDecodeError` (a `ValueError`, kept
            # explicit) or anything raised by malformed response *content*
            # below this block — only genuine I/O failure becomes "the model
            # is unavailable," per this file's own section-12 contract.
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


# -- Claude: translating to and from the project's OpenAI/Ollama shape ------
#
# `LLMProvider.chat()` promises every caller the OpenAI/Ollama function-
# calling shape (the Protocol's own docstring says so, and `services/
# librarian.py`'s tool loop is written to it). Anthropic's Messages API
# differs materially, not by a renamed field here and there:
#
#   * No `role: "system"` message at the start. The system prompt is the
#     top-level `system` parameter.
#   * Tool *definitions* are `{"name", "description", "input_schema"}`, with
#     no outer `{"type": "function", "function": {...}}` wrapper.
#   * A tool *call* is a `tool_use` content block on the assistant message,
#     not a `tool_calls` list beside it.
#   * A tool *result* is a `tool_result` block inside a **user** message —
#     and every `tool_use` from one assistant turn must be answered inside a
#     single following user message, not one message per tool.
#   * The assistant turn carries `thinking` blocks that must be passed back
#     unchanged for the tool loop to continue (see `LLMResponse.native`).
#
# Translated at this boundary rather than pushed up into `librarian.py`
# (section 10: an external dependency's wire shape stays behind the
# interface). The helpers below are pure data transforms — no network, no
# client — so the translation is unit-tested directly.


def _anthropic_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[list[dict[str, Any]]]:
    """OpenAI/Ollama tool *definitions* -> Anthropic's shape.

    `{"type": "function", "function": {"name", "description", "parameters"}}`
    becomes `{"name", "description", "input_schema"}`. `None` rather than an
    empty list when there are none, so the parameter is omitted entirely.
    """
    if not tools:
        return None
    out = []
    for tool in tools:
        fn = tool.get("function", tool)
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _anthropic_messages(
    messages: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI/Ollama-shaped history -> `(system, anthropic_messages)`.

    Each step exists because `librarian.py`'s tool loop actually produces
    that shape — `{"role": "assistant", "tool_calls": [...]}` followed by one
    `{"role": "tool", "tool_name": ..., "content": ...}` per call:

    1. `system`-role messages are lifted out and returned separately.
    2. An assistant turn that called tools is sent as **its own native
       content** when the caller handed it back (`"native"`, from
       `LLMResponse.native`) — thinking blocks and real `tool_use` ids
       intact, which is what Anthropic requires to continue a tool loop.
       Without it (history built by hand, or by a caller that does not carry
       `native`), the calls are rebuilt as `tool_use` blocks with ids
       synthesised in call order. That path has no thinking blocks to lose
       and is valid on its own; it is the fallback, not the intended route.
    3. The `tool` messages that follow are converted to `tool_result` blocks,
       matched to those ids **by position** (neither this project's
       `ToolCall` nor its tool-role message carries an id), and batched into
       one user message — the one structural rule the API enforces here.

    A `tool` message with nothing left to answer is a malformed history — a
    caller bug, raised rather than silently dropped or guessed at.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    call_seq = 0

    for msg in messages:
        role = msg.get("role")

        if role == "system":
            if msg.get("content"):
                system_parts.append(str(msg["content"]))
            continue

        if role == "assistant" and (msg.get("native") or msg.get("tool_calls")):
            native = msg.get("native")
            if native:
                blocks = [dict(b) for b in native]
            else:
                blocks = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": str(msg["content"])})
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function", call)
                    blocks.append({
                        "type": "tool_use",
                        "id": f"call_{call_seq}",
                        "name": fn["name"],
                        "input": fn.get("arguments") or {},
                    })
                    call_seq += 1
            pending_ids = [b["id"] for b in blocks if b.get("type") == "tool_use"]
            out.append({"role": "assistant", "content": blocks})
            continue

        if role == "tool":
            if not pending_ids:
                raise ValueError(
                    "a 'tool' message has no preceding tool_use to answer — "
                    "the message history is malformed"
                )
            result_block = {
                "type": "tool_result",
                "tool_use_id": pending_ids.pop(0),
                "content": str(msg.get("content", "")),
            }
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and all(b.get("type") == "tool_result" for b in out[-1]["content"])
            ):
                out[-1]["content"].append(result_block)
            else:
                out.append({"role": "user", "content": [result_block]})
            continue

        out.append({"role": role, "content": str(msg.get("content", ""))})

    return "\n\n".join(system_parts), out


# Block types a model can emit that are never sent back. A `fallback` block
# is an audit marker for a server-side refusal fallback ("keep or drop") and
# is dropped so the stored turn stays valid on any endpoint, including the
# non-beta one the fallback-tier provider uses.
_UNREPLAYED_BLOCKS = {"fallback"}
# Before the last `fallback` block in a turn, these belong to the model that
# declined and must be omitted when the turn is echoed back.
_PRE_FALLBACK_DROPPED = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


def _replayable(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The assistant content to store for replay, per Anthropic's echo rules.

    Normally the whole turn, unchanged — thinking blocks included. After a
    mid-output server-side fallback, the declining model's `thinking`,
    `redacted_thinking` and `tool_use` blocks before the final `fallback`
    block are omitted; text, and everything after the boundary, is kept.
    """
    last_fallback = max(
        (i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1
    )
    kept = []
    for i, block in enumerate(content):
        kind = block.get("type")
        if kind in _UNREPLAYED_BLOCKS:
            continue
        if i < last_fallback and kind in _PRE_FALLBACK_DROPPED:
            continue
        kept.append(block)
    return kept


def _from_anthropic_response(content: list[dict[str, Any]], model: str) -> LLMResponse:
    """Anthropic content blocks -> this project's `LLMResponse`.

    Read by block `type`, never by position: with adaptive thinking a turn
    often opens with a `thinking` block whose text is empty under the
    default `display: "omitted"`. Prose and tool calls can share one turn,
    and both are collected. Only `tool_use` blocks that survive the echo
    rules become `ToolCall`s, so a call is never offered to the caller
    without the native turn that can carry its result back.
    """
    replay = _replayable(content)
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in replay:
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text", ""))
        elif kind == "tool_use":
            tool_calls.append(ToolCall(
                id=block.get("id", ""),
                name=block.get("name", ""),
                arguments=block.get("input") or {},
            ))
    return LLMResponse(
        content="".join(text_parts), tool_calls=tool_calls, model=model, native=replay
    )


class ClaudeProvider:
    """One Claude model, through the official Anthropic SDK.

    Behind the same `LLMProvider` contract `OllamaProvider` implements: the
    translation above is what lets `FallbackLLM`, `services/librarian.py`'s
    tool loop, and every other caller work against Claude unmodified.

    The SDK, not hand-rolled HTTP, on Anthropic's own guidance for a Python
    project: it owns the wire format, the version header, retries, and a
    typed error hierarchy, none of which this project should be keeping in
    step with by hand. It honours the standard `HTTPS_PROXY`/`NO_PROXY`
    environment variables for egress.

    `timeout`/`max_retries` are deliberately far below the SDK's defaults
    (10 minutes, 2 retries). The Librarian holds one of `MAX_CONCURRENCY`
    model slots across its whole tool loop, and section 12 says degrade
    rather than wait: one quick retry absorbs a transient 429/529, after
    which `FallbackLLM` moves to the next tier instead of a caller sitting
    through the SDK's full backoff ladder.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 1,
        max_tokens: int = CLAUDE_MAX_TOKENS,
        refusal_fallback: bool = False,
        http_client: Any = None,
    ):
        self.name = f"claude:{model}"
        self.model = model
        # `core/config.py` holds every credential this app reads, so the key
        # comes from there by default — never a bare `os.getenv` here.
        self.api_key = api_key if api_key is not None else config.ANTHROPIC_API_KEY
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.refusal_fallback = refusal_fallback
        self._http_client = http_client
        self._client = None

    def _sdk(self):
        if self._client is None:
            import anthropic

            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": self.timeout,
                "max_retries": self.max_retries,
            }
            if self._http_client is not None:
                kwargs["http_client"] = self._http_client
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> LLMResponse:
        if not self.api_key:
            # `get_llm_provider` never builds this provider without a key, so
            # this is a guard, not a code path — but it keeps the contract:
            # "this tier is unavailable", never a crash (section 12).
            raise LLMUnavailable(f"{self.name}: ANTHROPIC_API_KEY is not set")

        import anthropic

        # Before the network call, on purpose: a malformed history is a bug
        # in the caller and must reach it as one, not as "unavailable".
        system, anthropic_messages = _anthropic_messages(messages)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            params["system"] = system
        anthropic_tools = _anthropic_tools(tools)
        if anthropic_tools:
            params["tools"] = anthropic_tools

        try:
            if self.refusal_fallback:
                response = self._sdk().beta.messages.create(
                    **params, betas=[SERVER_SIDE_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._sdk().messages.create(**params)
        except anthropic.APIStatusError as exc:
            # 400 (a malformed request this translation produced), 401 (bad
            # key), 404 (a model id this account cannot use), and a 429/529
            # that outlasted the one SDK retry. Section 12's floor is one
            # exception for "this tier is not answering", not a diagnosis.
            raise LLMUnavailable(
                f"{self.name}: HTTP {exc.status_code} {exc.message}"
            ) from exc
        except anthropic.APIError as exc:
            # Connection failures and timeouts (`APIConnectionError`,
            # `APITimeoutError`), after the SDK's own retry.
            raise LLMUnavailable(f"{self.name}: {type(exc).__name__}: {exc}") from exc

        served_by = getattr(response, "model", None) or self.model
        stop = getattr(response, "stop_reason", None)

        if stop == "refusal":
            # HTTP 200 with nothing usable — and with `refusal_fallback`, the
            # server-side fallback refused too. Returning an empty reply here
            # would read as "the Librarian had nothing to say"; raising lets
            # `FallbackLLM` try the next tier, and past it the caller's own
            # deterministic answer, exactly as for an outage.
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMUnavailable(f"{self.name}: refused (category={category})")
        if stop == "max_tokens":
            log.warning(
                f"{self.name}: reply hit max_tokens={self.max_tokens}; "
                "the answer may be truncated"
            )

        content = [block.to_dict(exclude_none=True) for block in response.content]
        return _from_anthropic_response(content, served_by)


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
    """Claude when a key is configured; the local Ollama chain when it is
    not — decided 2026-10-06, once Claude API billing made that a real
    choice rather than a hypothetical one.

    The two chains are not mixed into one fallback list. `ANTHROPIC_API_KEY`
    present means Claude primary / Claude fallback; absent means exactly the
    qwen2.5 7b/3b chain this project ran before, unchanged — so a machine
    with no key set (every local dev box, by default) behaves identically
    to before this function existed, and nothing here requires Ollama to be
    installed at all once a key is configured.

    Both model names stay overridable (`CLAUDE_MODEL`/`CLAUDE_FALLBACK_MODEL`,
    `LLM_MODEL`/`LLM_FALLBACK_MODEL`) for the same reason the Ollama pair
    already was: a model id is a live external fact, not something to hold
    only as a literal in this file.
    """
    if config.ANTHROPIC_API_KEY:
        return FallbackLLM([
            # Server-side refusal fallback on the primary only: Anthropic
            # recommends it for claude-sonnet-5-5, and it is not part of
            # every model's request surface. The client-side tier below
            # still catches an outage, or a refusal the server-side
            # fallback could not rescue.
            ClaudeProvider(CLAUDE_MODEL, refusal_fallback=True),
            ClaudeProvider(CLAUDE_FALLBACK_MODEL),
        ])
    return FallbackLLM([
        OllamaProvider(DEFAULT_MODEL),
        OllamaProvider(FALLBACK_MODEL),
    ])
