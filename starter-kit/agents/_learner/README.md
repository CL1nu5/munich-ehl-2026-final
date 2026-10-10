# learner: negotiation with cautious learning inside one run

Code values terms and validates decisions; an optional model interprets the
supplier's messages and writes emails. LangGraph orchestrates each decision,
with an equivalent sequential fallback.

```text
prepare: dossier + claim catalogue
decide:  observe -> route -> close -> return acceptance immediately
                         -> diverse menu -> model or deterministic choice -> critic
result:  persist Arena result -> background reflection
```

## Decisions and evidence

Every term sheet in the supplier's latest message is valued, but only the
standing (first) sheet can be accepted: the Arena rejects an alternative's ID.
A cheaper alternative is mirrored back as our own sheet, which the supplier
answers with "agreed". Older sheets are not accepted. The ledger, offer builder and last-ask comparison share the
same valuation function. Known buyer costs and the estimated relationship
penalty are exposed separately. Supplier costs, financing and effort remain
assumptions; the public data cannot reveal the Arena's hidden economics. The
generic harness fallback evaluates known costs when it has no supplier model.

The packages of one round cost us within 0.3 % of each other and differ only
in what the supplier gets, because a supplier takes whichever package is
dearest for us. A lever the supplier has insisted on is repriced to the same
cost for us (an index clause comes with a lower base price). Code sizes every
step: we always move (a share of the remaining gap, more when the supplier
moved, most in the end game) and never jump to its offer; the model chooses
among equally priced packages, arguments and wording. Repeated counteroffers and
verbatim, model-interpreted statements create preferences with source evidence.
These preferences rank packages; they are distinct from binding terms and
must-haves. The model can propose allowed nonprice terms. Code computes their
price, checks cost and validates the resulting sheet. A generic should-cost
estimate cannot override an observed supplier price as a hard floor; an
own-plant estimate is calibrated and capped at the margin the opening quote
must carry. The first ask is ambitious but bounded: below the standing price,
at or below today's price in increases and renewals, below the cheapest rival
bid in an RFQ, never below the point where squeezing a relationship supplier
costs more than it saves.

The concession horizon uses actual remaining moves and wave time. There is no
practice-specific four-round cap. Breakdown questions are tracked as asked,
answered or declined, including requests in model-written prose.

## Learning and isolation

`services.history()` supplies only the owning run's confirmed results, tagged
with its run ID. Workers do not discover journals by scanning directories.
Each experiment keeps its configuration, state, attachments, model budget and
results in a separate journal. Source fingerprints include agent helpers and
dependency files, so changing these prevents an old run from resuming silently.

A single outcome creates a hypothesis, not a policy adjustment. Behavioural
changes require at least three distinct cases; scopes spanning suppliers also
require two distinct suppliers. Repeated rounds and overlapping archetypes do
not multiply support. Conflicting anchor evidence reduces confidence. Anchor
adjustments are bounded to ±10%. A final score and an LLM's retrospective
judgment do not establish which claim caused a concession; entire claim kinds
are never blocked by this mechanism.

The engine persists a confirmed result before invoking optional `on_result`.
Deterministic diagnostics are available to subsequent cases immediately.
Optional model reflection skips busy capacity and never delays acceptance.
Asynchronous completion order can still affect which confirmed cases are
available to the next decision; run isolation does not imply identical outcomes.

## Local usage

From `starter-kit/`, without Arena or model calls:

```bash
uv run --frozen arena-harness agents check --agent learner
uv run --frozen arena-harness run --config configs/learner.toml
uv run --frozen arena-harness batch --config configs/learner.toml --copies 5
uv run --frozen arena-harness resume-batch BATCH_ID
uv run --frozen arena-harness audit RUN_ID
uv run --frozen python -m agents._learner.inspect RUN_ID
```

The batch default is two sessions per run and ten total. Repeat `--config` for
different experiments instead of `--copies`. See [HARNESS.md](../../HARNESS.md)
for explicit hosted authorization, resource limits and recovery.

| Configuration | Purpose |
|---|---|
| `learner.toml` | Local deterministic simulator |
| `learner_openai.toml` | Optional remote model; requires `--allow-model` |
| `learner_final.toml` | Hosted settings; final remains one exclusive run |

`decision_seconds` at the top level and `[model].timeout` are authoritative;
the harness passes the effective remaining budget into each decision. Do not
duplicate these under `[parameters]`. Agent parameters include `llm`,
`two_call`, `memory`, `max_lessons` and `anchor_drop`. Legacy `round_budget` and
`runs_dir` parameters no longer control the learner.

## Evaluation limits

The simulator tests scheduling and protocol correctness; its scripted supplier
does not measure email quality or realistic negotiation strategy. Synthetic
tests vary IDs, price scales, offer order and nonprice conditions. Recorded
prefix audits expose missed choices without inventing subsequent replies or a
counterfactual score. A performance claim requires a later, separately
authorized evaluation on unseen cases. Empty memory alone cannot prevent
overfitting. See [EVALUATION.md](../../EVALUATION.md).
