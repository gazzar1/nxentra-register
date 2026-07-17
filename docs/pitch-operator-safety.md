# Pitch: "Accounting software you can't break"

- **Audience:** M2 design-partner conversations (e-commerce merchants, nonaccountants)
- **Discipline:** every line here maps to shipped evidence (M3 rule: no claim without substance). The "do not say" list is as binding as the talking points.
- **Backed by:** [design-principle-operator-safety.md](design-principle-operator-safety.md)
- **Price to quote:** $29/store/month (owner decision 2026-07-11) — bring it up yourself; a pitch without a price is a demo.

## The fear you are answering

Merchants don't avoid accounting software because it's expensive. They avoid
it because they're afraid of it: *"I'm not an accountant — I'll mess up the
books, and then my accountant will charge me to redo everything."* Every
line below answers that fear, not a feature checklist.

## The one-liner

> "Nxentra is accounting software you can't break. It does the accounting;
> you just confirm what you see. And when something doesn't add up, it tells
> you — it never guesses and never hides."

## Three promises (merchant language ↔ what backs them)

### 1. "You can't delete or overwrite your books."

**Say:** "Every change in Nxentra is a new entry on top of the old one —
nothing is ever edited in place or quietly deleted. If you make a mistake,
you reverse it, and both the mistake and the correction stay visible. That's
what your accountant does with paper; Nxentra just enforces it."

**Backed by:** event-sourced immutable ledger (events are append-only);
reversal workflow with visible badges and cross-links (shipped 2026-07-01);
month-end close gate that blocks posting into closed periods (A152, shipped
2026-07-07).

**If they push:** "Even we can't silently change your history — the system
rebuilds your books from the recorded events and checks they still balance."

### 2. "When something's unclear, it stops and asks — it never guesses."

**Say:** "Say a payout arrives in dollars and there's no exchange rate for
that day. Most tools would silently pick some rate and move on — and now
your books are quietly wrong. Nxentra refuses: it parks that item on your
exceptions list, tells you exactly what's missing, and posts it the moment
you fix it. Wrong numbers never enter your books by default."

**Backed by:** missing-rate quarantine + automatic retry, proven on
production books (2026-07-13: two stranded orders healed and self-resolved);
fail-loud settlement projection (F27); exceptions queue with a one-click
merchant-facing resolve flow; external uptime monitoring that emails a human
when anything is stuck (alert drill passed 2026-07-13, ~1-minute detection).

**If they push:** "We drill this. We plant a failure on purpose and time how
fast the system detects it, emails us, and recovers. Last drill: detected in
about a minute."

### 3. "Every number can explain itself."

**Say:** "Click any figure — an account balance, a payout, a fee — and
Nxentra shows you the orders, payouts, and bank lines behind it. You don't
need to understand debits and credits to check your own books; the
reconciliation page reads like your business: what you sold, what the
gateway paid you, what reached the bank."

**Backed by:** GL account drilldown (A137); journal-entry preview before
posting (A85); per-order reconciliation drilldown; canonical fees tile.

**If they push:** show, don't tell — the 3-stage reconciliation page on a
real store is the demo.

## Supporting facts (safe to state, with dates)

- Listed on the Shopify App Store (approved 2026-06-16).
- Daily managed-database backups with 7-day point-in-time restore, **and** a
  tested per-company restore: full drill passed 2026-07-13 with the books'
  integrity checks verified (trial balance, subledger tie-out).
- Store credentials encrypted at rest (shipped 2026-06-23); Shopify GDPR
  data requests handled end-to-end (evidence run 2026-07-13).
- Multi-currency done conservatively: foreign amounts convert with a real
  rate or wait in exceptions — never silently 1:1.

## Do NOT say (M3 claims discipline — unshipped, unsubstantiated, or false)

- ~~"98.8% match rate"~~ — unsubstantiated; remove wherever it survives.
- ~~"Universal reconciliation" / "works with every provider"~~ — Shopify +
  Stripe + CSV today; say exactly that.
- ~~"Encrypted backups"~~ — backup archives are not encrypted; say "daily
  backups with a tested restore" instead.
- ~~"The page flags anything it can't explain"~~ — that's A189, not shipped
  yet. Until it lands, say "click through any number" (true) and nothing
  stronger.
- ~~"Fully bilingual"~~ — interface language exists; the flagship pages are
  not fully translated.
- No invented testimonials, ever.

## The honest-vendor close (use it — it's the differentiator)

"We publish our own safety rules and we audit our own product against them —
including the list of things it doesn't do well yet. Ask me for the gaps and
I'll show you the actual list. You're one of three design partners; the
things you hit get fixed first."
