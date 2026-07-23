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
# DJANGO_TEST_MODE is set by test_settings in the PARENT pytest process; it
# must be popped so a subprocess simulates a real production boot (the A2
# guard is exempt whenever DJANGO_TEST_MODE=1 is present).
_POPPED = [
    "DEBUG",
    "DJANGO_DEBUG",
    "TESTING",
    "DISABLE_EVENT_VALIDATION",
    "DJANGO_TEST_MODE",
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
    """The test path (DJANGO_SETTINGS_MODULE=nxentra_backend.test_settings, as
    every CI pytest job sets) must make settings importable with TESTING on and
    no production env. Post-A2 the exemption is the settings-MODULE identity, not
    an env flag."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.TESTING, s.DISABLE_EVENT_VALIDATION, s.PROJECTIONS_SYNC)",
        {"TESTING": "True", "DJANGO_SETTINGS_MODULE": "nxentra_backend.test_settings"},
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


# ---------------------------------------------------------------------------
# A2 (2026-07-23) — fail-closed boot on unsafe bypass flags.
# A stray TESTING/RLS_BYPASS/DISABLE_EVENT_VALIDATION in a production .env
# silently disables RLS, event validation and the security-hardening block.
# The sanctioned test path (test_settings) sets DJANGO_TEST_MODE=1 before
# importing settings and is exempt; production (manage.py/gunicorn/celery)
# imports the settings module directly with no such sentinel.
# ---------------------------------------------------------------------------


def test_prod_boot_refuses_rls_bypass():
    result = _run("import nxentra_backend.settings", _prod_env(RLS_BYPASS="True"))
    assert result.returncode != 0, "prod boot must refuse RLS_BYPASS=True"
    assert "RLS_BYPASS" in result.stderr and "Refusing to boot" in result.stderr


def test_prod_boot_refuses_testing_flag():
    result = _run("import nxentra_backend.settings", _prod_env(TESTING="True"))
    assert result.returncode != 0, "prod boot must refuse TESTING=True"
    assert "TESTING" in result.stderr and "Refusing to boot" in result.stderr


def test_prod_boot_refuses_disable_event_validation():
    result = _run("import nxentra_backend.settings", _prod_env(DISABLE_EVENT_VALIDATION="True"))
    assert result.returncode != 0, "prod boot must refuse DISABLE_EVENT_VALIDATION=True"
    assert "DISABLE_EVENT_VALIDATION" in result.stderr and "Refusing to boot" in result.stderr


def test_prod_boot_refuses_lowercase_and_numeric_bypass():
    """Tolerant parsing must not become a bypass: rls_bypass=1 / testing=yes
    are still caught."""
    r1 = _run("import nxentra_backend.settings", _prod_env(RLS_BYPASS="1"))
    r2 = _run("import nxentra_backend.settings", _prod_env(TESTING="yes"))
    assert r1.returncode != 0 and "RLS_BYPASS" in r1.stderr
    assert r2.returncode != 0 and "TESTING" in r2.stderr


def test_prod_boot_refuses_django_test_mode():
    """DJANGO_TEST_MODE must NOT be an env-controlled master bypass: under
    production settings (DEBUG=False, not the test-settings module) a truthy
    DJANGO_TEST_MODE — alone or with other unsafe flags — must refuse boot."""
    r1 = _run("import nxentra_backend.settings", _prod_env(DJANGO_TEST_MODE="1"))
    assert r1.returncode != 0 and "DJANGO_TEST_MODE" in r1.stderr and "Refusing to boot" in r1.stderr
    r2 = _run("import nxentra_backend.settings", _prod_env(DJANGO_TEST_MODE="1", RLS_BYPASS="True"))
    assert r2.returncode != 0 and "Refusing to boot" in r2.stderr


def test_test_settings_module_exempts_bypass():
    """The exemption is the settings-MODULE identity: importing settings under
    DJANGO_SETTINGS_MODULE=nxentra_backend.test_settings is exempt even with
    bypass flags on."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.RLS_BYPASS, s.TESTING)",
        {
            "DJANGO_SETTINGS_MODULE": "nxentra_backend.test_settings",
            "RLS_BYPASS": "True",
            "TESTING": "True",
            "DJANGO_TEST_MODE": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True"]


def test_test_settings_refuses_nxentra_env_production():
    """test_settings disables RLS/validation/hardening — it must refuse to load
    when NXENTRA_ENV=production."""
    result = _run("import nxentra_backend.test_settings", {"NXENTRA_ENV": "production"})
    assert result.returncode != 0
    assert "Refusing to load" in result.stderr and "test_settings" in result.stderr


def test_wsgi_entrypoint_asserts_settings_module():
    """The production WSGI entrypoint must refuse a non-production settings
    module (e.g. an operator pointing gunicorn at test_settings)."""
    result = _run(
        "import nxentra_backend.wsgi",
        {"DJANGO_SETTINGS_MODULE": "nxentra_backend.test_settings"},
    )
    assert result.returncode != 0
    assert "Refusing to start WSGI" in result.stderr


def test_prod_boot_refuses_pytest_current_test():
    """PYTEST_CURRENT_TEST must not create TESTING/RLS_BYPASS/hardening bypass
    under production settings — boot refuses when it (or it plus another flag)
    is present outside the test-settings module."""
    r1 = _run(
        "import nxentra_backend.settings as s; print(s.TESTING, s.RLS_BYPASS)",
        _prod_env(PYTEST_CURRENT_TEST="tests/x.py::y (call)"),
    )
    assert r1.returncode != 0 and "PYTEST_CURRENT_TEST" in r1.stderr and "Refusing to boot" in r1.stderr
    r2 = _run(
        "import nxentra_backend.settings",
        _prod_env(PYTEST_CURRENT_TEST="tests/x.py::y (call)", RLS_BYPASS="True"),
    )
    assert r2.returncode != 0 and "Refusing to boot" in r2.stderr


def test_celery_entrypoint_asserts_settings_module():
    """A Celery worker/beat process must refuse a non-production settings module
    (guarded on the celery CLI so ordinary/test imports are unaffected)."""
    result = _run(
        "import sys; sys.argv=['celery','-A','nxentra_backend','worker']\nimport nxentra_backend.celery",
        {"DJANGO_SETTINGS_MODULE": "nxentra_backend.test_settings"},
    )
    assert result.returncode != 0
    assert "Refusing to start Celery" in result.stderr


def test_debug_dev_allows_bypass():
    """Dev (DEBUG=True) is allowed to run with RLS_BYPASS — the guard only
    fires in a production context."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.DEBUG, s.RLS_BYPASS)",
        {"DEBUG": "True", "RLS_BYPASS": "True"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True"]


def test_clean_prod_boot_unaffected_by_a2():
    """A2 must not false-positive: a clean production env (no bypass flag)
    boots normally."""
    result = _run(
        "import nxentra_backend.settings as s; print(s.DEBUG, s.RLS_BYPASS, s.TESTING)",
        _prod_env(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False", "False"]


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
