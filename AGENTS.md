# AGENTS.md — Nxentra agent operating manual

Canonical, always-load instructions for **any** agent (Codex, Claude, others)
working in this repository. This file is a **router and a list of
non-negotiables**, not an encyclopedia — it points at the authoritative
documents and states the rules you may not break. When this file and a linked
authoritative document disagree, **the linked document wins** and this file
must be corrected.

**Live code beats stale prose.** Where an authoritative document's prose
contradicts the **live code**, the code is authoritative until the discrepancy
is reconciled through a deliberate decision (ADR or the owning roadmap item).
Documents bind *intent and rules*; they do not override observed runtime
behavior, and you must not "fix" working code solely to match a doc claim that
a tracked item still contradicts. For **who writes a given financial fact**, the
[canonical money spine](docs/architecture/canonical-money-spine.md) (the writer
registry) and the live code are authoritative — not the illustrative model list
in the finance event-first policy, several of whose named models (e.g.
`SalesInvoice` — open **A110**; `BankStatement` — a documented Rule 1 hybrid)
are command-owned in code today. Known open discrepancies are recorded in
[NEXT_TASKS.md](NEXT_TASKS.md) / [docs/adr/](docs/adr/); follow the code and the
owning decision, not the stale line.

## What Nxentra is (and its current boundary)

Nxentra is a **reconciliation-first, event-sourced accounting platform** for
Shopify merchants, Egypt-first. The financial state is derived from an immutable
event stream; projections are read models, never sources of truth.

The only supported runtime posture today is the constrained pilot contract
**`ISOLATED_SHADOW_LEDGER_V1`** (single merchant, single-tenant deployment,
EGP-only ingestion, NON_STOCK inventory / Option B). **Real merchant data is
blocked** until the roadmap gates close — see the live tracker below. Do not
describe the core as "finished" or "production-proven": it is a strong
integrity foundation that is not yet cleared to hold real merchant data.

## Authoritative documents (the routing map)

| Concern | Authoritative source |
|---|---|
| Binding architecture rules | [docs/architecture/architecture-constitution.md](docs/architecture/architecture-constitution.md) |
| Canonical financial facts, writers, legacy paths | [docs/architecture/canonical-money-spine.md](docs/architecture/canonical-money-spine.md) |
| Supported product contracts | [docs/architecture/supported-product-contracts.md](docs/architecture/supported-product-contracts.md) |
| Event-first / write-barrier / projection policy | [docs/finance_event_first_policy.md](docs/finance_event_first_policy.md) (the `docs/` copy is canonical; the root `FINANCE_EVENT_FIRST_POLICY.md` is a superseded tombstone) |
| Architecture decisions (ADRs) | [docs/adr/](docs/adr/) |
| **Current constrained-pilot gate status** | [docs/status/constrained_pilot_status.md](docs/status/constrained_pilot_status.md) — the single source of truth for what is merged/open; check it before claiming any gate state |
| Working engineering practice | [ENGINEERING_PROTOCOL.md](ENGINEERING_PROTOCOL.md) (practice only — the constitution + ADRs + PR contract govern) |
| PR contract (enforced) | [.github/pull_request_template.md](.github/pull_request_template.md) |

Point-in-time audits, evaluations, and session logs are **historical, not
authoritative** — never follow them as current instructions.

## Non-negotiables (summarized from the constitution — the constitution binds)

1. **One canonical path per financial fact** — one identity, one representation,
   one authoritative writer, explicit derived readers. A second writer or a
   competing source of truth requires an accepted ADR.
2. **Provider logic stays outside the financial core** — adapters (Shopify,
   Stripe, Paymob, Bosta, banks) depend on the core; the core never depends on
   an adapter. A `source == "shopify"` condition inside core financial code is a
   violation.
3. **One invariant implementation per concept** — call the canonical
   implementation (e.g. `require_pilot_journal_currency`, the posted-journal
   validity boundary); never re-implement "just this once".
4. **Projections calculate; reactors orchestrate** — a projection transforms an
   event into derived state and does nothing else (no events, no external calls,
   no notifications, no swallowed financial failures).
5. **Posted journals go through the canonical emit boundary** — every
   `journal_entry.posted` emission goes through `emit_posted_journal` /
   `prepare_posted_journal_for_emit`; there is no other door.
6. **External vs internal are different trust models** — external sources
   produce untrusted evidence (auth, idempotency, raw preservation, quarantine,
   reconciliation); internal domain commands are trusted (permissions, lock
   order, admission, invariants). They may converge at the canonical fact — they
   must **never** share one generic "emit anything into the ledger" ingress.
7. **Refactor only around proven workflows** — the second provider pays for an
   abstraction, not the imagined tenth; characterize behavior before moving it.

Exceptions to any rule require an **accepted ADR** in `docs/adr/` (a code
comment is not an exception). Allowlists in
`backend/tests/test_architecture_rules.py` may shrink freely but never grow
without an ADR.

## Setup & test commands (backend)

Run from `backend/`. The default test DB is SQLite; PostgreSQL suites require
`TEST_DATABASE_URL`.

```bash
# Full SQLite battery (mirrors CI "Backend Tests (SQLite)")
python -m pytest tests/ accounting/tests/ events/tests/ accounts/tests/ reconciliation/tests/ \
  --ignore=tests/e2e/ --ignore=tests/test_truth_invariants.py \
  --ignore=tests/test_runtime_invariants.py --ignore=tests/test_control_invariants.py

# Architecture rules (blocking)
python -m pytest tests/test_architecture_rules.py

# PostgreSQL e2e / invariants (real concurrency proofs)
TEST_DATABASE_URL="postgres://<user>:<pw>@localhost:5432/<db>" \
  python -m pytest tests/e2e/ --reuse-db

# Statics (all must be clean)
ruff check . && ruff format --check .
python ../scripts/check-types.py          # strict mypy on the canonical spine
python manage.py makemigrations --check --dry-run
```

Full job set (Frontend build, Security & Deploy, Quality Gate) lives in
[.github/workflows/ci.yml](.github/workflows/ci.yml) — treat CI as the
authority on the complete gate.

## PR, review, and merge protocol

- Every PR body must satisfy the **PR Architecture Contract** (the template
  headings, including `## Out of scope` and a substantive `### Allowlist or
  ADR`). It re-runs on body edits.
- PRs get a **Codex review**. Do not merge with an open **P1/P2** finding, an
  unresolved review thread, a red check, or a moved head.
- Merge is **squash + `--match-head-commit <reviewed head>`**, never `--admin`,
  never force. After merging, verify tree-identity against the reviewed head and
  that post-merge `main` CI is green.
- **No migration, event-schema, or second-writer change** rides in silently —
  each is called out explicitly and, where it needs an exception, carries an ADR.

## Stop conditions

Stop and report instead of proceeding when: the reviewed head moved, `main`
moved unexpectedly, any required check is red, any review thread is unresolved,
or a change would introduce a second writer / generic ingress / provider→core
dependency without an ADR. **Merchant data stays blocked** until the tracker
says the gates are closed.
