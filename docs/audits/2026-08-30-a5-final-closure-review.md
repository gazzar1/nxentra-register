# A5 Final Closure Review — 2026-08-30

**Verdict: A5 is COMPLETE for `ISOLATED_SHADOW_LEDGER_V1`.** A5 is not
deployed. **Merchant data remains blocked until G1 and G2 close.**

This is the read-only closure review required by the live tracker before the
A5 gate ("durable visible state for included money paths",
[2026-07-18 audit §21.2](2026-07-18-nxentra-current-state-audit.md)) could be
marked complete. It is **evidence for the A5 closure decision only** — it does
not certify the repository as a whole, the shared/standard contract, private
beta, or GA, and it does not replace the
[supported product contracts](../architecture/supported-product-contracts.md)
as contract authority, the
[live tracker](../status/constrained_pilot_status.md) as gate-status
authority, the architecture constitution, or the 2026-07-18 audit's broader
private-beta/GA requirements.

## Reviewed revision and evidence base

- Review revision: `main` = `2c7cabb7433ca78f7fb8481ac8a15765203e9d06`
  (the PR #135 squash merge).
- A5 implementation sequence, all merged and tree-verified:
  **#127** (`425a6b9`), **#128** (`ce2e571`), **#129** (`e0d199d`),
  **#130** (`9900c81`), **#131** A5-PR1a (`bc61268`), **#132** A5-PR2c
  (`e3d0cdf`), **#133** A5-PR1b (`3039712`), **#134** A5-PR4a (`9f4d08b`),
  **#135** A5-PR4b (`2c7cabb`).
- Post-merge main CI run **33322761489** on `2c7cabb7…`: all seven jobs
  successful, including Quality Gate.
- **Post-review correction (2026-08-31, before this artifact was
  finalized):** PR #137 (`3e962c5`; reviewed head `4ba27c1`; post-merge main
  CI run 33370644540 green) closed the matched-line exclusion A4 admission
  gap found during this review (side-finding item 9 below). PR #137 changed
  ADMISSION only — it did not change A5 durable-outcome semantics or the
  65-row A5 verdict. The review itself ran on main `2c7cabb`, as recorded
  above; it was not rerun.
- Method: read-only; eight focused readers over the supported contract only
  (no repository-wide sweep), each verdict anchored to code lines and the
  merged test suites; every proposed A5 blocker was independently and
  adversarially re-verified against the strict four-part test below. Existing
  green CI and the merged focused suites were treated as evidence.

## The closure standard applied

Every included financial input or operator action must end in exactly one
truthful durable outcome from the vocabulary
**POSTED / MATCHED / REJECTED / FAILED / QUARANTINED**, such that:

1. the authoritative source fact is identifiable;
2. a successful financial effect is represented exactly once;
3. a rejected or failed effect cannot masquerade as success;
4. a terminal outcome has durable evidence before it is considered consumed;
5. a retry cannot silently duplicate the financial effect;
6. a corrected retry can heal where the contract says healing is possible;
7. human-actionable outcomes appear in authenticated operator visibility;
8. they contribute to the aggregate PII-free alert contract where appropriate;
9. missing visibility data cannot produce a false all-clear;
10. a manual correction cannot hide its source and reason.

The five names are **contract-level outcome classes**, not one shared storage
enum: a domain model may use a domain-specific status name for one of these
classes when the class-to-storage mapping is explicitly defined and the
outcome is durably evidenced. Normative mapping for the one domain-specific
name in scope: a never-matched bank statement line deliberately excluded as a
nuisance row maps to the A5 **REJECTED** outcome class. Its concrete durable
representation is `BankStatementLine.MatchStatus.EXCLUDED` plus the
`RECONCILIATION_MATCH_UNMATCHED` event with `final_status=EXCLUDED`; its
meaning is that the operator intentionally rejected the imported row from
further reconciliation — an intentional no-financial-effect terminal
disposition, not MATCHED, not FAILED, not QUARANTINED. Match-destructive
exclusion (excluding a line that would dismantle an existing match) is
**not** part of this mapping — it is blocked under
`Capability.UNSAFE_BANK_MATCH` by PR #137.

A5 deliberately does **not** require: active alert delivery to a named human
(G1 owns it), the isolated restore rehearsal (G2), production deployment,
commercial proof, or handling of workflows outside the supported contract
(inventory, purchasing/AP, Stripe, Shopify Payments payout accounting,
foreign currency, multiple users, disputes, rebuild-as-recovery, legacy
banking, shared multi-merchant databases).

## §21.2 acceptance criteria — 8/8 MET

The audit's A5 definition was checked criterion by criterion on the merged
head: the headline durable-visible-state requirement; the mapping of the five
outcome classes onto existing mechanisms (not a new state machine, and not
one shared generic state machine — the REJECTED class, for example, is
concretely `ImportRejectedRow.Status.REJECTED` for ingress refusal,
`ShopifyRejectedEvidence` for authenticated malformed Shopify input, and
`BankStatementLine.MatchStatus.EXCLUDED` for the permitted operator rejection
of a never-matched nuisance row); all four
closed-scope bullets (the Shopify credit-note/missing-mapping/orphan
branches; the named Paymob/Bosta settlement branches; bank CSV row accounting
plus permitted matching; the resulting journal generation); the
manual-corrections traceability clause (pilot adjustments with reason +
source-item reference — PRs #134/#135); and the injection acceptance sentence
— each listed failure (missing mapping, closed period, credit-note failure,
zero/negative/oversized settlement, corrupt bank row) is proven never marked
successfully handled while its included accounting result is incomplete, and
a retry heals exactly once (applied-marker dedup + A177 idempotency +
`test_a5_pr1b` self-heal pins).

## Included process matrix — 65 rows: 63 PASS, 2 DOCUMENTED RESIDUAL, 0 blockers

| Domain | Rows | Result | Load-bearing anchors |
|---|---|---|---|
| Shopify paid order (normal; missing mapping; canonical failure; malformed authenticated payload; transient infrastructure failure; exact redelivery) | 6 | 6 PASS | Webhook acks 200 only after durable evidence commits (evidence-persistence failure → retryable 503); triple idempotency (command dedup, emitter unique key, applied marker); closed period → atomic QUARANTINE; `test_a5_shopify_fail_loud`, `test_a5_pr2b_rejected_evidence` |
| Shopify refund (positive; zero; negative aggregate; malformed; orphan; credit-note failure; corrected + exact redelivery) | 8 | 8 PASS | PR #132 MALFORMED_MONEY reroute (no row, no event, no sequence, no journal); identical redelivery re-sights one row; corrected redelivery supersedes and posts exactly once; `test_a5_pr2c_negative_refund_evidence` |
| Paymob/Bosta settlement import (valid; malformed; blank batch; malformed numerics; imbalance; ORPHAN_ORDER_ID; duplicate retry) | 7 | 6 PASS + R1 | Per-row REJECTED evidence outside the batch atomics; the zero-substitution masquerade pinned dead; orphan QUARANTINE flags exist iff the JE committed; `test_a5_pr3a`, `test_a5_pr3bc` |
| Bank CSV (valid; malformed dates/numerics/amounts; zero; duplicates; non-EGP; commit retry) | 7 | 7 PASS | Signed parse token; every row imported, rejected, or quarantined, none silently dropped |
| Reconciliation (canonical match; manual match; difference; resolve_difference adjustment; unmatch; exclude; materialization failure) | 7 | 6 PASS + R2 | Projection is the sole writer of match state (replay convergence pinned); D#9 honest rollback (no matched-response/UNMATCHED contradiction); one correction path. Post-review: match-destructive exclusion is now REFUSED under the constrained pilot by PR #137 (`3e962c5`); never-matched nuisance-row exclusion remains supported, and its durable EXCLUDED status is the concrete A5 REJECTED-class outcome for that permitted action (mapping defined in the closure standard above) |
| Projection framework (retryable failure; A3 terminal quarantine; DeferEvent; TerminalSkip atomicity; self-heal) | 5 | 5 PASS | Rule 17 one-owning-transaction pins (evidence → marker → bookmark, all or none); a quarantined financial event can never be consumed traceless; `test_a5_pr1b_terminalskip_atomicity` |
| Pilot adjustments + end-to-end contract (traced create/edit/post; untraceable refusal; invalid/cross-company; exact retry; changed-content conflict; reversal inherit/new-source; scratchpad refusal; source-resolution link survival; D1–D8) | 17 | 17 PASS | Zero-residue refusals before the JE mint; raw stamps non-request-writable (Rule 18); server-side enforcement load-bearing; profile-NONE body/financial semantics unchanged; CORS strictly additive (`test_a5_pr4a`, `test_a5_pr4b_cors_idempotency_header`, `test_a177_je_idempotency`) |
| Visibility & alerts (C1–C8) | 8 | 8 PASS | See below |

## Visibility and alert closure — PASS

Truthful global unresolved totals for `ProjectionFailureLog`,
`ImportRejectedRow`, and `ShopifyRejectedEvidence` fold into one combined
pool; a failed queue request or 403 can never render an all-clear (per-source
persistent load state, stale-response generation guard — PR #131); projection
lag, age-aware stale consumers, registry-derived missing consumers, paused
and errored consumers are represented; `shopify_reauth_required` and
`shopify_stale_sources` are present for the pilot; counter-registration
conflicts raise loudly; a raising counter produces the structured
`alert_counter_errors` unhealthy result — never a false zero and never an
uncontrolled 500; the alert response remains aggregate-only and PII-free.
**Active human delivery is correctly left to G1** — nothing inside A5 claims
delivery is complete.

## Pilot-adjustment end-to-end closure — PASS

The merged #134 + #135 contract closes as one workflow: evidence source →
traced draft → post → immutable journal provenance → journal detail →
reversal with a separate reason → reconciliation/source navigation. The
generic manual UI cannot bypass typed source + reason; raw
`source_module`/`source_document` remain non-request-writable; server
validation — not frontend route origin — is load-bearing; profile-NONE body
and financial semantics remain unchanged (journal creation for every profile
now carries the additive `Idempotency-Key` request header, permitted through
CORS only for already-allowed origins with the default header list preserved
and no origin/CSRF/cookie/authentication weakening); system-owned drafts
cannot be relabelled through the manual door; creating an adjustment never
resolves or acknowledges its source; reconciliation retains exactly one
correction path.

## The strict A5 blocker test

A finding blocked A5 only if **all four** held: (1) reachable under
`ISOLATED_SHADOW_LEDGER_V1` by the supported system, pilot OWNER, Shopify,
Paymob/Bosta import, bank import, or supported reconciliation workflow;
(2) causing silent missing/duplicate financial effect, false success,
terminal evidence loss, false operator all-clear, unhealable corrected retry,
untraceable manual correction, or unauthenticated/wrong-permission exposure
through an A5-owned surface; (3) not already refused by activation/preflight
or represented by a durable visible outcome; (4) deferral would make the
first supervised pilot materially dishonest or unsafe.

**One candidate was proposed and refuted under adversarial re-verification**
(the ShopifyStore PENDING-sweep cascade — item 1 below). The reachability
chain is real; the harm claims failed: for POSTED outcomes the durable
evidence is the immutable `BusinessEvent` + `JournalEntry`, and both survive
the cascade (the event payload carries the full financial content);
REJECTED evidence survives store deletion by explicit `SET_NULL` design;
after re-authorization the idempotent re-sync rebuilds the mirror with zero
duplicate journals (the corrected retry heals; stable idempotency keys
prevent duplication); interim settlement rows degrade to truthful, visible
`ORPHAN_ORDER_ID` evidence; and the store exits the ACTIVE-filtered health
counters at disconnection (a state with its own durable event), not at
deletion, so the delete flips no alert to clear. The worst case is a
recoverable mirror gap plus permanent loss of the original webhook byte
mirror — a genuine robustness defect requiring a pre-activation guard (below),
not an A5 falsehood.

## Side-finding dispositions (final)

None of these are dismissed as harmless; each has a named owner. They do not
block A5 under the strict test.

| # | Finding | Final disposition |
|---|---|---|
| 1 | ShopifyStore PENDING sweeps (hourly beat + request-time sweep) hard-delete a DISCONNECTED-with-history store flipped to PENDING by an abandoned reconnect, CASCADE-deleting the canonical Shopify mirror (orders/refunds/fulfillments/payouts/products) | **G1 / DEPLOYMENT PRECONDITION** — before first pilot activation, add a has-history guard or retain the store as DISCONNECTED rather than deleting canonical-history mirrors |
| 2 | `/_health/full` and `/_metrics` can expose company slugs/operational detail if the whole health prefix is published | **G1 / DEPLOYMENT PRECONDITION** — only the specifically approved aggregate alert endpoint may be externally reachable; G1 must prove the broader health/metrics endpoints are blocked or internally restricted |
| 3 | Shopify webhook traffic rides the inherited anonymous 100/hour throttle | **G1 / DEPLOYMENT DECISION** — before first merchant data, either prove the merchant's expected webhook burst and retry volume fit safely inside the configured limit, or introduce a dedicated HMAC-protected Shopify webhook throttle posture |
| 4 | Production corpus scan (`audit_posted_journal_corpus`) has not been run | **CONDITIONAL DEPLOYMENT EVIDENCE, not an unconditional G1 gate** — a fresh isolated pilot DB receiving no legacy BusinessEvents does not require the legacy-droplet corpus scan; if any legacy event history is reused, restored, migrated, or replayed into the pilot, the scan must run first and its result must be dispositioned |
| 5 | `ImportRejectedRow.raw_row` may retain COD/customer PII with no customer-redaction path | SHARED-BETA / GA FOLLOW-UP (single isolated deployment, founder-operated, merchant data blocked meanwhile) |
| 6 | `ProjectionFailureLog` has no table-level RLS | SHARED-BETA / GA FOLLOW-UP (isolation is deployment-level under this contract) |
| 7 | `ReconciliationLink.confirmed_by` fields not populated | SHARED-BETA / GA FOLLOW-UP |
| 8 | Older Shopify-family tables lack the `ShopifyRejectedEvidence` table-level RLS posture | SHARED-BETA / GA FOLLOW-UP |
| 9 | `exclude_line` reversal-capability posture vs `UNSAFE_BANK_MATCH` | **CLOSED BEFORE PUBLICATION** — PR #137 (`3e962c5`) conditionally applies the serialized `Capability.UNSAFE_BANK_MATCH` gate to match-destructive exclusion (decided on the admission-locked Company row after the `BankStatementLine` row lock, before any reversal work). Never-matched nuisance-row exclusion remains supported. A5's outcome verdict is unchanged |
| 10 | Period-override enforcement gaps on PATCH/save-complete | A3/A4 FOLLOW-UP, NOT A5 |
| 11 | `journal_entry.updated` lacks a separate A3 apply-door validator | A3/A4 FOLLOW-UP, NOT A5 |
| 12 | `_emit_automatic_reversal` payload-vs-emit-validation compatibility | GENERAL BACKLOG |
| 13 | Post-sequence fail-return paths can leave a JE-numbering gap | GENERAL BACKLOG |
| 14 | Legacy bare `ProjectionAppliedEvent` markers without a failure log are not retroactively repaired | GENERAL BACKLOG (fresh pilot DB has none by construction) |

## Accepted A5 residuals

- **R1 — settlement-import non-EGP refusal envelope.** The settlement import
  view's generic exception envelope wraps the DRF non-EGP refusal as a 400
  error body with zero evidence and zero financial residue — a loud refusal
  the operator cannot mistake for success. Align the HTTP status with the
  bank-import door post-A5.
- **R2 — terminal nuisance-row rejection.** Under the active pilot a
  never-matched nuisance bank row may be deliberately rejected from further
  reconciliation. The concrete status is
  `BankStatementLine.MatchStatus.EXCLUDED`, which maps to the contract-level
  REJECTED outcome class (the normative mapping in the closure standard
  above); the row and its exclusion event are durable and auditable.
  EXCLUDED has no in-pilot unexclude transition — an accepted operational
  limitation, not a missing durable outcome. For a misclassified real row: a
  supervised traced adjustment (PRs #134/#135) may correct the FINANCIAL
  consequence while preserving the exclusion history; it does not reopen,
  reconcile, resolve, or "heal" the bank line. Match-destructive exclusion
  and unmatch are blocked after PR #137, so no reversal-bearing exclude path
  is an admitted pilot operation any more; the older attempt-scoped reversal
  `request_id` observation therefore belongs to the profile-NONE / general
  reconciliation backlog, not to this contract's residuals.

## What this closure does and does not mean

A5 closes the durable-outcome gate for the included pilot money paths under
`ISOLATED_SHADOW_LEDGER_V1` on revision `2c7cabb`. It does **not** assert
operational readiness (G1 — live fresh-company E2E with independent control
totals, failure injection, and the three preconditions above), recovery
readiness (G2 — the isolated-database restore drill), deployment, or any
claim about the standard/shared contract, private beta, or GA.

Operational sequence from here:
**fresh-pilot runbook → G1 → G2 → first supervised merchant.**
