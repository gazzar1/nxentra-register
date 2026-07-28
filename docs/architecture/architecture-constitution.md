# Nxentra Architecture Constitution

Binding rules for changes to this repository. Short by design: six rules, an
exception policy, and an honest statement of what is and is not enforced today.

- **Status:** Adopted via [ADR-0003](../adr/0003-architecture-constitution-governance.md) (Accepted; binding on `main` from the merge of PR #109)
- **Scope:** all code that creates, transforms, or reports financial facts
- **Companion documents:** [Canonical money spine](canonical-money-spine.md) ·
  [Supported product contracts](supported-product-contracts.md) ·
  [Finance event-first policy](../finance_event_first_policy.md) ·
  [Operator-safety design principle](../design-principle-operator-safety.md)

---

## Rule 1 — One canonical path for every important financial fact

Every important financial fact (an order, a refund, a settlement, a bank line,
a match, a posted journal entry) must have:

- **one canonical identity** (a stable ID or deterministic idempotency key);
- **one canonical representation** (the event and/or model that other code
  treats as the truth);
- **one authoritative writer** (the single command/projection permitted to
  create or mutate it);
- **explicit derived readers** (projections and reports declare that they are
  derived, not authoritative);
- **explicit treatment of legacy representations** (a legacy table or path is
  named as legacy in the [canonical money spine](canonical-money-spine.md),
  with its disposition — retained, frozen, or scheduled for removal).

"One canonical path" does not mean only one table exists. It means one
authoritative interpretation and one permitted route into financial truth.
Introducing a second writer or a competing source of truth requires an
accepted ADR.

## Rule 2 — Provider-specific logic stays outside the financial core

Shopify, Stripe, Paymob, Bosta, banks, and every future provider adapter
translate external data into canonical Nxentra contracts (events, canonical
models, settlement payloads).

- Core event, journal, reconciliation, and financial-invariant code must not
  depend on provider-specific modules.
- Provider adapters may depend on the financial core. The financial core may
  not depend on provider adapters.
- A provider condition (`source == "shopify"`, a Stripe-shaped field) inside
  core financial code is a rule violation, not a convenience.

## Rule 3 — One invariant implementation per financial concept

Each financial invariant has one canonical implementation. Examples:

- posted-journal validity (balanced lines, postable accounts, open period);
- settlement equations (gross = net + fees + uncollected);
- source identity and idempotency (deterministic keys, dedup);
- currency acceptance (which currencies a deployment may ingest);
- reconciliation match identity (what constitutes the same match);
- reversal and correction validity.

Views, connectors, and projections must call the canonical implementation.
A local re-implementation ("just this one check inline") is a violation even
when it is currently equivalent — equivalence decays.

## Rule 4 — Projections calculate; reactors orchestrate

A projection transforms an event into derived state. Nothing else. A
projection must not:

- contact an external provider;
- emit another event;
- enqueue background work;
- send notifications;
- initiate an unrelated business command;
- silently absorb a financial failure (failures are recorded loudly, e.g. the
  projection failure log — never swallowed).

Follow-on workflow ("when X happens, do Y") belongs in an explicit
reactor/process manager, named and testable on its own.

## Rule 5 — Supported product contracts are versioned

Supported capability combinations are represented by explicit, versioned
contracts such as `ISOLATED_SHADOW_LEDGER_V1`
(see [supported product contracts](supported-product-contracts.md)). Each
contract defines:

- included capabilities;
- excluded capabilities;
- runtime gates (the enforcement — not UI hiding);
- activation preflight;
- go-live preflight;
- acceptance tests;
- graduation requirements to a broader contract.

Scattered, unrelated feature booleans are not a product contract and must not
replace one.

`pilot_profile = NONE` means **no constrained profile is selected**. It does
not mean the company is certified as production-ready; standard/shared
deployment readiness is governed by the roadmap gates (A3, A5, G1, G2), not by
the absence of a pilot profile.

## Rule 6 — Refactor only around proven workflows

A material refactor must be justified by at least one of:

- a confirmed defect;
- real pilot evidence;
- repeated founder intervention;
- a repeated change across workflows;
- a real customer requirement;
- a measured performance or maintainability problem;
- a security or accounting invariant.

Characterization tests must protect existing behavior before code is moved.
Speculative platform rewrites and abstractions for hypothetical future
providers are out — the second provider pays for the abstraction, not the
imagined tenth.

---

## Exceptions and ratchet policy

- An exception to any rule requires an **accepted Architecture Decision
  Record (ADR)** in [docs/adr/](../adr/). A code comment alone is not an
  architectural exception.
- Existing allowlists (e.g. in
  [backend/tests/test_architecture_rules.py](../../backend/tests/test_architecture_rules.py))
  may **shrink** at any time. They may **not grow** without an ADR and a
  removal plan.
- Every ADR exception identifies: exact files/symbols, risks, tests that
  bound the risk, an owner, and a removal trigger.

## Current enforcement status (honest)

This constitution documents intent. Enforcement is partial and is being
ratcheted — the table below is the truth, not the aspiration. Current
architecture debt has **not** been eliminated by adopting this document.

| Rule | Enforcement today |
|---|---|
| Rule 1 — canonical path | **Documented now** (the [money spine](canonical-money-spine.md) names writers, readers, and legacy paths). Stronger writer enforcement (central posted-JE validation, durable terminal outcomes) belongs to **A3/A5**, which remain open. |
| Rule 2 — provider/core direction | **Partially enforced.** Known violations are documented in the money spine (e.g. `source == "shopify"` conditions inside core reconciliation views). Additional dependency checks will be introduced incrementally. |
| Rule 3 — one invariant implementation | **Central posted-JE enforcement is A3 and remains open.** Some invariants are already canonical (settlement equation guard, pilot currency acceptance in `accounts/pilot_policy.py`); others are duplicated and documented as debt. |
| Rule 4 — projection purity | **Partially enforced** by existing architecture tests ([test_architecture_rules.py](../../backend/tests/test_architecture_rules.py): projections must not emit events; views must not enter projection write contexts; guarded field writes). Remaining projector/reactor cleanup is incremental. |
| Rule 5 — versioned contracts | **Materially enforced for `ISOLATED_SHADOW_LEDGER_V1` by A4** (merged PR #107): runtime gates at shared boundaries, transactional audited activation, exhaustive setup/go-live preflight, ~80 acceptance tests. |
| Rule 6 — evidence-gated refactors | **Begins with the PR contract** (`.github/pull_request_template.md` + the `PR Architecture Contract` check): every PR must state its evidence. |

**Advisory-first rollout:** the `PR Architecture Contract` workflow runs on
every pull request and fails loudly on non-compliance, but it is **initially
advisory** — it does not block merges until a later GitHub ruleset /
branch-protection step marks it as required. The existing `main` Quality Gate
is not modified by this governance baseline.
