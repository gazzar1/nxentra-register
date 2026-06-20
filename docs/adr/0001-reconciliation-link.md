# ADR-0001: ReconciliationLink — a first-class, replay-safe reconciliation match object

- **Status:** Proposed (draft v2 — revised after adversarial code review)
- **Date:** 2026-06-20
- **Deciders:** (founder/eng)
- **Supersedes / relates to:** A86 reconciliation bounded context; NEXT_TASKS A116, A129, A99b, A110, A114, A115; Phase B canonical platform models; Phase C reconciliation engine.
- **Scope:** the truth spine only. UI redesign, AI automation, provider analytics, and unifying the existing `ReconciliationException` table are explicit non-goals (see §12).

> **One-line decision:** Introduce a durable, queryable, projection-owned `ReconciliationLink` aggregate (own UUID PK, typed legs, deterministic idempotency key) that the *existing, replay-proven* reconciliation projection writes as a second target — making the three-way money relationship a row instead of a parsed `source_document` string, and giving the A116/A129 correctness fixes a structural home.

> **Revision note (v2):** A 6-skeptic adversarial review against the live code returned **needs_adjustment on all 6 decisions** — core architecture sound, but with 4 blocker-level false premises that would ship broken code if taken literally. v2 folds in those fixes and adds an explicit **prerequisites phase (P1–P6, §11)** that must land *before* the link table. Where v1 asserted something as fact that is actually future work, v2 marks it **[NET-NEW]**. The key reversal: the deterministic-key / replay-determinism story was partly fiction in v1 (uuid4 emitter, churning `JournalLine.public_id`, post-hoc `source_document`) — those are now prerequisites, not co-deliverables.

---

## 1. Problem statement

`/finance/reconciliation` is the strategic surface ("universal reconciliation engine"). Verification surfaced six load-bearing problems — three **correctness** (they corrupt the numbers the page shows), three **structural** (they block the redesign).

1. **A116 — source stamps lost on rebuild (OPEN).** `JournalEntry.source_module` / `source_document` are stamped via post-hoc `.update()` *after* the event-emitting step ([reconciliation/commands.py:411](../../backend/reconciliation/commands.py#L411)); absent from `JournalEntryCreatedData` / `JournalEntryPostedData` ([events/types.py:429](../../backend/events/types.py#L429)). On rebuild they come back **blank**, breaking the Stage 1 → Stage 3 Banked join. The "idempotency on rebuild" comment is the opposite of true.
2. **A129a/b/c — settlement clearance fragility (OPEN).** Only the EBD line's `reconciled` flag guards double-posting ([matching.py:177](../../backend/reconciliation/matching.py#L177)); delete-then-reimport resets it and double-posts. No `BankStatement` delete handler routes through unmatch — admin/cascade delete orphans the posted clearance JE (`matched_journal_line` is `SET_NULL`). Clearance JE ignores statement currency.
3. **Replay gives false confidence.** `test_replay_convergence_full_lifecycle` ([tests/test_a86_7a_cutover.py:414](../../backend/tests/test_a86_7a_cutover.py#L414)) asserts only `{match_status, matched_journal_line_id, match_confidence}` over `auto-match → unmatch → manual-match`, via a **manual field reset, not `rebuild()`**. It never deletes, re-imports, varies currency, or asserts the clearance-JE source stamps.
4. **Brittle source stamps as the join key.** The settlement ↔ clearance ↔ EBD ↔ bank relationship is `JournalEntry.source_document = "provider:batch_id"`, rebuilt via `.split(':')` in ≥5 sites across two apps ([matching.py:181](../../backend/reconciliation/matching.py#L181), [accounting/reconciliation_views.py:523](../../backend/accounting/reconciliation_views.py#L523)). No link row, no FK.
5. **Two exception models.** A rich, populated `ReconciliationException` ORM model + orphaned UI ([bank_connector/models.py:144](../../backend/bank_connector/models.py#L144)) coexists with a narrower `_needs_review_queue` over `BankStatementLine` differences ([reconciliation_views.py:495](../../backend/accounting/reconciliation_views.py#L495)), plus event-sourced `ExceptionRaised/Resolved` that the projection **no-ops** ([projections.py:118](../../backend/reconciliation/projections.py#L118)).
6. **Shopify coupling reaches the write path.** Per-order drilldown is `source="shopify"`-locked ([reconciliation_views.py:705](../../backend/accounting/reconciliation_views.py#L705)); `payment_settlement_projection.py:452` hardcodes `invoice__source='shopify'`.

**Preserve (the ~60% leverage):** the six-event match vocabulary, the idempotent canonical projection, and *proven* replay convergence for the happy lifecycle ([event_types.py](../../backend/reconciliation/event_types.py), [projections.py:6](../../backend/reconciliation/projections.py#L6)). This ADR **extends** that spine; it does not rebuild it.

---

## 2. Decision — the canonical object

Create `reconciliation/models.py` (the context owns **zero tables** today):

- **`ReconciliationLink`** — the durable aggregate. One row = one reconciliation relationship (two-way bank↔JE, or N-way obligation(s)↔settlement↔clearance↔bank). Carries status, confidence, currency math, idempotency key, full audit.
- **`ReconciliationLeg`** — child rows, one per source object, typed by `leg_type`. This is what makes **many-to-one** (many orders → one payout → one deposit) a *reconciled* unit, not a display grouping.

**Both must be guard (read) models** — install `objects = ProjectionWriteManager()` and a guarded `save()`/`delete()` calling `write_context_allowed({'projection'})`, exactly like `AccountingReadModel` ([accounting/models.py:135,714](../../backend/accounting/models.py#L135)). *(Red-team A7: "sole writer" is unenforced unless built this way — a plain `models.Model` is writable from anywhere.)* Optionally add an arch rule asserting conformance.

> **"Match" vs "Link":** a *match* is a `ReconciliationLink` in `CONFIRMED` status. We standardize on `ReconciliationLink` in code. **Note (A10):** the event `aggregate_type` stays the live value `"ReconciliationMatch"` (see §5) — do not rename it; only the model is named Link.

**Relationship to the existing spine:** the existing events stay; the projection gains a **second write target** (link/leg rows) alongside the legacy `BankStatementLine` flags (kept during cutover). No new event family for confirm/unmatch. **The existing `ReconciliationException` table is NOT folded into the projection in this ADR** (red-team B3 — see §10).

---

## 3. Source legs (`ReconciliationLeg.leg_type`)

Each leg references a source by a **replay-survivable key**. Critically — *the kind of stability differs by source*:

- **Projection-rebuilt sources** (`BankStatementLine`, `JournalEntry`) reconstruct the *same* `public_id` on replay (it's in the event payload + `get_or_create(public_id=…)`).
- **Command-created sources** (`SalesInvoice`, `PlatformSettlement`, `BankTransaction`, `ReconciliationException`) keep a stable `public_id` only because *nothing regenerates them* — they are **not** recreated by replay, so a *deletion* of one leaves a dangling leg (hence the delete-guard, §8, must extend to them later).
- **`JournalLine` is the exception — its `public_id` is NOT stable (red-team B1, BLOCKER).** `_replace_lines` does `entry.lines.all().delete()` then recreates without passing `public_id` ([projections/accounting.py:507,546](../../backend/projections/accounting.py#L507)), firing a fresh `uuid4` on every `POSTED`/`UPDATED`/`SAVED_COMPLETE` — not just rebuild. `JournalLineData` has no `line_public_id`. **Until prerequisite P1 lands, any JournalLine leg keys on `(entry_public_id, line_no)`, not `journal_line_public_id`.**

| `leg_type` | References (today) | Stable key |
|---|---|---|
| `OBLIGATION` | `SalesInvoice` (source-tagged; drop the `source="shopify"` filter) | `public_id` (command-created; never regenerated) |
| `SETTLEMENT` | settlement `JournalEntry` (+ optional `PlatformSettlement`) | `entry_public_id` |
| `BANK_LINE` | `BankStatementLine` | `public_id` (projection-stable) |
| `BANK_TX` | `BankTransaction` (the bank-feed path that has no bank line today) | `public_id` |
| `JOURNAL_LINE` | `JournalLine` (clearing / EBD / AR-control) | **`(entry_public_id, line_no)`** until P1, then `line_public_id` |
| `CLEARANCE_JE` | synthesized clearance `JournalEntry` | `entry_public_id` |
| `ADJUSTMENT_JE` | A16 difference adjustment `JournalEntry` | `entry_public_id` |
| `CASE` | a **new link-case row** (NOT the existing `ReconciliationException`) | own UUID — see §10/B3 |

A leg stores `source_public_id` (or the composite key) + `source_kind` (string discriminator), **not** a Django GenericForeignKey, to keep RLS/write-barrier enforcement explicit.

---

## 4. Statuses (`ReconciliationLink.status`)

```
PROPOSED     → advisory suggestion (heuristic or AI); NO canonical effect, NO JE
CONFIRMED    → canonical match; JL.reconciled flips, clearance/adjustment JEs valid
NEEDS_REVIEW → confirmed-within-tolerance with an unexplained residual (A16); finance "Needs Review" is a view over this
REJECTED     → a PROPOSED link declined; terminal, audit-only
UNMATCHED    → a CONFIRMED link reversed; legs released; side-effect JEs reversed
REVERSED     → terminal archive of an UNMATCHED link
DISPUTED     → confirmed but flagged (chargeback/short-pay); maps to a CASE leg
```

Transitions: `PROPOSED → {CONFIRMED, REJECTED}`; `CONFIRMED → {NEEDS_REVIEW, UNMATCHED, DISPUTED}`; `NEEDS_REVIEW → {CONFIRMED, UNMATCHED}`; `UNMATCHED → REVERSED`. Every transition is an event; the projection is the sole writer.

**Invariant (red-team-confirmed boundary):** `PROPOSED`/`REJECTED` links **must not** contribute to Banked/Open math and **must not** flip `JL.reconciled` or `BankStatementLine.match_status`. This preserves the advisory-vs-canonical contract the projection already enforces ([projections.py:93-108](../../backend/reconciliation/projections.py#L93)). Add a test asserting a `PROPOSED` link leaves all canonical fields untouched.

---

## 5. Identity and idempotency

- **Link identity:** `ReconciliationLink.id` is a **single dedicated UUID** (model PK).
- **Event identity (A10 — corrected):** keep `aggregate_type = "ReconciliationMatch"` (the live value; [commands.py:195,261](../../backend/reconciliation/commands.py#L195)) and set `aggregate_id = link.id` (36 chars, within `varchar(64)` — [events/models.py:181](../../backend/events/models.py#L181)). **This fixes the 2026-06-12 production 500** from concatenating two UUIDs (73 chars). There is a deliberate identity *discontinuity*: historical events keep `aggregate_id ∈ {bank_line.public_id, "bank_tx:…"}`; new events use `link.id`. This is **safe for replay** because the projection stitches by *payload* `public_id`s, never by `aggregate_id` ([projections.py:144-148](../../backend/reconciliation/projections.py#L144)) — see §7.
- **Idempotency key** — `ReconciliationLink.idempotency_key`, deterministic + domain-derived, unique per `(company, idempotency_key)`:
  - **settlement-clearance:** `f"clearance:{parent_provider_normalized_code}:{batch_id}"`.
    - `batch_id` is read verbatim from the CSV, empty rows skipped, no surrogate — **stable across delete/reimport** ([settlement_imports.py:173-174,781](../../backend/accounting/settlement_imports.py#L173)). ✓ confirmed sound.
    - `parent_provider_normalized_code` = the **header** provider on `source_document`, **never** a `provider_breakdown` sub-gateway code (multi-gateway batches post one JE under the parent — [payment_settlement_projection.py:103](../../backend/accounting/payment_settlement_projection.py#L103)).
    - **[NET-NEW / A1]** provider is **currently stripped** before clearance ([matching.py:181](../../backend/reconciliation/matching.py#L181)); `_create_settlement_clearance_je` receives only a bare `batch_id`. Prerequisite **P2** threads `provider_normalized_code` through the plan → synthesizer. The provider component is **non-optional** — without it, two providers with the same `batch_id` + same net amount collide ([matching.py:158-166,194-226](../../backend/reconciliation/matching.py#L158)).
  - **manual / generic-GL:** `f"manual:{bank_line_public_id}:{entry_public_id}:{line_no}"` — **keyed on `(entry_public_id, line_no)`**, not `journal_line_public_id` (B1, until P1).
  - **platform-payout:** `f"payout:{payout_external_id}:{bank_tx_public_id}"`.
- **[NET-NEW / A2 — not current behavior]** Today `_emit_match_confirmed` uses `idempotency_key=f"…:{uuid4()}"` ([commands.py:263](../../backend/reconciliation/commands.py#L263)) → zero dedup on reimport. Prerequisite **P3** replaces this with the deterministic key above and stamps it into the event payload so links survive replay deterministically. The A129a fix is literally: *before creating a clearance link, check for an existing non-`REVERSED` link with this key; if found, link to it instead of posting again.*

---

## 6. Audit and proof

`ReconciliationLink` records the accountability fields the current events *define but never populate* (a silent audit gap — `confirmed_by_user_id` is dropped even though `manual_match()` holds `actor.user`):

- `confirmed_by_user_id` / `_email` / `_at` (and reject/unmatch equivalents); `confirmation_kind` ∈ {`auto`, `manual`, `rule`, `platform_payout_reconcile`, `ai_proposed_then_confirmed`}.
- `confidence` (0–100) + `match_reasons` (structured, explainable: `[{rule:"batch_id_in_description",+70},{rule:"amount_exact",+20},{rule:"currency_mismatch",-50}]`).
- `evidence` (JSON) + **`source_snapshots`** (immutable copies of each leg's key fields *at match time*, so the proof survives later edits/deletes of source rows).
- `reason` (required ≥10 chars for reject/unmatch — currently a hardcoded placeholder, [commands.py:180](../../backend/reconciliation/commands.py#L180)).

This is the read source for the **Money Trace**. **(A10 note)** a per-match history drawer hitting the generic events API ([events/views.py:61](../../backend/events/views.py#L61)) must **UNION** old (`bank_line.public_id` / `bank_tx:…`) + new (`link.id`) aggregate_ids, **or** resolve history from `source_snapshots` instead. (No frontend hardcodes `aggregate_type` — verified zero matches.)

---

## 7. Replay behavior

- The projection is the **sole writer** of `ReconciliationLink`/`Leg` (guard models, §2). Same inputs → same links → same Banked/Open.
- **`_clear_projected_data` must be overridden (A8 — currently an inherited no-op).** `ReconciliationProjection` has no override → inherits `BaseProjection._clear_projected_data` = `pass` ([base.py:134](../../backend/projections/base.py#L134)) — the A115 bug. Harmless *today* only because output is in-place flags and the replay test does a manual reset. The moment links exist, `rebuild()` would leave stale links. Implement it to delete `ReconciliationLink`/`Leg` for the company under `projection_writes_allowed()`.
- **A116 fixed two ways:** (a) `source_module`/`source_document` move *into* `JournalEntryCreatedData`+`PostedData` (safety net, prerequisite **P4**); (b) Banked/Open reads from link legs, not `.split(':')`.
- **[NET-NEW / A9]** "Same link set on replay" is **unprovable until P3** (the uuid4 emitter gives each replayed link a new UUID). The acceptance gate's link assertions land *after* P3.
- **Replay-stitch is by payload `public_id`s, not `aggregate_id`** ([projections.py:86-148](../../backend/reconciliation/projections.py#L86)) — this is why the §5 identity switch is replay-safe.

---

## 8. Unmatch / delete / re-import behavior

- **Unmatch** emits `MatchUnmatched`; the projection releases legs, sets `UNMATCHED → REVERSED`, reverses side-effect JEs (clearance/adjustment) — driven by the `CLEARANCE_JE`/`ADJUSTMENT_JE` legs, not `source_document` re-derivation.
- **Statement deletion (A129b + B4 + A5):** add a **`pre_delete` signal** (NOT an overridden `delete()` — that fires on no real path here: there is no single-instance `statement.delete()`, and cascade/`QuerySet.delete()`/admin all bypass it; A5). The signal raises if any line participates in a non-`REVERSED` link — **gated behind a `statement_delete_allowed()` contextvar** (mirror `projections.write_barrier`). The guard must be **named on `accounting.BankStatement`** specifically (A11 — there are two `BankStatement` models; the bank-feed `bank_connector.BankStatement`/`BankTransaction` path is out of scope for this guard, documented). The contextvar is entered by:
  1. `unmatch_and_delete_statement` (the legitimate delete command) *after* it reverses links;
  2. **company offboarding** — `delete_unverified_user` → `company.delete()` cascades into statements ([accounts/commands.py:2367](../../backend/accounts/commands.py#L2367)); without the allowlist the guard **aborts the whole company deletion** (red-team B4, BLOCKER);
  3. **demo reseed** — `seed_shopify_demo._flush()` uses `QuerySet.delete()` ([seed_shopify_demo.py:193](../../backend/shopify_connector/management/commands/seed_shopify_demo.py#L193)) under `command_writes_allowed()` (A6).
- **Restore is safe by design** — `importer._clear_company_data` uses raw SQL ([importer.py:151,175](../../backend/backups/importer.py#L151)), bypassing the signal. Document this so a future ORM refactor of the importer doesn't silently reintroduce the break.
- **Re-import (A129a):** the deterministic settlement-clearance key (§5) resolves a reimported batch to the existing link → **no second clearance JE**.
- *Note:* `accounting.BankStatement` is a plain non-RLS model ([models.py:2613](../../backend/accounting/models.py#L2613)), so the `pre_delete` guard is the sole deletion control surface — no conflict with existing read-model guards.

---

## 9. Currency semantics

Four currencies must be representable and never conflated: **transaction** (order), **settlement** (provider statement), **bank** (bank account), **functional** (company base).

- Each `ReconciliationLeg` stores `amount` + `currency` + `fx_rate_to_functional` + `amount_functional`.
- Tolerance / rounding / Open-Banked math are computed in **functional** currency, `quantize("0.01")`, ROUND_HALF_UP, shared by planner *and* execute.
- **Plumbing exists (confirmed sound):** `create_journal_entry` accepts per-line `currency`/`amount_currency`/`exchange_rate` ([commands.py:614-617,678-692](../../backend/accounting/commands.py#L614)); `JournalLine` has the columns ([models.py:1549-1566](../../backend/accounting/models.py#L1549)).
- **The convention, stated correctly (red-team A-major):** pass debit/credit as the **settlement-currency (foreign) amount**, set line `currency=settlement_currency` + `exchange_rate`; **`post_journal_entry` converts** (functional into debit/credit, foreign into `amount_currency`, [commands.py:1187-1204](../../backend/accounting/commands.py#L1187)). **Do NOT pre-convert.** Scope strictly to `kind=NORMAL` (the conversion is skipped for `kind=ADJUSTMENT`); apply the same threading to the A16 difference-adjustment JE ([commands.py:1731](../../backend/reconciliation/commands.py#L1731)), which currently also posts currency-blind.
- **BLOCKER B2 — EBD must net to zero, not just "be denominated."** Stage-1 settlement JE books the EBD CR with `currency` but **no exchange_rate** ([payment_settlement_projection.py:342](../../backend/accounting/payment_settlement_projection.py#L342)); Stage-2 clearance passes `bank_line.amount` with no currency/rate ([commands.py:630](../../backend/reconciliation/commands.py#L630)). For a non-functional statement the two EBD legs are in different units and **the EBD account will not zero out.** Required:
  1. **Persist the Stage-1 EBD `exchange_rate`** (prerequisite **P4**) and have Stage-2 **reuse that identical rate** (read it from the settlement JE's EBD `JournalLine.exchange_rate`, not a fresh lookup) so the clearance CR-EBD functional amount equals the Stage-1 CR-EBD functional amount.
  2. When **bank-leg currency ≠ settlement-leg currency** (DR Bank vs CR EBD), book the residual as a real **FX gain/loss line**, not a rounding fudge.
  3. The acceptance gate (§11) asserts **EBD-account functional balance == 0 after clearance**, not merely "denominated per convention."

---

## 10. Wiring implications (architecture serving product, not theatre)

| Capability (current state) | Wiring to `ReconciliationLink` |
|---|---|
| **Exception queue** (`ReconciliationException`, rich + orphaned, **imperatively written** by detectors) | **Do NOT make the projection a second writer (B3 BLOCKER).** `bank_connector/exceptions.py:46` already `create()`s these (dedup by `(type, ref_type, ref_id)`); a projection co-writer means dup rows + a rebuild that **deletes detector rows it didn't create**. Instead the link references a **new, projection-owned `CASE` row/leg**; the existing `ReconciliationException` stays detector-owned. Table unification (convert detectors to emit `ExceptionRaised`, then make it projection-owned) is **deferred to A86.8**, out of this ADR. |
| **Confidence scorer** (computed, persisted, rendered nowhere) | `confidence` + `match_reasons` live on the link; the dead `match_confidence` on the wire is sourced from the link and finally rendered. The scorer emits `MatchProposed` → a `PROPOSED` link. |
| **`ExceptionRaised`/`Resolved` events** (projection no-op) | **Wiring the handler is not enough — there is no emitter.** `reconciliation/exceptions.py` is an empty A86.1 scaffold ([exceptions.py:1-24](../../backend/reconciliation/exceptions.py#L1)); the events are emitted nowhere. Either implement the emitter (the anomaly detectors → `ExceptionRaised`) **or defer** Exception wiring to A86.8. This ADR ships the `CASE` leg structure but **does not claim Exception events are live.** |
| **Dry-run preview** (`preview_auto_match`, uncalled, covers 1 of 3 passes) | Must (a) cover **all three** passes and (b) emit a *projected link set* identical to what execute creates — made testable by the link (parity gate, §11). Until parity is green, preview is a *candidate* surface, not the command-preview of record. |
| **Three-column commerce view** (read-only, Shopify-shaped) | Each row becomes a link (or `PROPOSED` link); confirm/reject/split/merge act on links; the many-orders→one-payout grouping becomes a real many-to-one link with multiple `OBLIGATION` legs. |
| **AI `MatchProposed` seam** (typed, audit-capable, unwired) | AI emits `MatchProposed` (`proposer="ai_agent:…"` + evidence) → `PROPOSED` link; **operator confirms**. No AI auto-confirm (§12). |

**Arch-rule constraints (A-minor):** the link-writing handler must **not** `emit_event` (Rule 2 — emitters stay in commands/reactors); no **new** direct `JL.reconciled` write may be added in `reconciliation/commands.py` (the `RECONCILED_WRITE_EXPECTED_COUNTS = {"reconciliation/commands.py": 3}` gate is exact — [test_architecture_rules.py:247](../../backend/tests/test_architecture_rules.py#L247)).

---

## 11. Prerequisites (P1–P6) and acceptance gate

**The link table cannot be implemented in the order v1 implied.** These prerequisites land first.

- **P1 (gates B1):** make `JournalLine.public_id` replay-deterministic — add `line_public_id` to `JournalLineData`, emit it from commands, and change `_replace_lines` to upsert by `(entry, line_no)` reusing the event's id (or derive `uuid5(entry_public_id, line_no)`). Until done, the `JOURNAL_LINE` leg + `manual:` key use `(entry_public_id, line_no)`.
- **P2 (gates A1/A4):** thread `provider_normalized_code` (parent code) through plan → `_settlement_prepass_match` → `_create_settlement_clearance_je` → link/key.
- **P3 (gates A2/A9):** replace `_emit_match_confirmed`'s `uuid4` idempotency key with the deterministic domain-derived link key, stamped into the event payload.
- **P4 (gates B2/A116):** persist the Stage-1 EBD `exchange_rate`; move `source_module`/`source_document` into the JE event payload.
- **P5 (gates A7):** build `ReconciliationLink`/`Leg` as guard models (`ProjectionWriteManager` + guarded save/delete) in their initial migration.
- **P6 (gates B4/A5/A6):** add the `statement_delete_allowed()` contextvar, entered by the command path, company offboarding, and reseed.

**Acceptance gate** (replaces the happy-path-only replay test; uses the *real* `rebuild()` path, not a manual reset):

```
import orders → import settlement → import bank line
→ auto-match (ALL 3 passes)
→ assert preview_auto_match() predicted EXACTLY the links auto-match created      [parity]
→ capture Banked / Open / link set / EBD functional balance
→ rebuild() reconciliation projection (clear + replay)
→ assert Banked / Open / link set identical; EBD functional balance == 0          [A116/B2/A8]
→ delete bank statement (must route through unmatch_and_delete or be blocked)     [A129b/B4]
→ re-import same statement → auto-match → assert NO second clearance JE; one link/batch  [A129a]
→ unmatch → assert legs released + clearance JE reversed → re-match → same Banked/Open
VARIANTS:
→ USD statement vs EGP-functional company → assert EBD functional balance == 0    [A129c/B2]
→ multi-gateway Paymob batch (paymob + paymob_accept) → one clearance link, one key  [A3]
→ two providers, SAME batch_id, SAME net amount → two distinct links, no constraint clash, correct pairing  [A4]
```

---

## 12. Non-goals

My recommendation on your two question marks: **both are non-goals — yes.**

- **No AI automation yet.** The link is *designed for* AI proposals (`PROPOSED` status, proposer/evidence) but **emission is deferred** — AI over a just-stabilized truth layer is fake sophistication.
- **No provider scorecards yet.** Read-side analytics on top of links; need link history first.

Additional non-goals (keep this a tight truth-spine): **no `ReconciliationException` table unification** (deferred to A86.8 — B3); **no canonical `PlatformOrder`** (the `OBLIGATION` leg references `SalesInvoice`; Phase B); **no UI redesign**; **no open-item allocation aging**; **no bulk match actions**; **no Exception-event emitter** (the detectors stay imperative until A86.8).

---

## 13. Consequences

**Positive:** one queryable match object kills the `.split(':')` join, homes the A116/A129 fixes structurally, fixes the `aggregate_id` overflow, gives Money Trace + the AI seam a foundation. Reuses the proven event spine + projection (low architectural risk — *no skeptic refuted the architecture; all blockers are implementation preconditions*).

**Negative / risks:** (a) a prerequisites phase (P1–P6) precedes the visible payoff — P1 (JournalLine identity) and P4 (FX/event-payload) touch hot paths; (b) shadow-write window (flags *and* links) until surfaces cut over; (c) the `pre_delete` guard changes operator/admin muscle memory and **interacts with company offboarding + reseed** (must be allowlisted — B4/A6); (d) `ReconciliationException` stays a separate imperative writer until A86.8 — projection-rebuild must **never** clear exception rows it didn't create; (e) back-population of links is replay-derived (§14).

**Sequencing:** P1–P6 → link table (P5) → A116/A129 fixes landing *on* the link → acceptance gate green → unification map → wire surfaces to read links → UI redesign → AI. Do not invert.

---

## 14. Open questions / resolutions

1. `ReconciliationLeg` child table (chosen) vs. hybrid (1:1 legs as FKs on the link, many-side in children) — confirm the pure-child design is worth the extra join for the common two-leg match.
2. **Backfill — RESOLVED to replay-derive.** Historical `BusinessEvent`s are **immutable** ([events/models.py:350,401](../../backend/events/models.py#L350)); they are **not** rewritten (forward-only is the *only* option). The backfill command folds the existing `ReconciliationMatch*` event stream (stitched by payload `public_id`s) into fresh `ReconciliationLink`/`Leg` rows; new events thereafter use `aggregate_id=link.id`.
3. `DISPUTED` as a link status vs. purely a `CASE`-leg state.
4. SLA/due-date column home when cases unify (on `ReconciliationException` vs. on the link) — deferred with A86.8.
5. **Event identity (A10):** keep `aggregate_type="ReconciliationMatch"` + change only `aggregate_id` (chosen, smaller blast radius) vs. full rename. v2 chose keep-type.
