---
name: nxentra-review-resolution
description: Drive a Nxentra PR through Codex review rounds to a CLEAN verdict on the exact head — trigger, read findings, fix the class (not the instance), re-verify, and resolve every thread. Use whenever a PR needs its Codex review worked (AGENTS.md requires a clean verdict before merge).
---

# Nxentra Codex review resolution

The ritual that takes a PR from "opened" to "Codex CLEAN on the exact head,
zero unresolved threads" — the review half of the AGENTS.md merge protocol.
(The merge half is `nxentra-guarded-merge`.)

## 1. Trigger a round

Comment on the PR (Codex reviews on PR-open and on `@codex review` comments):

```bash
gh pr comment <N> --body "@codex review

<one-paragraph scope summary> on head \`$(git rev-parse HEAD)\` (base main \`<base-sha>\`).
Please look especially at: <the riskiest seams of this PR>."
```

Always pin the FULL head SHA in the comment — verdicts are only meaningful
against an exact commit.

## 2. Detect the verdict (two different channels!)

- **Finding rounds** arrive as REVIEW objects with inline comments.
- **Clean verdicts** arrive as an ISSUE COMMENT ("Didn't find any major
  issues…") — a review-object poll alone will miss them. Poll both:

```bash
# reviews (finding rounds)
gh api repos/<owner>/<repo>/pulls/<N>/reviews \
  --jq '[.[] | select(.user.login=="chatgpt-codex-connector[bot]")] | length'
# latest round's inline findings
RID=$(gh api repos/<owner>/<repo>/pulls/<N>/reviews \
  --jq '[.[] | select(.user.login=="chatgpt-codex-connector[bot]")] | .[-1].id')
gh api repos/<owner>/<repo>/pulls/<N>/reviews/$RID/comments --jq '.[] | {path, line, body}'
# clean verdict (issue comment)
gh pr view <N> --json comments --jq '.comments[-3:]'
```

The review body always says "Here are some automated review suggestions" —
that preamble appears even on rounds WITH findings; only the inline-comment
count and the issue-comment verdict text are meaningful.

## 3. Fix each finding — the class, not the instance

Per AGENTS.md, no merge with an open P1/P2. For every finding:

1. **Verify it against live code first** (Codex is usually right here, but
   confirm the mechanism — the fix must target the real failure, and a
   refuted finding gets a reasoned reply instead of a code change).
2. **Fix the CLASS**: sweep every sibling surface for the same defect (e.g. a
   view-wrapper bug → grep every view calling the same command family). Codex
   escalates by finding the next instance of a class you fixed narrowly.
3. **Pin it with a test** (route-level for HTTP behavior, functional for CLI
   refusals) — the fix commit carries its regression test.
4. Re-run: the affected suites, `tests/test_architecture_rules.py`, and the
   statics (`ruff check`, `ruff format --check`, `check-types.py`,
   `makemigrations --check`). Full SQLite battery before the round is
   declared done (run it in the background — but NEVER run two SQLite pytest
   processes concurrently: they share the test DB file and deadlock with
   "database is locked").
5. Commit with a per-finding message (what was wrong, what the fix is), push,
   and post the next `@codex review` comment: new head SHA + a numbered
   per-finding fix summary. Repeat from step 2.

Envelope guard: if a finding cannot be fixed inside the PR's stated envelope
(migration, event-schema, second writer, A3-core), STOP and report — that is
a founder decision, not a bigger commit.

## 4. Resolve every thread before merge

After the clean verdict, every finding thread gets a reply naming the fixing
commit, then resolution (AGENTS.md: no unresolved threads at merge):

```bash
gh api graphql -f query='query { repository(owner: "<owner>", name: "<repo>") {
  pullRequest(number: <N>) { reviewThreads(first: 50) { nodes { id isResolved path } } } } }'
# per thread:
gh api graphql -f query='mutation($tid: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $tid, body: $body}) { comment { id } } }' \
  -f tid=<THREAD_ID> -f body="Fixed in <sha>: <one-line what changed>."
gh api graphql -f query='mutation($tid: ID!) {
  resolveReviewThread(input: {threadId: $tid}) { thread { isResolved } } }' -f tid=<THREAD_ID>
```

A clean verdict is only exit-criteria when it names the CURRENT head — a new
push after the verdict restarts step 1.
