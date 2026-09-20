"""Chat conversation history — F-22 Phase D.

Server-side in Redis (already in the stack for the rate limiter), not sent by
the client: a browser that carries the whole transcript makes every request
bigger as the conversation grows, and lets a client rewrite what it claims to
have been told.

Whose history is whose
----------------------
The key is the load-bearing decision here, not the storage.

    signed in   ->  the account id, and nothing else
    anonymous   ->  an opaque token this module mints and the client echoes

`ChatbotRequest.user_id` is **not** usable as a key. It is a free-text profile
string any caller can set to any value (`"guest"` by default), so keying a
private transcript on it would let anyone read anyone else's conversation by
guessing a name — F-07's exact shape, which the authorization sweep closed for
comments, reminders and profiles. A signed-in caller's key is always derived
from their token, so a supplied `conversation_id` cannot hand them somebody
else's thread either.

The anonymous token is 128+ bits from `secrets`, so it is unguessable rather
than merely obscure, and `_valid_token` rejects anything that is not one of
ours — without that check a client could send `conversation_id="u:5"` and read
account 5's history.

What happens when Redis is down
-------------------------------
The same answer as the rate limiter: degrade, warn once, never 500. History is
a convenience; losing it costs the reader context, not the reply. The
in-process fallback is bounded (`_LOCAL_MAX`) because an unbounded dict keyed
by anything a client can mint is a memory-growth lever.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from core import config

log = logging.getLogger(__name__)

# Six turns of context. Enough for "and something shorter?" to make sense,
# small enough that the prompt stays cheap on a 4096-token local model — and
# section 13 asks for the smallest thing that works, not the largest.
MAX_MESSAGES = 12          # 6 exchanges: user + assistant each
MAX_CONTENT = 2000         # matches ChatbotRequest's own cap
TTL_SECONDS = 60 * 60      # an hour; a chat resumed a day later is a new one
_LOCAL_MAX = 500           # bounded fallback, see the module docstring
MAX_BOOK_IDS = 12          # grounded books carried to the next turn

_TOKEN_BYTES = 16
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

_redis = None
_redis_checked = False
_warned_fallback = False

_lock = threading.Lock()
_local: "OrderedDict[str, tuple[float, list[dict[str, str]]]]" = OrderedDict()


def _get_redis():
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    try:
        import redis

        client = redis.Redis.from_url(
            config.REDIS_URL, socket_timeout=0.25, decode_responses=True
        )
        client.ping()
        _redis = client
        log.info("conversation history using Redis")
    except Exception as exc:
        log.warning(f"conversation history falling back to in-process: {exc}")
        _redis = None
    return _redis


def new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _valid_token(token: Any) -> bool:
    return isinstance(token, str) and bool(_TOKEN_RE.match(token))


def resolve_key(
    account_id: Optional[int], conversation_id: Optional[str]
) -> tuple[str, str]:
    """Return `(redis_key, conversation_id_to_report_back)`.

    A signed-in caller is keyed on their account and never on a value they
    sent: that is what stops a supplied `conversation_id` from reading another
    reader's thread. An anonymous caller keeps the token they were given, or
    is minted a fresh one.
    """
    if account_id is not None:
        return f"chat:u:{account_id}", ""
    token = conversation_id if _valid_token(conversation_id) else new_token()
    return f"chat:c:{token}", token


def _trim(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return messages[-MAX_MESSAGES:]


def _local_get(key: str) -> list[dict[str, str]]:
    with _lock:
        entry = _local.get(key)
        if entry is None:
            return []
        expires, messages = entry
        if expires < time.time():
            _local.pop(key, None)
            return []
        _local.move_to_end(key)
        return list(messages)


def _local_append(key: str, turns: list[dict[str, str]]) -> None:
    global _warned_fallback
    if not _warned_fallback:
        log.warning("conversation history in-process; correct for one instance only")
        _warned_fallback = True
    with _lock:
        entry = _local.get(key)
        existing = entry[1] if entry and entry[0] >= time.time() else []
        _local[key] = (time.time() + TTL_SECONDS, _trim(existing + turns))
        _local.move_to_end(key)
        while len(_local) > _LOCAL_MAX:
            _local.popitem(last=False)


def load(key: str) -> list[dict[str, str]]:
    """Past turns, oldest first. Never raises: no history is a usable state."""
    client = _get_redis()
    if client is None:
        return _local_get(key)
    try:
        import json

        raw = client.lrange(key, -MAX_MESSAGES, -1)
        return [json.loads(item) for item in raw]
    except Exception as exc:
        log.warning(f"redis history read failed, using in-process: {exc}")
        return _local_get(key)


def append(key: str, turns: list[dict[str, Any]]) -> None:
    """Record turns. Never raises: a chat reply must not fail because the
    transcript could not be saved (section 12).

    An assistant turn may carry `book_ids`: the catalogue rows that answer was
    grounded in. They are not part of what the model is shown — they exist so
    the next turn knows those books came from a tool, and a follow-up like
    "who wrote the first one?" is not mistaken for an invention.
    """
    cleaned: list[dict[str, Any]] = []
    for t in turns:
        if t.get("role") not in ("user", "assistant"):
            continue
        content = str(t.get("content") or "")
        if not content.strip():
            continue
        turn: dict[str, Any] = {"role": t["role"], "content": content[:MAX_CONTENT]}
        ids = t.get("book_ids")
        if t["role"] == "assistant" and isinstance(ids, list):
            turn["book_ids"] = [int(i) for i in ids[:MAX_BOOK_IDS] if isinstance(i, int)]
        cleaned.append(turn)
    turns = cleaned
    if not turns:
        return

    client = _get_redis()
    if client is None:
        return _local_append(key, turns)
    try:
        import json

        pipe = client.pipeline()
        for turn in turns:
            pipe.rpush(key, json.dumps(turn, ensure_ascii=False))
        pipe.ltrim(key, -MAX_MESSAGES, -1)
        pipe.expire(key, TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        log.warning(f"redis history write failed, using in-process: {exc}")
        _local_append(key, turns)


def clear(key: str) -> None:
    client = _get_redis()
    if client is not None:
        try:
            client.delete(key)
        except Exception:
            pass
    with _lock:
        _local.pop(key, None)


def reset() -> None:
    """Drop every conversation and re-probe Redis. Tests only."""
    global _redis, _redis_checked, _warned_fallback
    with _lock:
        _local.clear()
    if _redis is not None:
        try:
            for key in _redis.scan_iter("chat:*"):
                _redis.delete(key)
        except Exception:
            pass
    _redis, _redis_checked, _warned_fallback = None, False, False
