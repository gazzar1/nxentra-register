---
name: nxentra-guarded-merge
description: Execute the AGENTS.md guarded squash merge for a Nxentra PR — verify every gate (clean Codex verdict on the exact head, zero unresolved threads, green checks on the head SHA, unmoved main), merge with --match-head-commit, then prove tree-identity and post-merge main CI. Use for every merge to main.
---

# Nxentra guarded merge

The merge half of the AGENTS.md protocol. Preconditions come from
`nxentra-review-resolution`; this skill verifies them mechanically and never
substitutes trust for verification.

Every code block below is a SCRIPT fragment for one `bash` invocation with
`set -euo pipefail` — every line either succeeds or stops the ritual; no step
merely *displays* something an operator is supposed to eyeball. Substitute
`<owner> <repo> <N>` and run each stage as a script (not pasted line-by-line
into an interactive shell, where `exit` semantics differ).

## 1. Verify all four gates — each against the EXACT head SHA

Gate (a) is the one human step: read the latest Codex verdict and confirm it
is the CLEAN issue comment ("Didn't find any major issues") naming the exact
head you are about to merge — clean verdicts are ISSUE comments, not review
objects:

```bash
gh pr view <N> --json comments --jq '.comments[-3:]'
```

Gates (b)–(d) are mechanical assertions:

```bash
set -euo pipefail
HEAD=$(git rev-parse HEAD)          # the reviewed head you intend to merge
BASE=<sha>                          # the origin/main SHA the PR was built and reviewed on

# (b) zero unresolved review threads — PAGINATED (a bounded first page can
#     read 0 while an unresolved thread sits past the boundary) and
#     fail-closed (set -e aborts on a failed query; the count is asserted):
counts=$(gh api graphql --paginate -f query='query($endCursor: String) {
  repository(owner: "<owner>", name: "<repo>") {
    pullRequest(number: <N>) { reviewThreads(first: 100, after: $endCursor) {
      nodes { isResolved } pageInfo { hasNextPage endCursor } } } } }' \
  --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length')
unresolved=$(printf '%s\n' "$counts" | awk '{s+=$1} END {print s+0}')
if [ "$unresolved" -ne 0 ]; then
  echo "$unresolved unresolved thread(s) — do not merge"; exit 1
fi

# (c) checks on the head COMMIT (not just "the PR"): ASSERT that at least one
#     check ran and that the LATEST run of every check name concluded success
#     (a superseded failure with a later success for the same name is fine —
#     check_runs is returned newest-first, so unique_by(.name) keeps the
#     latest). A pending/failed/cancelled required check must abort:
bad=$(gh api "repos/<owner>/<repo>/commits/$HEAD/check-runs" \
  --jq '[.check_runs | sort_by(.started_at) | reverse | unique_by(.name)[]
         | select(.conclusion != "success") | "\(.name): \(.conclusion // .status)"] | @tsv')
total=$(gh api "repos/<owner>/<repo>/commits/$HEAD/check-runs" --jq '.total_count')
if [ "$total" -eq 0 ]; then echo "NO check runs on $HEAD — do not merge"; exit 1; fi
if [ -n "$bad" ]; then echo "non-success latest check(s) on $HEAD: $bad — do not merge"; exit 1; fi

# (d) main still equals the reviewed base — --match-head-commit protects the
#     PR HEAD only, so base movement must be asserted here:
git fetch origin main -q
if [ "$(git rev-parse origin/main)" != "$BASE" ]; then
  echo "main MOVED (now $(git rev-parse origin/main), reviewed on $BASE) — re-verify, do not merge"
  exit 1
fi
```

Any gate failing → STOP and report (AGENTS.md stop conditions). A moved main
means re-verify (rebase or fresh review) — never merge past it.

## 2. Merge — guarded, never forced

```bash
gh pr merge <N> --squash --match-head-commit "$HEAD"
```

- `--match-head-commit` makes the merge refuse if ANYONE pushed after the
  review — the guard is the point. Never `--admin`, never force, never retry
  a refused merge without re-running step 1 on the new head.

## 3. Prove the merge — identity, then CI, then local sync

```bash
set -euo pipefail

# merged state + merge commit (asserted, not displayed):
state=$(gh pr view <N> --json state --jq '.state')
MERGE_SHA=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
if [ "$state" != "MERGED" ] || [ -z "$MERGE_SHA" ]; then
  echo "PR not MERGED or merge commit missing (state=$state) — investigate"; exit 1
fi
git fetch origin main -q

# TREE-IDENTITY: the squash tree must be byte-identical to the reviewed head:
if [ "$(git rev-parse "origin/main^{tree}")" != "$(git rev-parse "$HEAD^{tree}")" ]; then
  echo "TREE MISMATCH between merged main and the reviewed head — investigate before anything else"
  exit 1
fi

# post-merge main CI to green (blocking — a red main is a stop condition).
# Pin the run to the MERGE COMMIT: immediately after merging, the new run may
# not exist yet, so `--branch main --limit 1` can return the PREVIOUS green
# run. Poll by --commit until the run appears, then watch it — and STOP the
# ritual on a red result (gh run watch --exit-status returns nonzero):
until RUN_ID=$(gh run list --commit "$MERGE_SHA" --limit 1 --json databaseId \
  --jq '.[0].databaseId' 2>/dev/null) && [ -n "$RUN_ID" ]; do sleep 15; done
if ! gh run watch "$RUN_ID" --exit-status; then
  echo "post-merge main CI FAILED on $MERGE_SHA — red main is a stop condition; do NOT continue"
  exit 1
fi

# local sync — ALWAYS ff-only (reset --hard nukes uncommitted work):
git checkout main && git pull --ff-only
```

## 4. Close the loop

The merge is not done until:
- the live tracker (`docs/status/constrained_pilot_status.md`) reflects the
  new gate state, if the PR changed one (usually the PR itself carries that
  edit — verify it landed);
- session memory records the merge SHA, the reviewed head, and the verdict.
