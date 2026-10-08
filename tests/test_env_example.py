"""The production env template stays in step with the code that reads it.

`.env.production.example` is the only record of what a production host must
set — `.env` itself is gitignored. A template written once and left alone
drifts: the first version of this one missed seven variables the code
already read (`WEB_CONCURRENCY`, the guard that stops a second worker
silently breaking job polls, among them), found only by inventorying the
source instead of remembering it. So the inventory is the test.

Read with the AST, not a regex: several reads span lines
(`os.getenv(\\n    "MODEL_VERSION", ...)`) or go through `config._bool`, and a
regex missed exactly those on the first pass.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.production.example"

# Read only by the test suite or by maintenance commands, never by a deploy.
TEST_ONLY = {"DIGIKITAB_TEST_DB", "GOLDEN_REGENERATE", "REQUIRE_DATABASE"}


def _variables_the_code_reads() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in (ROOT / "backend").rglob("*.py"):
        if "alembic" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if not re.fullmatch(r"[A-Z][A-Z0-9_]+", first.value):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            # os.getenv(...), os.environ.get(...), config._bool(...)
            if name in {"getenv", "get", "_bool"}:
                found.setdefault(first.value, set()).add(path.name)

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for var in re.findall(r"\$\{([A-Z][A-Z0-9_]+)", compose):
        found.setdefault(var, set()).add("docker-compose.yml")
    return found


def _documented() -> set[str]:
    """Every NAME= in the template, set or commented out — a commented line
    still documents the variable and its default."""
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))


def _active_assignments() -> dict[str, str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Z][A-Z0-9_]+)=(.*)$", text, re.M))


def test_every_variable_the_code_reads_is_in_the_template():
    reads = _variables_the_code_reads()
    missing = sorted(set(reads) - _documented() - TEST_ONLY)
    assert not missing, (
        "read by the code but absent from .env.production.example: "
        + ", ".join(f"{v} ({', '.join(sorted(reads[v]))})" for v in missing)
    )


def test_the_inventory_actually_finds_variables():
    """A scanner that finds nothing makes the test above pass vacuously."""
    reads = _variables_the_code_reads()
    for known in ("POSTGRES_PASSWORD", "JWT_SECRET", "ANTHROPIC_API_KEY",
                  "TRUSTED_PROXY_CIDR", "MODEL_VERSION", "DEBUG"):
        assert known in reads, f"the scanner missed {known}"


def test_no_development_default_survives_into_the_production_template():
    """A production value equal to a development default is exactly what
    `production_problems()` refuses to boot with — so the template must not
    hand one over as a value to copy."""
    from core import config

    active = _active_assignments()
    leaked = sorted(
        name for name, value in active.items()
        if value.strip() in config.DEV_DEFAULT_PASSWORDS
    )
    assert not leaked, f"development default values in the production template: {leaked}"


def test_the_production_template_declares_production():
    active = _active_assignments()
    assert active.get("ENV") == "production"
    assert active.get("DEBUG", "").lower() in {"false", "0", "no", "off"}


def test_jwt_secret_is_required_and_comes_with_the_generation_command():
    """The command must be the one config.py's own error message gives, so
    the two never disagree about how a secret is made."""
    text = TEMPLATE.read_text(encoding="utf-8")
    config_source = (ROOT / "backend" / "core" / "config.py").read_text(encoding="utf-8")

    assert "secrets.token_urlsafe(48)" in config_source
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("JWT_SECRET="))
    above = "\n".join(lines[max(0, index - 8):index])
    assert 'python -c "import secrets;print(secrets.token_urlsafe(48))"' in above
    assert lines[index] == "JWT_SECRET=", "the template must not ship a secret value"


def test_the_two_new_settings_are_documented_with_their_safe_defaults():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" in text
    assert "TRUSTED_PROXY_CIDR=" in text
    assert "--proxy-headers" in text and "--forwarded-allow-ips" in text
