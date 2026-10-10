# Negotiation regression and evaluation

The unchanged code baseline is git commit
`9ac429756b446ad56d8b30b56ccd325651a7e766`. The recorded practice run used for
diagnosis is `20261010T184935Z-e5c18603`: 10 completed cases, mean score
0.586004, 50 model calls, no recorded Arena timeouts and three document errors.
It completed operationally; negotiation choices, rather than a crashed run,
motivated this revision. Original run journals are retained unchanged.

## Regression evidence

The run exposed general failure modes: only the first current sheet was
considered, narrow cost filtering removed materially different contract terms,
generic should-cost estimates could dominate observed prices, reflection ran
before acceptance, and weak within-run evidence could become a broad rule.
Journal discovery and the single-run resource owner also prevented safe
independent parallel experiments.

Use `arena-harness audit RUN_ID` for a read-only replay. It evaluates available
offers before each recorded acceptance and ignores subsequent replies/results
when constructing that decision's alternatives. Cost differences describe
choices already on the table, not an estimated increase in final score.
Missing or previously unreadable quote documents can leave decisions unaudited.

The prefix audit of the diagnostic run found two cheaper current alternatives:

| Case | Difference in known buyer cost |
|---|---:|
| P07 | EUR 330.93 |
| P18 | EUR 1,141.29 |

P02's lower unit-price alternative was more expensive in total, so the audit
correctly retains the recorded choice there. These observations test offer
selection; they are not case-specific policy rules or score forecasts.

## Local acceptance checks

```bash
uv run --frozen pytest -q
uv run --frozen ruff check arena_kit/harness agents tests
uv run --frozen ruff format --check arena_kit/harness agents tests
```

Tests cover current-offer eligibility, stale IDs, ranking by full buyer cost,
different contract structures, evidence-backed preferences, should-cost
uncertainty, actual time horizons, confirmed-result learning and run isolation.
Synthetic fixtures use distinct development and additional regression seeds,
changing IDs, price scales and order. They check invariants, not score targets;
they are not an untouched real-world performance holdout.

Batch tests run five real learner engines against an in-process HTTP simulator,
using overlapping case IDs. They check ten-session capacity, shared pacing and
model limits, aggregate quota admission, isolated failures, restart after lost
session-open responses, and completed-batch resume without additional requests.
HTTP protocol fixtures use ASGI in process so these checks do not require a
listening socket. External network access remains blocked in default tests.

## Preventing evaluation leakage

The learner's policy contains no branches keyed to practice case or supplier
IDs. Numerical decisions use observed terms and public data, while uncertain
supplier costs remain labelled estimates. A current case never sees later
outcomes or another run's memory. Scores do not grant causal credit to tactics.
Learning counts independent cases and suppliers and bounds policy adjustments.

For a later performance evaluation, freeze the code and config first, reserve
unseen cases or supplier families, and compare the same case/order selections
with memory enabled and disabled. Repeat across seeds/orders and report score,
valid closures, timeouts, model cost and variation together. Do not tune on the
reserved set; choose a new untouched set after revisions. Concurrent cases can
finish in different orders, so exact equality of learned trajectories is not
promised.

No new hosted practice session or external model call is required by this
implementation's tests. Offline success supports correctness of the changed
mechanisms; it does not prove a stronger live negotiation score.

## Revision after practice run `20261010T194809Z-66d7a3ef`

That run (code `dadb021`) scored 0.524 against 0.586 for the diagnostic run. Its sessions,
replayed offline through `decide()` and compared with each result's `c_best` and cost breakdown,
showed four defects and one strategic gap:

| Finding | Evidence | Change |
|---|---|---|
| Accepting a supplier's *alternative* sheet ID is rejected (HTTP 422); the harness retried it until the round expired | P18: three 422s on `offer:70740-3-a`, then timeout, no deal (0.0); no alternative was ever accepted in 90 sessions | Only the standing (first) sheet is acceptable (as in the kit's `play.closing_move`); a cheaper alternative is mirrored back as our sheet; the engine logs 422 details and never retries a rejected ID |
| An own-plant should-cost above the quote emptied the menu: e-mails without term sheets | P02: `cost:350011` 374.95 vs quote 370.96 at a 0.5 % sustainable margin; five rounds of prose, score 0.08 | Should-cost is calibrated (results implied full costs ~2 % below it in P01, P02, P03) and capped at the margin the opening quote must carry; an empty menu falls back to a small price step on its own terms |
| Mutual standstill | P07, P11, P18: our concession share stayed 0.0; the supplier answered "no concession without reciprocal movement" and issued its final offer at its own price | We move every round (10 % of the remaining gap when it stands still, more when it moves, 50 % reciprocity); code sizes the step, the model only chooses among equally priced packages |
| Packages of equal value to the supplier but dearer for us | P05 14 days' payment, P08/P18 index clause, P10 56 days' lead time (EUR 33.6k late cost), P11 800 ppm without chargeback | Packages of one level cost us within 0.3 %; a lever the supplier insists on is repriced to the same cost for us (an index clause comes with a lower base price) |
| Anchors above the best attainable price | Implied `c_best` prices: P05 −25.6 %, P18 −22.5 %, P08 −15.7 %, P07/P10 −12.5 % vs. the opening; first asks were −4 % to −8 % | `anchor_drop` 0.10 → 0.18; at or below today's price in increase/renewal cases; below the cheapest rival bid in an RFQ; never below the price where squeezing a relationship supplier costs more than it saves |

The replays reuse the suppliers' recorded replies, so they check our moves, not the suppliers'
reactions to them. No practice session was used for this revision; the local simulator scores
0.787 against 0.759 before it (scripted supplier - not a live estimate).
