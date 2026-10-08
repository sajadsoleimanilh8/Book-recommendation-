"""LLMProvider and its fallback chain — F-22 Phase B.

Two kinds of test, kept apart on purpose:

1. **The chain's own logic**, against fake providers. Deterministic, fast, no
   Ollama needed — this is what proves "tries the next one, raises only when
   none are left" and would still run on a machine with no model installed.
2. **The real wiring**, against the actual local Ollama and the actual
   qwen2.5 models this project committed to. Skipped (not failed) when Ollama
   is unreachable, the same convention `database_reachable()` uses, because a
   green run on a machine that cannot run the model would be exactly the
   "test that can pass without executing the code under test" F-45 already
   named. The point of the live tests is that they use a *real* failure —
   asking Ollama for a model that was never pulled — instead of a mocked one.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.providers import llm  # noqa: E402
from services.providers.llm import (  # noqa: E402
    CLAUDE_FALLBACK_MODEL,
    CLAUDE_MODEL,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    ClaudeProvider,
    FallbackLLM,
    LLMResponse,
    LLMUnavailable,
    OllamaProvider,
    get_llm_provider,
)


def ollama_reachable() -> bool:
    # Same reasoning as llm._NO_PROXY_OPENER: a bare `urlopen` here honours
    # whatever system proxy is active and reports Ollama unreachable when a
    # proxy resets the loopback connection, not when Ollama actually is
    # down. Found live: all four "really" tests below skipped on a machine
    # where `curl http://127.0.0.1:11434/api/tags` answered 200.
    try:
        with llm._NO_PROXY_OPENER.open("http://127.0.0.1:11434/api/tags", timeout=3):
            return True
    except Exception:
        return False


requires_ollama = pytest.mark.skipif(
    not ollama_reachable(), reason="Ollama not reachable on 127.0.0.1:11434"
)


class _Working:
    def __init__(self, name="working", content="ok"):
        self.name = name
        self.content = content
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        return LLMResponse(content=self.content, model=self.name)


class _Broken:
    def __init__(self, name="broken"):
        self.name = name
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        raise LLMUnavailable(f"{self.name} is down")


MESSAGES = [{"role": "user", "content": "hello"}]


# --------------------------------------------------------------------------
# The chain's own logic — no Ollama involved
# --------------------------------------------------------------------------


def test_the_first_working_provider_answers_and_the_rest_are_never_called():
    first, second = _Working("first"), _Working("second")
    result = FallbackLLM([first, second]).chat(MESSAGES)

    assert result.model == "first"
    assert second.calls == 0, "a healthy primary must not also hit the fallback"


def test_a_broken_primary_falls_through_to_the_next_provider():
    broken, working = _Broken("primary"), _Working("fallback")
    result = FallbackLLM([broken, working]).chat(MESSAGES)

    assert broken.calls == 1, "the primary was never actually tried"
    assert result.model == "fallback"


def test_every_provider_failing_raises_one_specific_exception():
    """One thing for a caller to catch — section 12's floor for this module.
    The caller's own deterministic fallback takes over from here."""
    with pytest.raises(LLMUnavailable) as exc_info:
        FallbackLLM([_Broken("a"), _Broken("b")]).chat(MESSAGES)

    message = str(exc_info.value)
    assert "a is down" in message and "b is down" in message, (
        "the error must say what failed, not just that something did"
    )


def test_a_non_llm_error_is_not_swallowed_as_unavailability():
    """`FallbackLLM` must only catch `LLMUnavailable`. A bug in a provider
    (a KeyError, say) falling silently through to the next model would hide
    a real defect behind a working fallback."""

    class _Buggy:
        name = "buggy"

        def chat(self, messages, *, tools=None):
            raise KeyError("a real bug, not an outage")

    with pytest.raises(KeyError):
        FallbackLLM([_Buggy(), _Working()]).chat(MESSAGES)


def test_an_empty_chain_is_refused_at_construction():
    with pytest.raises(ValueError):
        FallbackLLM([])


def test_tools_are_passed_through_to_the_provider():
    seen = {}

    class _Spy:
        name = "spy"

        def chat(self, messages, *, tools=None):
            seen["tools"] = tools
            return LLMResponse(content="", model="spy")

    tools = [{"type": "function", "function": {"name": "search_catalog"}}]
    FallbackLLM([_Spy()]).chat(MESSAGES, tools=tools)

    assert seen["tools"] == tools


def test_the_default_chain_is_7b_then_3b():
    chain = get_llm_provider()
    assert [p.model for p in chain.providers] == [DEFAULT_MODEL, FALLBACK_MODEL]
    assert DEFAULT_MODEL == "qwen2.5:7b" and FALLBACK_MODEL == "qwen2.5:3b"


# --------------------------------------------------------------------------
# The real wiring — the actual local Ollama and the actual models
# --------------------------------------------------------------------------


@requires_ollama
def test_the_primary_model_really_answers():
    response = OllamaProvider(DEFAULT_MODEL).chat(
        [{"role": "user", "content": "Reply with exactly one word: hello"}]
    )
    assert response.model == DEFAULT_MODEL
    assert response.content.strip(), "the real model returned nothing"


@requires_ollama
def test_the_primary_model_really_composes_section_28s_own_worked_example():
    """Not a hypothetical: prmpt.md section 28's example, verbatim in spirit
    ("something like Atomic Habits but more philosophical, and don't
    recommend books I've already read"). Measured before this was built —
    both qwen2.5 sizes chose the right tool and extracted the exclusion. This
    keeps that from being a claim made once, in a scoping conversation."""
    tools = [{
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search the book catalogue by semantic query and optional filters",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "exclude_authors": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    }]
    response = OllamaProvider(DEFAULT_MODEL, timeout=60).chat(
        [{"role": "user", "content":
          "Find me a philosophical book similar to Atomic Habits, but nothing by James Clear."}],
        tools=tools,
    )

    assert response.tool_calls, "the model answered in prose instead of calling the tool"
    call = response.tool_calls[0]
    assert call.name == "search_catalog"
    assert "James Clear" in call.arguments.get("exclude_authors", []), (
        "it found the tool but lost the exclusion, the part that makes this a "
        "composed query rather than a plain search"
    )


@requires_ollama
def test_a_model_that_was_never_pulled_raises_llm_unavailable_not_a_crash():
    """A real failure mode, not a mocked one: Ollama answers 404 for a model
    that does not exist on this instance (verified before writing this)."""
    with pytest.raises(LLMUnavailable) as exc_info:
        OllamaProvider("definitely-not-a-real-model-xyz").chat(MESSAGES)

    assert "404" in str(exc_info.value) or "not found" in str(exc_info.value)


@requires_ollama
def test_a_dead_primary_really_falls_back_to_the_real_secondary():
    """The whole chain, live: a primary that genuinely fails (no such model)
    followed by a real, working qwen2.5:3b."""
    chain = FallbackLLM([
        OllamaProvider("definitely-not-a-real-model-xyz"),
        OllamaProvider(FALLBACK_MODEL),
    ])
    response = chain.chat([{"role": "user", "content": "Reply with exactly one word: hello"}])

    assert response.model == FALLBACK_MODEL, (
        "the chain returned without the fallback actually answering"
    )
    assert response.content.strip()


def test_an_unreachable_ollama_is_llm_unavailable_not_a_stack_trace():
    """Nothing is listening on this port. A connection refusal is the most
    likely real-world failure (Ollama not started), and must reach the caller
    as the one specific exception, never a raw URLError.

    Found to be environment-dependent in a way worth recording: on some
    machines/network stacks, port 1 with nothing listening answers
    `ConnectionRefusedError` (wrapped in `URLError`); on this one, it
    answered `ConnectionResetError` instead — a *different* exception,
    raised later (while reading the response, not while connecting), that
    the handler below did not catch until this was found. That regression
    is pinned deterministically in
    `test_a_connection_reset_mid_response_is_also_llm_unavailable`, rather
    than relying on this test's port continuing to fail the same way on
    whatever machine runs it next.
    """
    with pytest.raises(LLMUnavailable):
        OllamaProvider(DEFAULT_MODEL, base_url="http://127.0.0.1:1", timeout=2).chat(MESSAGES)


def test_a_connection_reset_mid_response_is_also_llm_unavailable(monkeypatch):
    """`URLError` covers a failed *connection attempt*. It does not cover a
    connection that succeeded and was then reset while the response was
    being read — that raises a raw `ConnectionResetError` (an `OSError`
    subclass), which reached the caller as an unhandled stack trace instead
    of `LLMUnavailable` until this was fixed. Mocked rather than relying on
    a real socket reset, which is exactly the kind of environment-specific
    behaviour `test_an_unreachable_ollama_is_llm_unavailable_not_a_stack_trace`'s
    own docstring found not to be portable.
    """
    def _reset(*_a, **_k):
        raise ConnectionResetError("[WinError 10054] connection reset by peer")

    monkeypatch.setattr(llm._NO_PROXY_OPENER, "open", _reset)
    with pytest.raises(LLMUnavailable):
        OllamaProvider(DEFAULT_MODEL).chat(MESSAGES)


def test_a_fully_unreachable_chain_raises_llm_unavailable():
    """Both tiers down at once — Ollama itself gone. Section 12's floor: the
    caller gets one catchable exception and can fall back to whatever
    deterministic behaviour it has, rather than the app hard-crashing."""
    chain = FallbackLLM([
        OllamaProvider(DEFAULT_MODEL, base_url="http://127.0.0.1:1", timeout=2),
        OllamaProvider(FALLBACK_MODEL, base_url="http://127.0.0.1:1", timeout=2),
    ])
    with pytest.raises(LLMUnavailable):
        chain.chat(MESSAGES)


# -- concurrency: how many requests may be in the model at once ------------
#
# OI-5. A per-caller rate limit bounds how fast one address can ask. It does
# not bound how many addresses ask at once, and every one of those requests
# drives up to MAX_STEPS round trips against one model on one GPU.

import threading  # noqa: E402


@pytest.fixture
def slots(monkeypatch):
    """A one-slot, no-wait semaphore, so saturation is reachable in a test
    without sleeping through the real queue wait."""
    monkeypatch.setattr(llm, "MAX_CONCURRENCY", 1)
    monkeypatch.setattr(llm, "QUEUE_WAIT", 0.05)
    monkeypatch.setattr(llm, "_slots", threading.BoundedSemaphore(1))


def test_a_request_inside_capacity_gets_a_slot(slots):
    with llm.slot():
        pass  # must not raise


def test_a_request_past_capacity_is_told_the_model_is_unavailable(slots):
    """Not a new exception type. Saturation and an outage mean the same thing
    to the caller — this tier is not answering — and `ChatbotEngine` already
    falls back to the classifier on exactly this."""
    with llm.slot():
        with pytest.raises(llm.LLMUnavailable):
            with llm.slot():
                pass


def test_the_slot_is_released_when_the_work_raises(slots):
    """The failure that would brick the endpoint permanently: a slot leaked
    on the error path means capacity falls by one for every failed request
    until nothing gets through, and the symptom — everything falls back to
    the classifier — looks like Ollama being down."""
    with pytest.raises(ValueError):
        with llm.slot():
            raise ValueError("the tool loop blew up")

    with llm.slot():
        pass  # capacity came back


def test_capacity_is_actually_shared_across_threads(slots):
    """A per-thread guard would count to one in each worker and cap nothing,
    which is the whole point on a threadpool server."""
    held = threading.Event()
    release = threading.Event()
    refused = []

    def hold():
        with llm.slot():
            held.set()
            release.wait(2)

    worker = threading.Thread(target=hold)
    worker.start()
    assert held.wait(2), "the holding thread never acquired"
    try:
        with llm.slot():
            refused.append(False)
    except llm.LLMUnavailable:
        refused.append(True)
    finally:
        release.set()
        worker.join(2)

    assert refused == [True], "a second thread got in while the slot was held"


def test_a_waiting_caller_gets_in_once_the_slot_frees(slots):
    """The wait must be a wait, not a formality: a burst that arrives
    together should be served, not degraded."""
    import time

    def hold():
        with llm.slot():
            time.sleep(0.01)

    worker = threading.Thread(target=hold)
    worker.start()
    worker.join(2)

    with llm.slot():
        pass



# ============================================================================
# ClaudeProvider -- Claude through the official Anthropic SDK, behind the same
# LLMProvider contract OllamaProvider implements (F-22 Phase B, extended
# 2026-10-06).
#
# Same two-tier split as the Ollama section above:
#   1. The translation helpers (pure functions, no network), and the provider
#      itself against a *faked HTTP layer* -- an httpx2.MockTransport handed
#      to the real SDK, so request building, headers, response parsing and
#      error mapping are all the SDK's own code paths, not a stand-in.
#   2. The real wiring against the actual API, gated behind ANTHROPIC_API_KEY
#      and skipped (not failed) without it -- the `requires_ollama` convention,
#      because a green run with no key would prove nothing about Claude.
# ============================================================================

import anthropic  # noqa: E402
import config  # noqa: E402
import httpx2  # noqa: E402

from services.providers.llm import (  # noqa: E402
    SERVER_SIDE_FALLBACK_BETA,
    _anthropic_messages,
    _anthropic_tools,
    _from_anthropic_response,
    _replayable,
)

requires_claude_key = pytest.mark.skipif(
    not config.ANTHROPIC_API_KEY,
    reason="ANTHROPIC_API_KEY not set",
)

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_catalog",
        "description": "Search the book catalogue",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


# -- translation: OpenAI/Ollama shape <-> Anthropic's ------------------------


def test_a_system_message_becomes_the_top_level_system_field():
    """`librarian.py` always sends one first; Anthropic takes it top-level."""
    system, out = _anthropic_messages([
        {"role": "system", "content": "You are a librarian."},
        {"role": "user", "content": "hi"},
    ])
    assert system == "You are a librarian."
    assert out == [{"role": "user", "content": "hi"}]


def test_multiple_system_messages_are_joined():
    system, _ = _anthropic_messages([
        {"role": "system", "content": "first"},
        {"role": "system", "content": "second"},
        {"role": "user", "content": "hi"},
    ])
    assert system == "first\n\nsecond"


def test_plain_user_and_assistant_turns_pass_through_as_strings():
    _, out = _anthropic_messages([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "thanks"},
    ])
    assert out == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "thanks"},
    ]


def test_a_native_turn_is_replayed_verbatim_with_its_thinking_and_real_ids():
    """The reason `LLMResponse.native` exists. Anthropic: thinking blocks go
    back *unchanged* to continue a tool loop -- dropping or editing them
    "breaks the turn". The tool result must answer the *real* tool_use id."""
    native = [
        {"type": "thinking", "thinking": "", "signature": "sig-abc"},
        {"type": "tool_use", "id": "toolu_real_1", "name": "search_catalog",
         "input": {"query": "x"}},
    ]
    _, out = _anthropic_messages([
        {"role": "user", "content": "find a book"},
        {"role": "assistant", "content": "", "native": native,
         "tool_calls": [{"function": {"name": "search_catalog", "arguments": {"query": "x"}}}]},
        {"role": "tool", "tool_name": "search_catalog", "content": '{"ok": true}'},
    ])

    assert out[1] == {"role": "assistant", "content": native}, (
        "the native turn must be sent exactly as the model produced it"
    )
    assert out[2]["content"][0]["tool_use_id"] == "toolu_real_1"


def test_without_native_a_tool_call_gets_a_synthesised_id():
    """The fallback path: history with no native turn (built by hand). No
    thinking block exists to lose, so this is valid on its own."""
    _, out = _anthropic_messages([
        {"role": "user", "content": "find a book"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "search_catalog", "arguments": {"query": "x"}}}]},
        {"role": "tool", "tool_name": "search_catalog", "content": '{"ok": true}'},
    ])
    [block] = out[1]["content"]
    assert block["type"] == "tool_use" and block["input"] == {"query": "x"}
    assert out[2]["content"][0]["tool_use_id"] == block["id"]


def test_several_tool_results_are_matched_by_order_and_batched():
    """The structural rule Anthropic enforces: every tool_use from one turn
    answered inside a *single* following user message."""
    _, out = _anthropic_messages([
        {"role": "user", "content": "do two things"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "a", "arguments": {"x": 1}}},
            {"function": {"name": "b", "arguments": {"y": 2}}},
        ]},
        {"role": "tool", "tool_name": "a", "content": "result-a"},
        {"role": "tool", "tool_name": "b", "content": "result-b"},
    ])

    assert len(out) == 3, "the two results must land in one user message, not two"
    id_a, id_b = (b["id"] for b in out[1]["content"])
    assert id_a != id_b
    assert {b["tool_use_id"]: b["content"] for b in out[2]["content"]} == {
        id_a: "result-a", id_b: "result-b",
    }


def test_a_tool_message_with_nothing_to_answer_raises():
    """A malformed history is a caller bug, raised -- not dropped or guessed."""
    with pytest.raises(ValueError, match="tool_use"):
        _anthropic_messages([
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_name": "x", "content": "orphaned"},
        ])


def test_tool_definitions_translate_to_input_schema():
    [translated] = _anthropic_tools([SEARCH_TOOL])
    assert translated == {
        "name": "search_catalog",
        "description": "Search the book catalogue",
        "input_schema": SEARCH_TOOL["function"]["parameters"],
    }


def test_no_tools_is_omitted_not_an_empty_list():
    assert _anthropic_tools(None) is None
    assert _anthropic_tools([]) is None


# -- the echo rules -----------------------------------------------------------


def test_a_normal_turn_is_stored_whole_thinking_included():
    turn = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "t1", "name": "search_catalog", "input": {}},
    ]
    assert _replayable(turn) == turn


def test_after_a_server_side_fallback_the_declined_models_blocks_are_dropped():
    """Anthropic's echo rule for a mid-output fallback: omit thinking and
    tool_use blocks before the final `fallback` marker, keep text and
    everything after it. The marker itself is dropped so the stored turn
    stays valid on the non-beta endpoint the fallback tier uses."""
    turn = [
        {"type": "thinking", "thinking": "", "signature": "declined"},
        {"type": "text", "text": "partial "},
        {"type": "tool_use", "id": "t-declined", "name": "x", "input": {}},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "thinking", "thinking": "", "signature": "rescuer"},
        {"type": "tool_use", "id": "t-kept", "name": "search_catalog", "input": {}},
    ]
    assert _replayable(turn) == [
        {"type": "text", "text": "partial "},
        {"type": "thinking", "thinking": "", "signature": "rescuer"},
        {"type": "tool_use", "id": "t-kept", "name": "search_catalog", "input": {}},
    ]


# -- reading a response -------------------------------------------------------


def test_a_response_is_read_by_block_type_not_position():
    """With adaptive thinking a turn often opens with a thinking block whose
    text is empty under the default display. It is not the reply."""
    response = _from_anthropic_response(
        [{"type": "thinking", "thinking": "", "signature": "s"},
         {"type": "text", "text": "Here is a book."}],
        "claude-sonnet-5-5",
    )
    assert response.content == "Here is a book."
    assert response.tool_calls == []
    assert [b["type"] for b in response.native] == ["thinking", "text"]


def test_prose_and_a_tool_call_in_one_turn_are_both_kept():
    response = _from_anthropic_response(
        [{"type": "text", "text": "Let me check: "},
         {"type": "tool_use", "id": "toolu_1", "name": "search_catalog", "input": {"q": "x"}}],
        "claude-sonnet-5-5",
    )
    assert response.content == "Let me check: "
    [call] = response.tool_calls
    assert (call.id, call.name, call.arguments) == ("toolu_1", "search_catalog", {"q": "x"})


def test_a_declined_models_tool_call_is_never_offered_to_the_caller():
    """A tool_use the echo rules drop must not become a ToolCall: its result
    would answer a call the stored turn no longer contains."""
    response = _from_anthropic_response(
        [{"type": "tool_use", "id": "t-declined", "name": "x", "input": {}},
         {"type": "fallback"},
         {"type": "text", "text": "rescued"}],
        "claude-opus-5-5",
    )
    assert response.tool_calls == []
    assert response.content == "rescued"


# -- ClaudeProvider through the real SDK, on a faked HTTP layer ---------------


def _message(content, *, stop_reason="end_turn", model="claude-sonnet-5-5", **extra):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5}, **extra,
    }


class _FakeAnthropic:
    """An httpx2 transport standing in for api.anthropic.com. Records every
    request the SDK makes and answers each with the next scripted reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx2.Response):
            return reply
        return httpx2.Response(200, json=reply)

    def provider(self, model=CLAUDE_MODEL, **kwargs):
        kwargs.setdefault("api_key", "sk-test")
        kwargs.setdefault("max_retries", 0)
        transport = httpx2.MockTransport(self.handler)
        return ClaudeProvider(
            model, http_client=anthropic.DefaultHttpxClient(transport=transport), **kwargs
        )

    def body(self, i=0):
        return json.loads(self.requests[i].content)


def test_claude_provider_sends_the_key_version_and_a_thinking_sized_max_tokens():
    fake = _FakeAnthropic(_message([{"type": "text", "text": "hi"}]))
    response = fake.provider().chat([{"role": "user", "content": "hello"}])

    request = fake.requests[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-test"
    assert request.headers["anthropic-version"]
    assert fake.body()["max_tokens"] == llm.CLAUDE_MAX_TOKENS >= 16000, (
        "thinking counts toward max_tokens; a reply-sized cap truncates mid-thought"
    )
    assert response.content == "hi"


def test_claude_provider_sends_system_and_tools_in_anthropics_shape():
    fake = _FakeAnthropic(_message([{"type": "text", "text": "ok"}]))
    fake.provider().chat(
        [{"role": "system", "content": "You are a librarian."},
         {"role": "user", "content": "find a book"}],
        tools=[SEARCH_TOOL],
    )
    body = fake.body()
    assert body["system"] == "You are a librarian."
    assert body["messages"] == [{"role": "user", "content": "find a book"}]
    assert body["tools"][0]["input_schema"] == SEARCH_TOOL["function"]["parameters"]
    assert "thinking" not in body, (
        "no thinking parameter: adaptive by default on Sonnet 5.5, off on Haiku 4.5, "
        "so one request shape is valid for both tiers"
    )


def test_a_tool_loop_sends_the_thinking_block_back_through_the_real_sdk():
    """End to end through the SDK: step 1's thinking block and real tool_use
    id come back on step 2's request exactly as the model produced them,
    via the same message shape `librarian.py` builds."""
    thinking = {"type": "thinking", "thinking": "", "signature": "sig-step-1"}
    tool_use = {"type": "tool_use", "id": "toolu_real_1", "name": "search_catalog",
                "input": {"query": "mystery"}}
    fake = _FakeAnthropic(
        _message([thinking, tool_use], stop_reason="tool_use"),
        _message([{"type": "text", "text": "Try The Moonstone."}]),
    )
    provider = fake.provider()

    messages = [{"role": "user", "content": "a mystery novel, please"}]
    first = provider.chat(messages, tools=[SEARCH_TOOL])
    [call] = first.tool_calls
    messages.append({
        "role": "assistant", "content": first.content, "native": first.native,
        "tool_calls": [{"function": {"name": call.name, "arguments": call.arguments}}],
    })
    messages.append({"role": "tool", "tool_name": call.name, "content": '{"items": []}'})
    second = provider.chat(messages, tools=[SEARCH_TOOL])

    sent = fake.body(1)["messages"]
    assert sent[1] == {"role": "assistant", "content": [thinking, tool_use]}, (
        "the thinking block did not go back unchanged"
    )
    assert sent[2]["content"][0]["tool_use_id"] == "toolu_real_1"
    assert second.content == "Try The Moonstone."


def test_the_primary_asks_for_server_side_refusal_fallback():
    fake = _FakeAnthropic(_message([{"type": "text", "text": "ok"}]))
    fake.provider(refusal_fallback=True).chat([{"role": "user", "content": "hi"}])

    request = fake.requests[0]
    assert SERVER_SIDE_FALLBACK_BETA in request.headers.get("anthropic-beta", "")
    assert fake.body()["fallbacks"] == "default"


def test_the_fallback_tier_does_not():
    fake = _FakeAnthropic(_message([{"type": "text", "text": "ok"}]))
    fake.provider(CLAUDE_FALLBACK_MODEL).chat([{"role": "user", "content": "hi"}])

    assert "anthropic-beta" not in fake.requests[0].headers
    assert "fallbacks" not in fake.body()


def test_the_model_that_actually_served_is_reported():
    """A server-side fallback can serve the reply from another model; the
    response's own `model` is the honest answer, not the one requested."""
    fake = _FakeAnthropic(_message([{"type": "text", "text": "ok"}], model="claude-opus-5-5"))
    response = fake.provider(refusal_fallback=True).chat([{"role": "user", "content": "hi"}])
    assert response.model == "claude-opus-5-5"


def test_a_refusal_is_llm_unavailable_not_an_empty_answer():
    """HTTP 200, `stop_reason: "refusal"`, nothing usable. Returned as-is it
    would read as the Librarian having nothing to say."""
    fake = _FakeAnthropic(_message(
        [], stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "x"},
    ))
    with pytest.raises(LLMUnavailable, match="refused"):
        fake.provider().chat([{"role": "user", "content": "hi"}])


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 529])
def test_an_http_error_is_llm_unavailable(status):
    fake = _FakeAnthropic(httpx2.Response(
        status, json={"type": "error", "error": {"type": "x", "message": f"status {status}"}}
    ))
    with pytest.raises(LLMUnavailable, match=str(status)):
        fake.provider().chat([{"role": "user", "content": "hi"}])


def test_a_connection_failure_is_llm_unavailable():
    fake = _FakeAnthropic(httpx2.ConnectError("connection refused"))
    with pytest.raises(LLMUnavailable):
        fake.provider().chat([{"role": "user", "content": "hi"}])


def test_one_retry_then_the_next_tier():
    """One quick retry absorbs a transient 529, but no more: past it,
    `FallbackLLM` moves on rather than a caller sitting through the SDK's
    full backoff ladder while holding a model slot."""
    overloaded = httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
    fake = _FakeAnthropic(overloaded, overloaded, _message([{"type": "text", "text": "late"}]))
    with pytest.raises(LLMUnavailable):
        fake.provider(max_retries=1).chat([{"role": "user", "content": "hi"}])
    assert len(fake.requests) == 2, "expected exactly one retry"


def test_no_key_is_llm_unavailable_and_sends_nothing():
    fake = _FakeAnthropic()
    with pytest.raises(LLMUnavailable, match="ANTHROPIC_API_KEY"):
        fake.provider(api_key="").chat([{"role": "user", "content": "hi"}])
    assert fake.requests == []


def test_a_malformed_history_is_a_bug_not_unavailability():
    """`test_a_non_llm_error_is_not_swallowed_as_unavailability`'s principle:
    a defect must not fall through to the next tier looking like an outage."""
    fake = _FakeAnthropic()
    with pytest.raises(ValueError):
        fake.provider().chat([{"role": "tool", "tool_name": "x", "content": "orphan"}])
    assert fake.requests == []


def test_the_defaults_degrade_rather_than_wait():
    """Far below the SDK's own defaults (10 minutes, 2 retries) on purpose:
    a model slot is held across the whole Librarian tool loop."""
    provider = ClaudeProvider(CLAUDE_MODEL, api_key="k")
    assert provider.timeout <= 60 and provider.max_retries <= 1


# -- selection ------------------------------------------------------------------


def test_with_a_key_the_chain_is_claude_with_refusal_fallback_on_the_primary(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-configured")
    primary, fallback = get_llm_provider().providers
    assert (type(primary), type(fallback)) == (ClaudeProvider, ClaudeProvider)
    assert (primary.model, fallback.model) == (CLAUDE_MODEL, CLAUDE_FALLBACK_MODEL)
    assert primary.refusal_fallback and not fallback.refusal_fallback


def test_the_model_ids_are_exactly_anthropics():
    """Complete as written, no date suffix appended."""
    assert CLAUDE_MODEL == "claude-sonnet-5-5"
    assert CLAUDE_FALLBACK_MODEL == "claude-haiku-4-5"


def test_with_no_key_the_chain_is_ollama_unchanged(monkeypatch):
    """Local dev, exactly as before Claude support existed."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    chain = get_llm_provider()
    assert all(isinstance(p, OllamaProvider) for p in chain.providers)
    assert [p.model for p in chain.providers] == [DEFAULT_MODEL, FALLBACK_MODEL]


# --------------------------------------------------------------------------
# The real wiring -- the actual Anthropic API, gated behind a configured key
# --------------------------------------------------------------------------


@requires_claude_key
def test_claude_really_answers():
    response = ClaudeProvider(CLAUDE_MODEL).chat(
        [{"role": "user", "content": "Reply with exactly one word: hello"}]
    )
    assert response.content.strip(), "the real model returned nothing"


@requires_claude_key
def test_claude_really_composes_section_28s_own_worked_example():
    """The worked example already proven against qwen2.5 above: the right
    tool, with the exclusion extracted."""
    tools = [{
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search the book catalogue by semantic query and optional filters",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "exclude_authors": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    }]
    response = ClaudeProvider(CLAUDE_MODEL).chat(
        [{"role": "user", "content":
          "Find me a philosophical book similar to Atomic Habits, but nothing by James Clear."}],
        tools=tools,
    )

    assert response.tool_calls, "the model answered in prose instead of calling the tool"
    call = response.tool_calls[0]
    assert call.name == "search_catalog"
    assert "James Clear" in call.arguments.get("exclude_authors", []), (
        "it found the tool but lost the exclusion"
    )


@requires_claude_key
def test_claude_rejects_an_invalid_key_as_llm_unavailable():
    with pytest.raises(LLMUnavailable):
        ClaudeProvider(CLAUDE_MODEL, api_key="sk-definitely-invalid-xyz").chat(
            [{"role": "user", "content": "hello"}]
        )


@requires_claude_key
def test_a_real_tool_loop_round_trips_its_thinking_blocks():
    """The proof the faked tests cannot give: the real API accepts the
    history this translation builds, native thinking blocks included, on the
    second step of a tool loop."""
    provider = ClaudeProvider(CLAUDE_MODEL)
    messages = [{"role": "user", "content": "Search the catalogue for a mystery novel."}]

    first = provider.chat(messages, tools=[SEARCH_TOOL])
    assert first.tool_calls, "expected a tool call to send a result back for"
    call = first.tool_calls[0]
    messages.append({
        "role": "assistant", "content": first.content, "native": first.native,
        "tool_calls": [{"function": {"name": call.name, "arguments": call.arguments}}],
    })
    messages.append({
        "role": "tool", "tool_name": call.name,
        "content": json.dumps({"ok": True, "items": [{"title": "The Moonstone"}]}),
    })

    second = provider.chat(messages, tools=[SEARCH_TOOL])
    assert second.content.strip() or second.tool_calls
