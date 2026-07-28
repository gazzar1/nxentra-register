# ADR-NNNN: <decision title>

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX | Rejected
- **Date:** YYYY-MM-DD
- **Decision owner:** <who is accountable for this decision and its removal trigger>
- **Related issue/PR:** <links>
- **Architecture rule affected:** <Rule 1–6 of docs/architecture/architecture-constitution.md, or "none — new area">

## Context

<What is true today, with file/symbol evidence. What forces the decision now
(defect, pilot evidence, requirement)? Per Rule 6, name the proof — not a
hypothetical.>

## Decision

<The decision, in one or two sentences. Then the concrete shape: exact
modules, boundaries, contracts.>

## Alternatives considered

<Each rejected alternative and the one-line reason it lost.>

## Consequences

<What becomes easier, what becomes harder, what debt is accepted knowingly.>

## Financial and operational invariants

<Which invariants this decision protects, changes, or newly introduces.
Reference the canonical implementation site.>

## CI fitness functions

<Which automated checks enforce this decision (architecture tests, checker
scripts, workflows) — or state honestly that enforcement is documentation-only
and name the ratchet plan.>

## Exception scope

<If this ADR grants an exception to a constitution rule: exact files/symbols
covered, risks, and the tests bounding those risks. Exceptions never cover
"the module" — they cover named symbols.>

## Migration and rollback

<How existing code/data moves to the decided shape; how to roll back if the
decision proves wrong.>

## Removal trigger

<The observable condition under which this ADR's exception or transitional
state must be removed (e.g. "when A3 lands", "when the last legacy row is
migrated"). An exception without a removal trigger is a permanent rule change
and must be written as one.>
