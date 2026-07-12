# tests/test_a162_failsafe_boot.py
"""
A162 — fail-safe production boot (2026-07-11 dual audit).

Before the fix:
- DEBUG defaulted True: a partially rebuilt droplet .env (DEBUG omitted)
  booted against real merchant data with HSTS/secure-cookies/CSP/
  SECRET_KEY/CORS guards and the FIELD_ENCRYPTION_KEY hard-fail silently
  OFF.
- PROJECTIONS_SYNC was True only via `or DEBUG`; async mode ghost-fails
  ~35 accounting command sites (create_journal_entry reads its projected
  row right after emitting) but nothing refused to boot that way.
- TESTING was argv-sniffed ("pytest"/"test" in sys.argv) — False at
  settings-import time under `python -m pytest`, and any exact `test`
  argv element silently disabled the write barrier.

These tests spawn subprocesses that import settings under controlled
env (dotenv neutralized so a real backend/.env can't leak in). No DB.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

FERNET_TEST_KEY = "q2jxAVYf7CLIEoWl4kirqfmtKwYHX-ne4zcmToWNVRM="

# Env keys that must never leak from the test runner into the subprocess.
_POPPED = [
    "DEBUG",
    "DJANGO_DEBUG",
    "TESTING",
    "PYTEST_CURRENT_TEST",
    "DJANGO_SETTINGS_MODULE",
    "SECRET_KEY",
    "DJANGO_SECRET_KEY",
    "FIELD_ENCRYPTION_KEY",
    "PROJECTIONS_SYNC",
    "ALLOWED_HOSTS",
    "CORS_ALLOWED_ORIGINS",
    "CSRF_TRUSTED_ORIGINS",
    "DATABASE_URL",
    "RLS_BYPASS",
]

_PRELUDE = "import dotenv; dotenv.load_dotenv = lambda *a, **k: None\n"  # neutralize backend/.env


def _run(code: str, extra_env: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in _POPPED:
        env.pop(key, None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", _PRELUDE + code],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _prod_env(**overrides):
    env = {
        "SECRET_KEY": "x" * 60,
        "FIELD_ENCRYPTION_KEY": FERNET_TEST_KEY,
        "PROJECTIONS_SYNC": "True",
        "ALLOWED_HOSTS": "app.nxentra.com",
        "CORS_ALLOWED_ORIGINS": "https://app.nxentra.com",
        "CSRF_TRUSTED_ORIGINS": "https://app.nxentra.com",
    }
    env.update(overrides)
    return env


def test_debug_defaults_false():
    """The audited defect: one omitted DEBUG env var must mean PRODUCTION
    posture, not a permissive dev boot."""
    result = _run("import nxentra_backend.settings as s; print(s.DEBUG)", _prod_env())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", f"DEBUG must default False, got {result.stdout!r}"


def test_prod_boot_refuses_missing_projections_sync():
    env = _prod_env()
    env.pop("PROJECTIONS_SYNC")
    result = _run("import nxentra_backend.settings", env)
    assert result.returncode != 0, "prod boot must refuse when PROJECTIONS_SYNC is unset"
    assert "PROJECTIONS_SYNC" in result.stderr


def test_prod_boot_refuses_projections_sync_false():
    """An operator cannot force the async footgun in production — every
    accounting command would ghost-fail."""
    result = _run("import nxentra_backend.settings", _prod_env(PROJECTIONS_SYNC="False"))
    assert result.returncode != 0
    assert "PROJECTIONS_SYNC" in result.stderr


def test_prod_boot_accepts_lowercase_true():
    """Tolerant parsing: PROJECTIONS_SYNC=true (lowercase) must not trip
    the assertion."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.PROJECTIONS_SYNC)",
        _prod_env(PROJECTIONS_SYNC="true"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_testing_env_var_is_recognized():
    """TESTING=True (as set by test_settings and all CI pytest jobs) must
    make settings importable with no production env at all."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.TESTING, s.DISABLE_EVENT_VALIDATION, s.PROJECTIONS_SYNC)",
        {"TESTING": "True"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True", "True"]


def test_explicit_debug_true_is_dev_mode():
    """Safety net against over-tightening dev: DEBUG=True alone boots."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.DEBUG, s.PROJECTIONS_SYNC)",
        {"DEBUG": "True"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True"]


def test_argv_element_no_longer_toggles_testing():
    """The old sniffing let ANY exact 'test' argv element disable the
    write barrier + event validation. Only manage.py's first argument
    keeps that meaning."""
    result = _run(
        "import sys; sys.argv = ['some-tool', 'run', 'test']\nimport nxentra_backend.settings as s; print(s.TESTING)",
        {"DEBUG": "True"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", "a non-leading 'test' argv element must not enable TESTING"


def test_env_example_contract():
    """backend/.env.example must document the real variable names."""
    text = (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")
    names = set()
    for line in text.splitlines():
        line = line.strip().lstrip("# ")
        if "=" in line and " " not in line.split("=", 1)[0]:
            names.add(line.split("=", 1)[0])

    required = {
        "DEBUG",
        "SECRET_KEY",
        "ALLOWED_HOSTS",
        "DATABASE_URL",
        "REDIS_URL",
        "SENTRY_DSN",
        "PROJECTIONS_SYNC",
        "FIELD_ENCRYPTION_KEY",
    }
    missing = required - names
    assert not missing, f".env.example is missing: {sorted(missing)}"

    stale = {
        "DJANGO_ALLOWED_HOSTS",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
    } & names
    assert not stale, f".env.example documents variables nothing reads: {sorted(stale)}"
