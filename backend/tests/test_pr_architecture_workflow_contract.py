# tests/test_pr_architecture_workflow_contract.py
"""Static contract tests for .github/workflows/pr-architecture-contract.yml.

A pull_request_target workflow is loaded from the BASE branch, so the PR that
introduces or edits it cannot prove its own definition at runtime. These pure
text/structure tests pin the security-relevant properties deterministically
instead: trusted trigger, read-only permissions, base-SHA-only checkout,
trusted execution path, body-as-data, no secrets, CI-standard Python, and the
stable check name. No YAML dependency — deliberately narrow line/regex checks.
"""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-architecture-contract.yml"


def _text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _lines() -> list[str]:
    return _text().split("\n")


def _effective_text() -> str:
    """The workflow's executable YAML: comment lines removed. Negative security
    assertions apply here — prose comments may legitimately NAME the forbidden
    things while explaining why they are forbidden."""
    return "\n".join(line for line in _lines() if not line.lstrip().startswith("#"))


def test_trigger_is_pull_request_target_not_pull_request():
    text = _text()
    assert re.search(r"(?m)^\s*pull_request_target:\s*$", text), "trigger must be pull_request_target"
    # A bare `pull_request:` trigger must NOT exist (substring care: the
    # `_target` form contains `pull_request`, so match exact trigger lines).
    assert not re.search(r"(?m)^\s*pull_request:\s*$", text), "plain pull_request trigger is forbidden"


def test_trigger_event_types_complete():
    text = _text()
    m = re.search(r"(?m)^\s*types:\s*\[(?P<types>[^\]]+)\]", text)
    assert m, "trigger types list missing"
    types = {t.strip() for t in m.group("types").split(",")}
    assert types == {"opened", "edited", "synchronize", "reopened", "ready_for_review"}


def test_permissions_are_read_only():
    text = _text()
    assert re.search(r"(?m)^\s*contents:\s*read\s*$", text)
    assert re.search(r"(?m)^\s*pull-requests:\s*read\s*$", text)
    assert not re.search(r"(?m)^\s*[a-z-]+:\s*write\s*$", text), "no write permission may be requested"


def test_checkout_is_base_sha_only():
    text = _effective_text()
    assert re.search(r"(?m)^\s*ref:\s*\$\{\{\s*github\.event\.pull_request\.base\.sha\s*\}\}\s*$", text), (
        "checkout must pin github.event.pull_request.base.sha"
    )
    assert "head.sha" not in text, "PR head must never be checked out"
    assert "refs/pull" not in text, "the PR merge ref must never be checked out"


def test_checker_runs_from_trusted_checkout_path():
    text = _text()
    assert re.search(r"(?m)^\s*path:\s*trusted-base\s*$", text), "base checkout must land in trusted-base/"
    assert re.search(r"(?m)^\s*run:\s*python\s+trusted-base/scripts/check_pr_architecture_contract\.py\s*$", text), (
        "the checker must execute from the trusted-base checkout"
    )


def test_pr_body_passed_as_env_data_only():
    text = _text()
    assert re.search(r"(?m)^\s*PR_BODY:\s*\$\{\{\s*github\.event\.pull_request\.body\s*\}\}\s*$", text), (
        "the PR body must be passed via the PR_BODY environment variable"
    )
    # The body expression may appear ONLY on that env line — never interpolated
    # into run commands, paths, or other expressions.
    body_refs = [line for line in _lines() if "pull_request.body" in line]
    assert len(body_refs) == 1 and "PR_BODY:" in body_refs[0], (
        "github.event.pull_request.body may only appear on the PR_BODY env line"
    )


def test_no_secrets_context():
    assert "secrets." not in _text(), "the workflow must not use any secrets context"


def test_python_matches_ci_standard():
    assert re.search(r"(?m)^\s*python-version:\s*\"3\.11\"\s*$", _text()), "Python must match CI standard 3.11"


def test_check_name_is_stable():
    # Both the workflow name and the job display name must remain exactly
    # 'PR Architecture Contract' — branch protection will key on this string.
    assert re.search(r"(?m)^name: PR Architecture Contract\s*$", _text())
    assert re.search(r"(?m)^\s{4}name: PR Architecture Contract\s*$", _text())
