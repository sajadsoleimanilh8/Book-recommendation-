"""The settings that decide whether this app may face a network — OI-5.

Each of these was a real finding, not a hypothetical:

  * both datastores published on 0.0.0.0, Redis with no password at all;
  * `uvicorn.run(host="0.0.0.0", ..., reload=True)` as the entrypoint;
  * `CORS_ORIGINS` defaulting to a localhost allowlist, because Express
    served the pages from a second origin;
  * an in-process job registry that quietly needs a single worker.

They are asserted here rather than left to a checklist because a checklist
is a thing you remember and a test is a thing you cannot forget.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import jobs  # noqa: E402


# -- the single-worker constraint ------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["uvicorn", "main:app", "--workers", "4"],
        ["uvicorn", "main:app", "-w", "2"],
        ["uvicorn", "main:app", "--workers=8"],
        ["gunicorn", "-w", "3", "main:app"],
    ],
)
def test_a_multi_worker_launch_is_refused(argv):
    """Without this the symptom is an audiobook job that 404s on poll
    roughly (N-1)/N of the time — which reads as a bug in synthesis, not as
    a deployment flag."""
    with pytest.raises(jobs.MultiWorkerRefused):
        jobs.check_single_worker(argv, {})


def test_web_concurrency_counts_too():
    """Several platforms set this instead of passing a flag."""
    with pytest.raises(jobs.MultiWorkerRefused):
        jobs.check_single_worker(["uvicorn", "main:app"], {"WEB_CONCURRENCY": "3"})


@pytest.mark.parametrize(
    "argv,env",
    [
        (["uvicorn", "main:app"], {}),
        (["uvicorn", "main:app", "--workers", "1"], {}),
        (["uvicorn", "main:app"], {"WEB_CONCURRENCY": "1"}),
        (["uvicorn", "main:app", "--workers", "not-a-number"], {}),
        (["uvicorn", "main:app"], {"WEB_CONCURRENCY": ""}),
        (["uvicorn", "main:app", "--workers"], {}),
    ],
)
def test_a_single_worker_launch_starts(argv, env):
    """The guard must not become a reason the app will not boot. A malformed
    value means "not a multi-worker launch I can prove", which is a warning's
    job at most, not a refusal's."""
    jobs.check_single_worker(argv, env)


def test_the_guard_runs_before_the_expensive_startup_work():
    """Refusing after each worker has loaded the catalogue and fitted the ML
    stack is a refusal that costs a minute per worker to deliver."""
    src = (BACKEND / "lifespan.py").read_text(encoding="utf-8")
    body = src.split("def startup():", 1)[1]
    assert body.index("check_single_worker()") < body.index("load_books_raw"), (
        "the worker check runs after the catalogue load"
    )


# -- what the app binds and admits -----------------------------------------


def _call(name: str):
    """The arguments of a named call in main.py, read from the parse tree.

    Not a text search: the first version of these tests grepped the source
    and failed on the comment that *describes* the old `host="0.0.0.0",
    reload=True` — prose about a fixed bug read as the bug. Parsing asks
    what the code does rather than what the file says.
    """
    import ast

    tree = ast.parse((BACKEND / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        ):
            return node
    raise AssertionError(f"no call to {name}() in main.py")


def _constants(node) -> set:
    import ast

    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant)}


def _keyword(node, name):
    return next((kw.value for kw in node.keywords if kw.arg == name), None)


def test_the_entrypoint_does_not_bind_every_interface():
    run = _call("run")
    consts = _constants(_keyword(run, "host"))

    assert "0.0.0.0" not in consts, "the default entrypoint binds every interface again"
    assert "127.0.0.1" in consts, "loopback is no longer the default"


def test_the_entrypoint_does_not_force_the_reloader():
    """`reload=True` re-executes the source tree on change. It was on
    unconditionally, including for anything calling this file directly."""
    import ast

    reload_arg = _keyword(_call("run"), "reload")

    assert not (
        isinstance(reload_arg, ast.Constant) and reload_arg.value is True
    ), "the auto-reloader is unconditionally on again"


def test_cors_admits_nobody_unless_asked():
    """The app serves its own frontend, so there is no second origin to
    permit. A default that names localhost is a setting you must remember to
    change before deploying; a default of none is not."""
    origins = _keyword(_call("add_middleware"), "allow_origins")
    consts = _constants(origins)

    assert not any(
        isinstance(c, str) and ("localhost" in c or "127.0.0.1" in c) for c in consts
    ), f"CORS still allowlists a host by default: {consts}"
    assert "CORS_ORIGINS" in consts, "the deliberate escape hatch is gone"
    assert "" in consts, "CORS_ORIGINS no longer defaults to empty"


def test_the_datastores_are_not_published_to_every_interface():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = [
        line.strip() for line in compose.splitlines()
        if line.strip().startswith('- "') and ":" in line and "CMD" not in line
    ]
    assert published, "no published ports found — this test has stopped reading them"
    for line in published:
        assert "BIND_HOST:-127.0.0.1" in line, (
            f"{line} publishes to every interface; Docker does this past the "
            "host firewall, so a private network is not the mitigation it looks like"
        )


def test_redis_requires_a_password():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--requirepass" in compose, (
        "Redis is unauthenticated again — it holds every conversation "
        "transcript and every rate-limit counter"
    )


def test_the_app_and_compose_agree_on_the_redis_password():
    """The failure this catches is silent: a mismatch makes every Redis call
    fail, and both the limiter and the history store are built to degrade
    quietly rather than crash, so the app would keep working with per-process
    rate limits and nobody would be told."""
    from core import config

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${REDIS_PASSWORD:-digikitab_dev}" in compose
    assert config.REDIS_PASSWORD, "the app sends no password"
    assert f":{config.REDIS_PASSWORD}@" in config.REDIS_URL, (
        "the password is set but not carried in the URL the client uses"
    )


# -- declaring production is enough to be told what is wrong ---------------


def test_production_refuses_the_development_credentials():
    from core import config

    problems = config.production_problems()
    assert any("POSTGRES_PASSWORD" in p for p in problems)
    assert any("REDIS_PASSWORD" in p for p in problems)


def test_health_reports_the_blockers_rather_than_hiding_them(fitted_app):
    """Asserted against the endpoint, not `config.summary()`.

    The first version of this checked the helper. It passed while `/health`
    never called `config.summary()` at all, so the list was unreachable and
    the claim that /health reported it was simply false — the same shape as
    "a correct limiter the route never calls". Caught by probing the running
    app rather than by reading either file."""
    _, client = fitted_app
    body = client.get("/api/health").json()

    assert "production_blockers" in body, "/health does not report them"
    assert any("POSTGRES_PASSWORD" in p for p in body["production_blockers"]), (
        f"the blockers are not the real ones: {body['production_blockers']}"
    )


def test_the_health_summary_never_carries_the_redis_password():
    from core import config

    assert config.REDIS_PASSWORD not in str(config.summary()), (
        "the Redis password leaks through /health"
    )


# -- what an error tells a stranger ----------------------------------------


def test_an_unexpected_failure_does_not_hand_back_its_details(monkeypatch):
    """Five handlers returned `f"...: {str(e)}"` for a bare `Exception`. What
    that actually returns depends on which one fired: a SQLAlchemy error
    carries the failing SQL and often the connection string, a file error an
    absolute path, an httpx error the upstream URL."""
    import json

    import main
    from core import config

    leaky = Exception(
        'connection to server at "10.0.0.5", port 5432 failed: password '
        'authentication failed for user "digikitab"'
    )

    monkeypatch.setattr(config, "DEBUG", False)
    body = json.loads(bytes(main.internal_error("Comment error", leaky).body))
    message = body["error"]["message"]

    assert "10.0.0.5" not in message and "password" not in message
    assert message == "Comment error", message


def test_the_details_are_kept_where_somebody_is_reading_them(monkeypatch):
    import json

    import main
    from core import config

    monkeypatch.setattr(config, "DEBUG", True)
    body = json.loads(bytes(main.internal_error("Comment error", Exception("boom")).body))

    assert "boom" in body["error"]["message"]


def test_no_handler_still_formats_a_raw_exception_into_its_response():
    """The helper only helps where it is used. This is the sweep."""
    import re

    offenders = []
    for path in sorted((BACKEND / "api").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"error_response\(\s*f?[\"'].*\{(str\()?e(xc)?\)?\}", line):
                offenders.append(f"{path.name}:{i}")
    assert not offenders, f"raw exception text returned to the caller at: {offenders}"


# -- declaring production, for real ----------------------------------------
#
# These boot a fresh interpreter with ENV=production rather than asserting on
# the source, because the question is what the app *does* under that setting.
# Importing `main` builds the app but does not run startup(), so this costs an
# import and not an ML fit.

import subprocess  # noqa: E402

PROD_ENV = {
    "ENV": "production",
    "JWT_SECRET": "x" * 48,
    "POSTGRES_PASSWORD": "not-the-default",
    "REDIS_PASSWORD": "also-not-the-default",
    "DEBUG": "false",
}


def _in_production(code: str, **overrides) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, **PROD_ENV, **overrides}
    return subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{BACKEND}')\n{code}"],
        capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=300,
    )


def test_production_hides_the_interactive_api_map():
    """`/docs` is a complete, self-service map of every route, parameter and
    schema, served to anyone who asks."""
    r = _in_production(
        "import main; print(main.app.docs_url, main.app.redoc_url, main.app.openapi_url)"
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip() == "None None None", r.stdout


def test_development_keeps_the_docs():
    """The gate must not be a deletion — they earn their keep locally."""
    import os

    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, r'{BACKEND}')\nimport main; print(main.app.docs_url)"],
        capture_output=True, text=True, env={**os.environ, "ENV": "development"},
        cwd=str(BACKEND), timeout=300,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip() == "/docs"


def test_production_refuses_to_boot_on_a_default_credential():
    """The whole point of the guard: declaring production is enough to be
    told what is still wrong, instead of finding out from a log later."""
    r = _in_production("import main", POSTGRES_PASSWORD="digikitab_dev")

    assert r.returncode != 0, "booted in production with the default database password"
    assert "POSTGRES_PASSWORD" in r.stderr, r.stderr[-2000:]


def test_production_refuses_to_boot_without_a_jwt_secret():
    r = _in_production("import main", JWT_SECRET="")

    assert r.returncode != 0, "booted in production with a generated JWT secret"
    assert "JWT_SECRET" in r.stderr, r.stderr[-2000:]


def test_a_correctly_configured_production_boots():
    """A guard that nothing can satisfy is not a guard, it is a wall."""
    r = _in_production("import main; print('booted')")

    assert r.returncode == 0, r.stderr[-2000:]
    assert "booted" in r.stdout


# -- the app serves its own pages ------------------------------------------
#
# The other half of retiring Express: it is only safe because the frontend
# comes from the API's own origin now. Asserted with real requests rather
# than by reading main.py for "StaticFiles" — the first version of this did
# the latter, and a sabotage that deleted the mount outright did not fail it,
# because the import line above it still matched.


def test_the_backend_serves_the_frontend_itself(fitted_app):
    _, client = fitted_app

    root = client.get("/")
    assert root.status_code == 200, "the app does not serve its own index"
    assert "text/html" in root.headers["content-type"]
    assert client.get("/filter.html").status_code == 200, (
        "a named page 404s — Express served these by hand and nothing replaced it"
    )
    assert client.get("/js/auth.js").status_code == 200, "static assets are not served"


def test_the_static_mount_does_not_shadow_the_api(fitted_app):
    """Mounted at "/", so ordering is the whole correctness argument: register
    it before the routers and it swallows every API route."""
    _, client = fitted_app

    assert client.get("/api/health").status_code == 200
    assert "application/json" in client.get("/api/health").headers["content-type"]


def test_a_missing_page_is_still_a_404(fitted_app):
    """`html=True` serving index.html for unknown paths would turn every
    typo into a 200, including mistyped API routes."""
    _, client = fitted_app

    assert client.get("/no-such-page.html").status_code == 404
    assert client.get("/api/no-such-route").status_code == 404


def test_the_frontend_asks_its_own_origin_for_the_api():
    """Seven pages hardcoded http://localhost:8000. One of them left behind
    would keep the app needing a CORS allowlist while looking like it did
    not."""
    import re

    offenders = []
    for path in sorted((ROOT / "frontend").rglob("*.*")):
        if path.suffix not in {".js", ".html"}:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""(?<!//\s)(fetch\(|API_BASE\s*=\s*)['"`]?https?://""", line):
                offenders.append(f"{path.name}:{i}: {line.strip()[:70]}")
    assert not offenders, f"absolute backend origins remain: {offenders}"
