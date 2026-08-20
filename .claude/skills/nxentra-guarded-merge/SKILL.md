---
name: nxentra-guarded-merge
description: Execute the AGENTS.md guarded squash merge for a Nxentra PR — verify every gate (clean Codex verdict on the exact head, zero unresolved threads, green checks on the head SHA, unmoved main), merge with --match-head-commit, then prove tree-identity and post-merge main CI. Use for every merge to main.
---

# Nxentra guarded merge

The merge half of the AGENTS.md protocol. Preconditions come from
`nxentra-review-resolution`; this skill verifies them mechanically and never
substitutes trust for verification.

## 1. Verify all four gates — each against the EXACT head SHA

```bash
HEAD=$(git rev-parse HEAD)   # the reviewed head you intend to merge

# (a) Codex verdict names $HEAD (clean verdicts are ISSUE comments):
gh pr view <N> --json comments --jq '.comments[-3:]'

# (b) zero unresolved review threads:
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: <N>) { reviewThreads(first: 50) { nodes { isResolved } } } } }' \
  --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length'

# (c) every required check SUCCESS on the head COMMIT (not just "the PR"):
gh api repos/<owner>/<repo>/commits/$HEAD/check-runs \
  --jq '.check_runs[] | [.name, .conclusion] | @tsv' | sort -u
# (a superseded failure plus a later success for the same check name is fine —
#  the LATEST run per name is what counts; gh pr checks <N> shows that view)

# (d) main has not moved:
git fetch origin main -q && git rev-parse origin/main   # == the base you built from
```

Any gate failing → STOP and report (AGENTS.md stop conditions). A moved main
means re-verify (rebase or fresh review) — never merge past it.

## 2. Merge — guarded, never forced

```bash
gh pr merge <N> --squash --match-head-commit $HEAD
```

- `--match-head-commit` makes the merge refuse if ANYONE pushed after the
  review — the guard is the point. Never `--admin`, never force, never retry
  a refused merge without re-running step 1 on the new head.

## 3. Prove the merge — identity, then CI, then local sync

```bash
# state + merge commit
gh pr view <N> --json state,mergeCommit
git fetch origin main -q

# TREE-IDENTITY: the squash tree must be byte-identical to the reviewed head
git rev-parse "origin/main^{tree}"    # must equal ↓
git rev-parse "$HEAD^{tree}"

# post-merge main CI to green (blocking — a red main is a stop condition):
gh run list --branch main --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch <run-id> --exit-status

# local sync — ALWAYS ff-only (reset --hard nukes uncommitted work):
git checkout main && git pull --ff-only
```

## 4. Close the loop

The merge is not done until:
- the live tracker (`docs/status/constrained_pilot_status.md`) reflects the
  new gate state, if the PR changed one (usually the PR itself carries that
  edit — verify it landed);
- session memory records the merge SHA, the reviewed head, and the verdict.
