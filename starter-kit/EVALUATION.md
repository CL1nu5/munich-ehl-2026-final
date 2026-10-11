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

## Revision after practice run `20261010T203905Z-7eb05c71`

That run (code `84c3940`) scored 0.649. Its traces, the suppliers' back-office logs (`GET
/v1/sessions/{sid}/backoffice`, after the end) and the replies of all four hosted runs of the day
were read across cases. Changes are limited to patterns seen with several suppliers, not to one case:

| Finding | Evidence | Change |
|---|---|---|
| Citing a *rising* index confirms the supplier's case | "Your figures confirm our case: that driver rose" after LCI-C +2.7 % in P01, P02, P04, P05 (14 replies) | Index claims only for a fall: the steepest window of 3-12 months ending in the series' last 6 months, at least -1 % (e.g. AS-14404 -5.5 % 2026-06 → 2026-09 against a supplier's "+5.9 % in twelve months") |
| Price history does not move suppliers | 0 of 23 `po` citations moved a supplier (seven suppliers): "it holds, but it does not change our calculation" | `po` claims rank last; the playbook says so |
| Volume questions unanswered | "Which volume can you commit to? Please send the exact figures" in P02, P04, P05, P11, P18; our prose stayed vague; in P05 the cheapest bidder (back office: about to make a final offer) withdrew after the next vague reply | Code answers in figures from the sheets: "Our binding volume is 6,110 pc a year over 12 months, ..." |
| Broken e-mail text | "Our recent orders ... were at Our recent orders ...", "consider the alternatives in We could agree ...", the same question twice, "Dear Mr.\nSeidl," | A claim placeholder replaces its whole sentence; packages get their own lines; reworded repeat questions are dropped; the question filter keeps lines whole; templates greet the signer by name and no longer ask about costs after a refusal |
| An insisted index clause was dropped again | P08, P18: "the index clause must remain in place", yet rounds 3-4 offered no clause: the insistence counter reset when we offered the clause once, and the insisted deadband was missing from the pruned menu | The counter survives our matching it; an insisted clause accepts any allowed deadband; a missing combination is built from the cheapest sheet and repriced |
| No index clause was ever tried unless demanded | `supplier_value` counts cash only, so a clause always looked dearer | Opening round: one sheet with the widest-deadband clause at the same cost for us, when the case allows it and the supplier quoted none (not in P02: there our relationship cost makes it dearer) |
| Repricing failed for relationship goals | `price_for_cost` bisected from half the price, where the margin penalty makes C non-monotonic | Bisection starts from the cheapest price on a grid |
| One more round for almost nothing | P05: the cheapest bidder sat 0.7 % above our ask and walked after our next step | Accept when the offer is within 5 % of the way from the opening cost to our last ask |
| `audit` replayed round 0 from the structured `opening` (no term sheets, no PDF) | offline replays showed e-mails without packages in round 0 | `audit` uses the session's `threads` |

Not changed, but limiting: every supplier issues its final offer at a fairly fixed round (P10 round
2, P08 3, P02 4, P01 5-6 in all four runs, whatever our anchor), while our concession schedule
assumes up to ten rounds; and we never push a final offer without a safe fallback. No practice
session was used for this revision; the local simulator still scores 0.787.

## Recursive practice loop (night of 10-11 October)

Each iteration played all ten practice cases once with the full hosted setup (`learner_openai.toml`),
was compared with the last committed state and either committed or reverted. One run per change is
noisy: identical asks drew supplier replies whose scores differed by up to 0.25 in a case (P08: 0.47-0.72
with the same 4.59 first ask). Changes were therefore kept only when a pattern recurred across several
suppliers or a defect was reproduced offline (journal replay through `decide()`); no rule is keyed to a
case or supplier.

| Run | Change | Mean | Kept |
|---|---|---:|---|
| `99ae196c` | the revision above (falling indices only, volume answers, near-ask acceptance) | 0.675 | yes |
| `123f3d95` | first ask 25 % below the standing price, smaller steps | 0.648 | no |
| `44606ca7` | the supplier's cheaper alternative levers carried into our packages; RFQ asks never above a rival's offer | 0.703 | yes |
| `91ad699b` | sheet-ID and validation fixes, 25 % anchor cap | 0.631 | fixes only (P18 lost to an arena 503, see below) |
| `41aa4864` | one argument per kind and message | 0.681 | yes |
| `e4b3ea96` | larger step after two holds; no model call with three suppliers waiting | 0.689 | yes |
| `bef03321` | 409 resync in the engine, no index claims to hedged suppliers, 25 % anchor cap | 0.672 | resync and hedged rule |
| `f9c789a3` | only levers that stay cheap for us at the supplier's own price | 0.678 | yes |
| `3b8b6cf9` | two arguments in every opening message, insisted values in robust sheets | interrupted (network) | no; the transport fix (no retry close to the deadline) kept |
| `697cf46d` | RFQ: a cheaper rival bid leads every message it is new in | 0.679 (RFQ 0.67 → 0.82) | yes |

Findings that drove the changes:

- **Anchors.** Asks more than 25 % below the supplier's current price drew "your offer is far below any
  calculation we can make" (five replies, three suppliers, none at 24 % or less) and a third of the usual
  first concession (6.05 → 5.76 instead of 5.36, 5.74 → 5.61 instead of 5.51, 36.0 → 32.5 instead of 30.5).
- **Concession timing.** Suppliers concede on their own schedule and issue a final offer after a few rounds
  (often after two "holds"); the size of our step did not change the size of theirs. After two holds the
  final offer usually came at the held price, so any agreement below it is a gain.
- **Levers.** Suppliers mostly answer with their own price on the levers we send. A lower price with 30
  days' payment came back as 430.01 with 30 days instead of 432.94 with 90 days (dearer for us). Packages
  are now kept only if their levers are also cheap for us at the supplier's own price.
- **Alternative packages reveal valuations.** A capacity-constrained supplier offered a longer lead time
  for 6 % less; we kept asking for the short lead time and its final offer dropped the alternative. Our
  next packages now carry the alternative's levers (snapped to allowed values) at the same cost for us;
  P10 rose from 0.57-0.59 in every earlier run to 0.66.
- **Arguments.** A second argument of the same kind in one message ("duplicate", "already taken into
  account") wastes one of three checked slots; price history never moved a supplier; index falls meet
  "not our reality" from suppliers whose material is hedged forward.
- **Arena timing.** The arena answers one session's messages one after another (replies after 36, 54 and
  72 s to three simultaneous messages), so the third RFQ bidder's 60 s window could expire. With three
  suppliers waiting we decide without the model. A 409 on one late thread used to close the whole case
  (the harness accepted a still-moving bid); the engine now refreshes all threads and continues. In one
  run an arena 503 was retried after the arena had already recorded our timeout and the supplier's final
  offer; the retried message read as a push after the final offer and the supplier withdrew.
- **Rival quotes in RFQs.** Bidders shown a *much lower* rival quote give half the step at once and the
  rest after checking it (back office: "I give half the step now and check the rest", then "Checked: give
  the rest of the step"). The three RFQ runs that scored 0.81-0.85 cited the rival in the opening message;
  the later ones (0.66) let the model choose and cited index and delivery data instead. The cheapest rival
  bid now leads every message to a dearer bidder in which it is new.
