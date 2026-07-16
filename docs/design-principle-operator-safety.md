# Design principle: easy to operate, hard to misuse

- **Status:** Active policy (applies to all new work and all reviews)
- **Date:** 2026-07-16
- **Origin:** owner articulation — "Nxentra should be easy for nonaccountants to operate, but difficult for them to misuse."
- **Relates to:** [finance_event_first_policy.md](finance_event_first_policy.md), [ADR-0001](adr/0001-reconciliation-link.md), [ADR-0002](adr/0002-canonical-payments-stripe-adapter.md), the fail-loud doctrine (F27/A157 in TASKS_DONE)

## The principle

Nxentra's operator is a merchant, not an accountant. The books must stay
trustworthy **even when the operator does not understand debits and credits** —
and the product must stay usable even though it refuses to let them break it.

"Hard to misuse" is achieved three ways, in strictly descending order of
preference:

1. **By construction** — invalid states are unrepresentable (posting profiles,
   close gates, immutable events). Requires zero vigilance. Prefer this.
2. **By refusal** — when the system cannot decide safely, it stops loudly and
   files an exception instead of guessing. Use this when construction is
   impossible.
3. **By gating** — permissions and warnings. The weakest kind: warnings get
   clicked through and permissions get granted. Necessary, never sufficient,
   and never a substitute for 1 or 2.

The failure mode of this principle is letting gating masquerade as safety
while making the product annoying. The resolution is **reversibility**: because
the ledger is event-sourced and corrections are reversals (never mutations),
a nonaccountant can be allowed to act freely — mistakes are recoverable, so
they don't need to be universally prevented. The sharpened form of the maxim:

> Easy to operate, hard to misuse, **impossible to silently and irreversibly
> corrupt the books.**

"Silently" is the load-bearing word and the competitive differentiator:
classic small-business tools let a merchant quietly delete a transaction and
the books lie forever after. Nxentra structurally cannot lie quietly.

## The three testable rules

Every PR that touches a financial write path, a merchant-facing surface, or a
destructive capability is checked against these. "Testable" means a reviewer
(human or agent) can answer pass/fail with a concrete scenario.

### Rule 1 — Every financial action is reversible or blocked

No user-reachable action may mutate or destroy posted financial state in
place. Either the action is expressible as a compensating event (reversal,
void, unmatch) or it is refused.

**Test:** for the new action, name the compensating action that undoes it and
the state both leave behind. If none exists and the action still proceeds,
the rule fails. Bulk operations count: a queryset `.delete()` that bypasses a
model guard is a violation even if the instance method raises.

- Embodied today: JE reversals with visibility (badge/tabs/cross-link);
  unmatch → rematch on reconciliation links; closed-period quarantine (C
  series); `BusinessEvent.delete()` raises; restore is in-transaction
  fail-closed with invariant verification.
- Known violations (tracked, P1): **A111/A112** — queryset bulk-delete
  bypasses the event-immutability guard and the project's own seed/flush
  tooling does it; Company FK CASCADE deletes events unguarded.

### Rule 2 — Every ambiguity stops loudly rather than guessing

When a write path lacks the information to post correctly (missing FX rate,
unknown provider, unmapped account, unparseable payload), it must quarantine
the work item, surface it on the exceptions queue, and leave a retry path.
Silent fallbacks (1:1 conversions, skip-and-continue, logger+return) are
forbidden on financial paths.

**Test:** feed the path an input with the load-bearing field missing. Pass =
a ProjectionFailureLog/exception row a merchant can see and retry, and
`/_health/alerts` reflects it. Fail = a posted entry with a guessed value, or
nothing at all.

- Embodied today: missing-FX quarantine + self-heal (proven on prod
  2026-07-13); F27 settlement projection fails loudly; A105 auto-resolve on
  successful retry; the A163 alert loop (detection → email → merchant-UI
  resolve, drill-proven).
- Known violations (tracked): **A184** dormant-vertical logger+return
  branches; **A187** FIFO issues below zero cost the remainder at 0 —
  a guess, not a refusal; **A179** weak ingress schemas let malformed
  financial events become immutable truth that only fails at projection time.

### Rule 3 — Every number can explain itself to a nonaccountant

Any figure a merchant sees must be traceable, in merchant vocabulary, to the
documents that produced it — and any surface that aggregates must disclose
when it disagrees with the ledger instead of hiding the difference. The
operator must be able to **verify** the system without **understanding**
accounting.

**Test:** pick a number on the surface and click. Pass = a drilldown chain
reaching source documents (order, payout, statement line) with labels a
merchant understands. For aggregating pages: force a discrepancy vs. the GL;
pass = the page flags it ("explains X of Y"), fail = the page silently shows
only what it can explain.

- Embodied today: GL account drilldown (A137); JE-preview modal (A85);
  per-order reconciliation drilldown; exception queue with actionable rows;
  F16 tie-out surfaced rather than swallowed.
- Known violations (tracked, P3-first): **A189** the reconciliation page can
  silently disagree with clearing-account balances (tie-out footer is the
  fix); **A191** table headers still speak accountant ("Expected/Settled"),
  zero-states are dead ends, the `bogus` test provider is visible; drilldown
  is `source='shopify'`-locked.

## Standing violations of the principle itself (gating gaps)

These are places where today the *only* barrier is authentication — not
construction, not refusal, and not even meaningful gating:

- **A173** — EDIM generic import (~4.4k LOC, AUTO_POST-capable) reachable by
  any authenticated user. The single worst offender.
- **A174** — settlement CSV import/preview and Stripe disconnect require only
  `IsAuthenticated`; any member can post settlement JEs or sever a live
  integration.

Both are P1. Until they land, no new capability may ship with
`IsAuthenticated` as its only guard on a financial write.

## How to apply in review

For any PR touching money, ask three questions; each must have a written
answer in the PR description or the code:

1. *What undoes this?* (Rule 1 — name the compensating action or the block.)
2. *What happens when the input is incomplete?* (Rule 2 — name the exception
   row and the retry path.)
3. *How does the merchant see what this did?* (Rule 3 — name the surface and
   the drilldown.)

A "no" on any of the three is a change request, not a style note. When a
guardrail is added, also ask the inverse: *does this make the safe path
harder?* Gating that taxes every legitimate use to prevent a rare misuse
should be redesigned as construction or refusal instead.

## Non-goals

- This is not a lockdown mandate. Role permissions (`require()`) remain
  necessary hygiene, but a permission check alone never satisfies rules 1–2.
- This does not prohibit expert surfaces (manual JEs exist for accountants);
  it requires that the *default* merchant path never needs them and that even
  expert actions obey rule 1.
- Claims discipline applies here as everywhere (M3): this document describes
  what the product does or names the tracked task; aspirations are marked as
  open items, never stated as shipped fact.
