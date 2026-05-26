#!/usr/bin/env python
"""
A97: mypy spine type-check gate.

Cross-platform wrapper: chdir into backend/ so django_stubs can import
nxentra_backend.settings, then run mypy with --follow-imports=silent so
transitively-imported files (the ~235 errors in sales/commands.py +
accounting/commands.py, tracked as A98) don't block the gate.

Invoked as a pre-push hook from .pre-commit-config.yaml AND callable
manually: `python scripts/check-types.py` or
`./scripts/check-types.sh` / `.\scripts\check-types.ps1`.
"""

import os
import subprocess
import sys
from pathlib import Path

SPINE_FILES = [
    "events/emitter.py",
    "events/types.py",
    "events/models.py",
    "projections/base.py",
    "projections/write_barrier.py",
    "projections/models.py",
    "tenant/context.py",
    "tenant/router.py",
    "accounts/authz.py",
    "accounts/middleware.py",
    "accounts/email_service.py",
    "accounts/throttles.py",
    "accounting/models.py",
    "accounting/policies.py",
    "accounting/behaviors.py",
    "accounting/validation.py",
    "nxentra_backend/pagination.py",
]


def main() -> int:
    backend = Path(__file__).resolve().parent.parent / "backend"
    os.chdir(backend)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            "pyproject.toml",
            "--follow-imports=silent",
            *SPINE_FILES,
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
