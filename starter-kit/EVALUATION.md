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
