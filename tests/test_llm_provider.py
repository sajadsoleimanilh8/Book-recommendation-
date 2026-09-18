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

import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.providers.llm import (  # noqa: E402
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    FallbackLLM,
    LLMResponse,
    LLMUnavailable,
    OllamaProvider,
    get_llm_provider,
)


def ollama_reachable() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3):
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
    as the one specific exception, never a raw URLError."""
    with pytest.raises(LLMUnavailable):
        OllamaProvider(DEFAULT_MODEL, base_url="http://127.0.0.1:1", timeout=2).chat(MESSAGES)


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
