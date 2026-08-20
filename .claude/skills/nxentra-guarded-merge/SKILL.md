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

# (b) zero unresolved review threads — PAGINATED (a bounded first-page query
#     can read 0 while an unresolved thread sits past the page boundary) and
#     FAIL-CLOSED with an explicit `exit 1` (a bare `false` or a `... | awk`
#     pipeline keeps executing without set -e, so a failed query would read
#     as 0 and let the rest of the gate run unverified). Run the gate block
#     as a script so the exits stop the ritual:
if ! counts=$(gh api graphql --paginate -f query='query($endCursor: String) {
  repository(owner: "<owner>", name: "<repo>") {
    pullRequest(number: <N>) { reviewThreads(first: 100, after: $endCursor) {
      nodes { isResolved } pageInfo { hasNextPage endCursor } } } } }' \
  --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length'); then
  echo "thread query FAILED — gate NOT verified, do not merge"
  exit 1
fi
unresolved=$(printf '%s\n' "$counts" | awk '{s+=$1} END {print s+0}')
if [ "$unresolved" -ne 0 ]; then
  echo "$unresolved unresolved thread(s) — do not merge"
  exit 1
fi

# (c) every required check SUCCESS on the head COMMIT (not just "the PR") —
#     fail-closed: capture first so a failed query aborts instead of being
#     masked by a downstream pipe stage's exit code:
if ! check_runs=$(gh api "repos/<owner>/<repo>/commits/$HEAD/check-runs" \
  --jq '.check_runs[] | [.name, .conclusion] | @tsv'); then
  echo "check-runs query FAILED — gate NOT verified, do not merge"
  exit 1
fi
printf '%s\n' "$check_runs" | sort -u
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

# TREE-IDENTITY: the squash tree must be byte-identical to the reviewed head —
# an explicit assertion, not an eyeball comparison:
if [ "$(git rev-parse "origin/main^{tree}")" != "$(git rev-parse "$HEAD^{tree}")" ]; then
  echo "TREE MISMATCH between merged main and the reviewed head — investigate before anything else"
  exit 1
fi

# post-merge main CI to green (blocking — a red main is a stop condition).
# Pin the run to the MERGE COMMIT: immediately after merging, the new run may
# not exist yet, so `--branch main --limit 1` can return the PREVIOUS green
# run and declare success for CI that never ran. Poll by --commit until the
# run appears, then watch it:
MERGE_SHA=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
until RUN_ID=$(gh run list --commit "$MERGE_SHA" --limit 1 --json databaseId \
  --jq '.[0].databaseId' 2>/dev/null) && [ -n "$RUN_ID" ]; do sleep 15; done
gh run watch "$RUN_ID" --exit-status

# local sync — ALWAYS ff-only (reset --hard nukes uncommitted work):
git checkout main && git pull --ff-only
```

## 4. Close the loop

The merge is not done until:
- the live tracker (`docs/status/constrained_pilot_status.md`) reflects the
  new gate state, if the PR changed one (usually the PR itself carries that
  edit — verify it landed);
- session memory records the merge SHA, the reviewed head, and the verdict.
