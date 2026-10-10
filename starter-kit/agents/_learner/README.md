# learner: a negotiation agent that learns within its run

Linus's concept. For every decision a small graph of sub-agents runs (LangGraph, with a plain
sequential fallback). Code computes every number; the model reads the supplier and writes. When a
case ends, the agent reflects on it, and the next cases **of the same run** start from those lessons.
Every run starts with an empty memory, so repeated runs on the same cases cannot overfit.

```
prepare (no model):   Dossier Builder · Claim Cataloguer · Run-Memory Reader
decide:  Observer -> Router -+-> Closer -> Reflector (LLM, if time)        -> accept / walk away
                             +-> Offer Builder -> Negotiator (LLM, 1 call)  -+-> Critic -> e-mails
                                               -> Strategist -> Writer (2)  -+
                                               -> Deterministic policy      -+
```

| Sub-agent | What it does |
|---|---|
| Dossier Builder (`dossier.py`) | SQL over the data package plus keyword tagging of notes, letters, news and quote remarks into supplier archetypes (`priors.py`: cash_short, price_frozen, hedged_forward, ...). Reads the Sunday release when present. |
| Claim Cataloguer (`claims.py`) | Data arguments that pass the arena's check, with the figure computed from `facts.json` (index pairs, scorecard, quotes, orders, should-cost, forecast, notice period). Each ID is cited once per case. |
| Run-Memory Reader (`memory/`) | Finds this run's journal (`.runs/<run>/run.sqlite`, read-only), extracts the finished cases, diagnoses them and merges the lessons. |
| Observer (`ledger.py`) | Per thread: standing offer and its cost C, concessions, statuses, claim results, attachments; checks our last prediction. |
| Router / Closer (`policy.py`) | Accept, walk away or negotiate, by fixed rules (never the model). |
| Offer Builder (`offers.py`) | Term sheets of equal value to the supplier (`cost.supplier_value`), cheapest for us first, at concession levels set by reciprocity and a deadline schedule. |
| Negotiator / Strategist / Writer (`prompts.py`) | The model picks packages and claims from the menu, predicts the reply, and writes the e-mail with placeholders. |
| Critic (`writer.py`) | Fills in the figures, strips leaks (urgency, budget, no alternative), stray figures and IDs, and validates the message. |
| Reflector (`graph.reflect`) | At the close: 1-4 lessons scoped to supplier, archetype, case type, goal or global, plus whether the lessons it used held. |

How the learning works:

- **Deterministic diagnostics** in `memory/diagnose.py`:
  - Pushed after a final offer and the supplier walked.
  - Walked away early.
  - Agreed to our first ask (we were too soft).
  - Contradicted claims, and claims that did or did not move the supplier.
  - When the supplier gave its final offer.
  - Cost components left on the table, from the exact score.
- **Recursion:** lessons gain or lose support from later outcomes. The Reflector's held/failed verdicts and the scores of cases that used a lesson both count. A lesson contradicted more than supported is retired.
- **Knobs:** lessons change behaviour even without the model, through knobs (`memory/consolidate.knobs`):
  - stop pushing final offers;
  - anchor harder or softer;
  - block a claim kind;
  - shorten the concession horizon.
- **Where memory lives:** only in the harness journal of the current run. The agent stores its reflection in its own returned state, which the engine journals. It never writes files.
- **Learning order:** within a Live Final wave all cases start together, so learning carries from wave to wave. Locally, `parallel = 1` or `2` in the TOML makes later cases see earlier ones.

## Run

From `starter-kit/`. The harness does not load `.env`, so export the keys from it first (nothing is printed):

```bash
export LLM_API_KEY="$(grep '^LLM_API_KEY=' ../.env | cut -d= -f2- | tr -d '"')"
export ARENA_API_KEY="$(grep '^ARENA_API_KEY=' ../.env | cut -d= -f2- | tr -d '"')"

uv run --frozen arena-harness agents check --agent learner                         # contract, offline
uv run --frozen arena-harness run --config configs/learner.toml                    # simulator, no model
uv run --frozen arena-harness run --config configs/learner_openai.toml --allow-model   # simulator + gpt-6-luna
uv run --frozen python -m agents._learner.inspect RUN_ID                            # what that run learned

# hosted (uses the Arena key; practice consumes quota)
uv run --frozen arena-harness doctor --profile practice --config configs/learner_final.toml
uv run --frozen arena-harness run --profile practice --config configs/learner_final.toml \
  --allow-arena --allow-model --max-cases 10
```

Configs:

| Config | Use |
|---|---|
| `learner.toml` | Simulator, no model, one case after another. |
| `learner_openai.toml` | Simulator with `gpt-6-luna` (reasoning effort medium), 2 cases in parallel. |
| `learner_final.toml` | Practice batches and the Live Final: 10 in parallel, 1500 model calls. |

The model configs have `model_timeout = 35` in `[parameters]`, equal to `[model].timeout` (35 s is the harness
maximum), and `decision_seconds = 45` both at the top level and in `[parameters]` (the agent does not read
the top-level value). A decision only starts a model call that can finish inside its budget, which is also
capped by the thread's 60 s answer window; otherwise it plays the deterministic policy.

Parameters (`[parameters]` in the TOML; defaults in `params.py`):

| Parameter | Effect |
|---|---|
| `llm` | Use the model at all. |
| `two_call` | Allow strategist + writer when the clock fits two calls. |
| `model_timeout` | Mirror of `[model].timeout`; the agent only starts a call that can finish in time. |
| `decision_seconds` | Mirror of the top-level `decision_seconds` (default 30); keep both equal. |
| `memory` | Learn from finished cases of this run. |
| `anchor_drop` | First ask below the standing value, before priors and lessons. |
| `round_budget` | Rounds we plan to use per thread. |
| `runs_dir` | Location of the runs directory, if not the repo's `.runs/`. |
| `data_dir` | Location of the data package, if not the repo's `data/`. |

Simulator scores only check the plumbing: the scripted supplier ignores tone, non-price terms and
claim figures.
