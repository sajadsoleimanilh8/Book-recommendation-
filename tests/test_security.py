"""Regression tests for the vulnerabilities closed in PR 1.

These are the tests that must never be deleted. Each one fires the actual
attack from the Phase 0 audit and asserts it fails. If any of these start
passing the exploit, the vulnerability is back.

Deliberately dependency-light: these exercise the validation boundary and
the path-derivation logic, not a live server, so they run without
scikit-learn / gtts / plyer installed. See tests/README.md.
"""

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

BACKEND = Path(__file__).resolve().parents[1] / "backend"


# --------------------------------------------------------------------------
# F-04 — unauthenticated arbitrary file overwrite
# --------------------------------------------------------------------------

class AudiobookRequest(BaseModel):
    """Mirror of main.AudiobookRequest.

    Duplicated rather than imported because importing main pulls in engine,
    which needs scikit-learn. test_schema_matches_source below guards the
    duplication against drift.
    """

    model_config = ConfigDict(extra="forbid")

    book_id: int = Field(..., ge=1)
    book_name: str = Field("Animal Farm", min_length=1, max_length=300)
    language: str = Field("en", min_length=2, max_length=5)


ATTACK_PAYLOADS = [
    pytest.param({"book_name": "x", "output_file": "../server.js"}, id="original-exploit"),
    pytest.param({"book_id": 1, "output_file": "../../server.js"}, id="traversal-with-valid-id"),
    pytest.param({"book_id": 1, "output_file": "/etc/passwd"}, id="absolute-path"),
    pytest.param({"book_id": 1, "output_file": "audiobook.mp3"}, id="even-the-old-default"),
]


@pytest.mark.parametrize("payload", ATTACK_PAYLOADS)
def test_output_file_is_rejected(payload):
    """output_file must fail closed, never be silently ignored.

    A client that still sends it gets a 422 and a clear error, rather than a
    success response implying the server honoured a path it did not.
    """
    with pytest.raises(ValidationError):
        AudiobookRequest(**payload)


@pytest.mark.parametrize("book_id", [0, -1, -999])
def test_non_positive_book_id_rejected(book_id):
    with pytest.raises(ValidationError):
        AudiobookRequest(book_id=book_id)


def test_legitimate_request_still_works():
    """The fix must not break the endpoint's actual purpose."""
    req = AudiobookRequest(book_id=42, book_name="Animal Farm")
    assert req.book_id == 42
    assert req.language == "en"


def test_schema_matches_source():
    """Guard the duplication above against drift in main.py."""
    src = (BACKEND / "main.py").read_text(encoding="utf-8")
    body = src.split("class AudiobookRequest")[1].split("\nclass ")[0]
    # Comments in that block legitimately mention output_file to explain why
    # it was removed; only real field declarations matter here.
    code = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    assert "output_file" not in code, (
        "output_file has reappeared on AudiobookRequest — F-04 may be reintroduced"
    )
    assert 'model_config = ConfigDict(extra="forbid")' in src, (
        "extra='forbid' removed from AudiobookRequest — unknown fields would "
        "be silently ignored again"
    )


# --------------------------------------------------------------------------
# F-04 defence in depth — server-side path derivation
# --------------------------------------------------------------------------

AUDIO_DIR = (BACKEND / "audio_outputs").resolve()


def derive_output_path(book_id):
    """Replica of the guard in AudiobookEngine.generate.

    Kept in sync by test_engine_guard_present below.
    """
    try:
        book_id = int(book_id)
    except (TypeError, ValueError):
        return None
    if book_id < 1:
        return None
    path = (AUDIO_DIR / f"{book_id}.mp3").resolve()
    if path.parent != AUDIO_DIR:
        return None
    return path


@pytest.mark.parametrize(
    "hostile",
    ["../server.js", "../../server.js", "1/../../x", "..", "/etc/passwd", None, "abc", -5, 0],
)
def test_path_derivation_rejects_hostile_book_ids(hostile):
    """Even bypassing pydantic entirely, no write escapes audio_outputs."""
    assert derive_output_path(hostile) is None


def test_path_derivation_accepts_valid_id():
    path = derive_output_path(42)
    assert path is not None
    assert path.parent == AUDIO_DIR
    assert path.name == "42.mp3"


def test_engine_guard_present():
    """The engine must derive its own path, never accept one."""
    src = (BACKEND / "engine.py").read_text(encoding="utf-8")
    generate = src.split("def generate(")[1].split("\n    @staticmethod")[0]
    assert "output_file: str" not in generate, "generate() accepts a caller-supplied path again"
    assert "AUDIO_DIR" in generate, "generate() no longer derives its path from AUDIO_DIR"
    assert "os.remove(output_file)" not in src, "the unguarded os.remove is back"


# --------------------------------------------------------------------------
# F-05 — Express path traversal
# --------------------------------------------------------------------------

def test_traversable_express_routes_are_gone():
    src = (Path(__file__).resolve().parents[1] / "server.js").read_text(encoding="utf-8")
    for route in ("'/css/:file'", "'/js/:file'"):
        assert route not in src, (
            f"{route} reintroduced — req.params.file reaches path.join() and "
            "Express decodes params, so ..%2f traverses out of the directory"
        )
    assert "express.static" in src, "static serving removed along with the vulnerable routes"


# --------------------------------------------------------------------------
# F-06 — concurrent generation corruption
# --------------------------------------------------------------------------

def test_temp_files_are_isolated_per_run():
    src = (BACKEND / "engine.py").read_text(encoding="utf-8")
    assert 'f"_tmp_{i}.mp3"' not in src, (
        "temp fragments are being written to the CWD again — concurrent "
        "generations will corrupt each other"
    )
    assert "mkdtemp" in src, "per-run temp directory removed"


# --------------------------------------------------------------------------
# Secret containment
# --------------------------------------------------------------------------

def test_env_is_gitignored():
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "\n.env\n" in ignored or ignored.startswith(".env\n"), (
        ".env is not gitignored — GOOGLE_BOOKS_API_KEY could be committed"
    )


# Keys allowed to carry a value in .env.example: non-secret defaults, plus
# the local docker-compose dev credentials, which must match compose's own
# defaults to be useful and grant nothing beyond a throwaway container.
ENV_EXAMPLE_MAY_HAVE_VALUES = {
    "HOST", "PORT", "LOG_LEVEL", "ENV", "DEBUG",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST",
    "POSTGRES_PORT", "POSTGRES_DB", "REDIS_URL",
    "CATALOGUE_LANGUAGES", "CATALOGUE_PATH", "AUDIO_DIR",
    "BOOK_LOAD_LIMIT", "CORS_ORIGINS", "DB_ECHO",
    "JWT_EXPIRE_MINUTES",
}

# These are real secrets. They must ALWAYS be blank in the committed example.
ENV_EXAMPLE_MUST_BE_BLANK = {"JWT_SECRET", "GOOGLE_BOOKS_API_KEY", "DATABASE_URL"}


def test_env_example_carries_no_real_secrets():
    root = Path(__file__).resolve().parents[1]
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()

        if key in ENV_EXAMPLE_MUST_BE_BLANK:
            assert value == "", f"{key} must be blank in .env.example — it is a real secret"
        else:
            assert key in ENV_EXAMPLE_MAY_HAVE_VALUES, (
                f"{key} is new in .env.example. Add it to "
                "ENV_EXAMPLE_MAY_HAVE_VALUES if it is a non-secret default, "
                "or to ENV_EXAMPLE_MUST_BE_BLANK if it is a secret."
            )


def test_production_refuses_a_generated_jwt_secret():
    """A per-process secret in production would break tokens across workers
    and log everyone out on every restart, silently."""
    src = (BACKEND / "config.py").read_text(encoding="utf-8")
    assert "IS_PRODUCTION" in src and "JWT_SECRET must be set" in src, (
        "config.py no longer refuses to boot without JWT_SECRET in production"
    )
