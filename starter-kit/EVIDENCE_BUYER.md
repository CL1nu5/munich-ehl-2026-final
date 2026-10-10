# Evidence Buyer

Research-backed negotiation agent for the existing team harness. Developed on top of
`18db44b` (PRs #3 and #4), informed by Jonas Huberts' data/agent guide in PR #2.
The agent represents the **buyer**, Hollerau Pumpenwerke, and negotiates with suppliers.

## Run

From the repository root, with Python 3.12:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e './starter-kit[sim]' pytest
.venv/bin/python starter-kit/run_evidence.py check
.venv/bin/python starter-kit/run_evidence.py local
```

For the existing team environment, `uv sync --frozen --extra sim --group dev` from
`starter-kit/` is also supported. Then use `uv run python run_evidence.py local`.

`local` runs only the local simulator and makes no model calls. `model` uses the
same simulator plus OpenAI, capped at 12 model requests per run, 512 output tokens
per request and a 15-second request timeout. It does not consume Arena practice.

```sh
.venv/bin/python starter-kit/run_evidence.py model
```

The key is read from ignored `starter-kit/.env.local` as `OPENAI_API_KEY` and mapped
to the harness's `LLM_API_KEY`. Existing environment variables win. Never put keys
in commits, screenshots, prompts or submission descriptions. The key does not
transfer EHL promotional credits: the connected OpenAI project needs its own
usable billing/credits. One connection test succeeded on 2026-10-10.

This launcher has **no hosted practice mode**. The shared generic harness remains
available to teammates with its own explicit authorization flags. No hosted
practice requests were used to develop this agent.

## What is implemented

1. Build a cached local dossier from supplier master data, financial context,
   recent negotiation history, contracts, clauses, recent correspondence and
   direct case references. Case-specific facts override general facts.
2. Search permitted terms in a bounded beam (48 packages per stage, up to 32
   choices per field), filter mandatory constraints, and solve the price for a
   common buyer-cost target. Offer up to three distinct, complete packages.
   Equivalence is approximate because unit prices have two decimal places. For strategic
   cases with a usable cost estimate, a separate minimax search ranks packages by
   worst estimated total cost, including relationship penalties.
3. Keep an opening-based target with gradual concessions. The initial reduction
   is a tunable heuristic: 12% of opening total cost, or 6% for strategic/supply
   security goals. These are **not research-derived optimal percentages**.
4. The optional model reads dossier/recent messages and selects a relevant
   conversational opening and question from a small permitted vocabulary. It
   cannot write prices, acceptance actions, arbitrary promises or evidence claims.
5. Code renders at most one exact numerical claim per message, with its source ID
   in the same sentence. Used evidence IDs are tracked across the case. Model
   output cannot add citations or invent statistics. Current claim templates
   cover delivery scorecards and index observations, not causal cost arguments.
6. Default tone is warm professional English, matching the kit's case language.
   A restrained paperwork remark is eligible only once, on the second buyer reply,
   after a positive supplier signal. It is suppressed for quality/capacity cases and
   detected urgency, complaints, delays or dissatisfaction. No invented shared interests or personal research.
   This deliberately limited language palette should be expanded only alongside
   corresponding promise/claim checks.
7. Standing offers are accepted explicitly through the existing common harness
   safeguards: current offer ID, readable quote, required fields, must-haves and
   buyer alternative. Hard unit-price and total-cost limits and the risk estimate apply
   consistently to ordinary acceptance, model failure and deadline closure. A final
   quote that violates a mandatory condition can receive one checked counterpackage
   before walking away. RFQs wait for outstanding replies instead of closing solely
   because one supplier said agreed. Near a wave deadline the harness safely closes.
8. Model failure, malformed JSON, injection-like output, unavailable credentials
   or exhausted model-call budget leads to deterministic language. The harness
   separately journals transport failures and recovers durable move IDs.

## Boundaries and limitations

The three simulator suppliers are scripts. They do not establish performance
against hosted bots, and they cannot validate the economic effect of tone.
No promised win rate, ranking, or hidden-final score follows from local results.

The search is bounded and not a global optimizer. Supplier acceptance is uncertain.
Should-cost is normalized by `price_unit`; its error band produces low/high cost
scenarios, combined with financing-rate sensitivities of half/twice the buyer rate.
The worst of these four estimates drives strategic offer ranking and every closing
path. Rates are assumptions, not verified supplier facts or guarantees. Candidate
prices are searched on a 41-point grid. No probability-calibrated risk claim is made.

Missing/invalid cost estimates and bundles without part weights are explicitly
recorded in agent state; no supplier margin is invented. Those cases use nominal
buyer cost and the more cautious strategic anchor. This is a disclosed coverage
limit, not a claim that every hidden supplier's economics can be recovered.

`target_unit_price` is an aspiration. `max_unit_price` and `max_total_cost` are hard
limits, also enforced on outgoing proposals and emergency closure. They are optional
and can be set per case under `[parameters.case_limits."case:ID"]`. Do not apply a
single arbitrary unit-price limit to unrelated parts. All amounts use the case's
unit basis; source should-cost tables are separately normalized. The system never
raises a hard limit to manufacture a deal.

The market state is frozen by the challenge. Live web statistics in the research
note below inform design only; they are not supplied as Arena evidence. The model
has no browser, external tools, secret access, or access to leaderboard/event feeds.

## Scientific basis and actual statistics

These are human experiments, not experiments on Tacto's bots. Effects are tied
to each study's task, sample and intervention. We do not rank researchers as
"the best", and we do not convert effect sizes into promised savings.

| Finding | Original evidence | Application and limitation |
|---|---|---|
| Offer several economically comparable packages | Leonardelli et al. (2019), six experiments. Experiment 1: offerer outcome `t(35)=−2.40, p=.022, r=.38`; joint outcome `t(35)=−2.44, p=.020, r=.38`. Joint outcome was not significant in every subsequent experiment. | Offer up to three meaningful choices. This is not proof that three is always optimal. [Paper](https://research-management.mq.edu.au/ws/portalfiles/portal/132609298/132287967.pdf), pp. 68–69. |
| Understand the counterpart's constraints | Galinsky et al. (2008), three studies: perspective-taking helped find hidden agreements and create/claim resources; emotional empathy did not provide the same consistent advantage. | Use supplier context to ask about payment, capacity or quality. No personality diagnosis. [Original abstract](https://pubmed.ncbi.nlm.nih.gov/18399891/). |
| Rapport can improve social outcomes | Morris et al. (2002), Study 2: rapport 5.34 vs 3.78, `p<.001`; impasse 40.6% vs 60.7%, but only marginal `p<.10`; joint points difference `p=.15`. | Warm opening and genuine tradeoff questions. A friendly sentence is not equivalent to the study's five-minute phone call, and the economic result is not conclusive. [Paper](https://www.law.northwestern.edu/faculty/fulltime/nadler/Morris_Nadler_SchmoozeOrLose.pdf), pp. 95–97. |
| Humor is context-sensitive | Bitterly, Brooks & Schweitzer (2017), eight studies on humor and status. Appropriateness and audience reaction change the direction of the effect. | Sparse optional humor, no joking about defects or shortages. These studies measure status, not procurement savings. [Paper](https://faculty.wharton.upenn.edu/wp-content/uploads/2017/03/2016-54537-001.pdf). |
| Anchors matter; preparation can counter them | Galinsky & Mussweiler (2001), three experiments on first offers and negotiator focus. | Track an explicit target and alternative rather than blindly following every counteroffer. Our numerical concession schedule is an engineering heuristic. [Original research](https://business.columbia.edu/faculty/research/first-offers-anchors-role-perspective-taking-and-negotiator-focus). |

## Local evaluation, 2026-10-10

Same three S01–S03 cases and dataset; all three completed in every run:

| Variant | S01 | S02 | S03 | Mean |
|---|---:|---:|---:|---:|
| Initial Evidence Buyer | .6287 | .6173 | .5470 | .5977 |
| Existing baseline | .8694 | .8592 | .2909 | .6732 |
| Slower concessions | .7948 | .7813 | .6920 | .7561 |
| Slower concessions + OpenAI | .7948 | .7813 | .6920 | .7561 |

The change improves the three-case mean by 8.29 percentage points over baseline,
mostly through S03; baseline still wins S01 and S02. This is a tiny development
set, not held-out validation or statistical significance. Model and offline
scores match because the scripted supplier does not reward nuanced language.
The model run used 12 calls; it is not evidence of an LLM economic uplift.

Regression coverage includes invalid numeric prices, malformed/malicious model
responses, model timeout, short deadlines, complete/equivalent offers, must-haves,
unique evidence IDs, RFQ waiting, real dossier retrieval, and the original harness's
recovery, secret redaction, endpoint restrictions and rate-limit tests.
Raw journals are local and ignored under `.runs/`. Report using
`arena-harness report RUN_ID`; never feed final results back to the live agent.

## Expanded stress evaluation and research

See [professional policy and detailed assessment](PROFESSIONAL_POLICY.md) and the
reproducible `evaluations/stress_evidence.py`. Two independent local sets now cover
180 scenario instances, each run with two agents: 360 simulated negotiations.

| Synthetic set | Known feasible at tested final terms | Baseline deals | Evidence Buyer deals | Unsafe deals |
|---|---:|---:|---:|---:|
| Development, 60 combinations | 42 | 23 | 39 | 0 for both |
| New combinations, 120 | 79 | 48 | 73 | 0 for both |

Among paired deals, buyer cost changed by −0.73% in development but **+0.14%** in
validation. This does not show universal price superiority. The improved agent
finds more feasible packages, while retaining hard limits. The validation set
contains new combinations of the same five scripted opponent families; it does
not test unseen human behavior or hosted bots. Feasibility is evaluated at a
specific final-threshold price; it is not a global oracle for uncertain economics.
Full outputs: `evidence-stress.json` and `evidence-validation.json`.

## Before the actual final

Consult the current [official submission instructions](../brief/submission.md),
[API](../brief/api.md), [brief](../brief/brief.md) and [Arena FAQ](https://arena.tacto.ai/faq).

- Incorporate the official Sunday release when published, preserving base data.
- Choose a reviewed, clean commit and ensure code/config/data are included.
- Provide the team's existing `ARENA_API_KEY` through the local secret file.
  The OpenAI key is a different credential. Do not create a second Arena team.
- The final launcher uses the separate `evidence-final.toml`: at most 500 model
  calls, 512 output tokens per call, 15-second timeouts and ten parallel cases.
  Local model tests remain capped at 12 calls. This is a call cap, not a euro
  spending cap. Once exhausted the agent remains deterministic. Final was not run.
- Verify Entire checkpoint linkage and EHL reviewer repository access.
- Use the actual frozen commit in the submission and launch before the final:
  `.venv/bin/python starter-kit/run_evidence.py final --commit FULL_COMMIT_SHA`.
  This starts real final sessions, requires the Arena credential and a clean checkout,
  and was intentionally **not executed during development**.
- Final waves are autonomous. Do not inspect or consume event/screen/leaderboard/
  result feeds to steer the running agent. Have the selected machine remain awake.

No submission was made on behalf of the whole team, and no claim is made that
reviewer permissions, Sunday data or final credentials are already configured.

Data attribution: Tacto Negotiation Arena, Tacto Technology GmbH, CC BY 4.0;
market series retain the separate terms listed in `data/ATTRIBUTION.md`.


## Readiness and future data updates

Run `.venv/bin/python starter-kit/run_evidence.py ready` from the repository root.
This makes zero network requests and prints key presence only, release presence,
source cleanliness, data hash and the enforced model limits. It does not validate
credentials or claim final submission completion.

New case/part/supplier IDs require no whitelist changes. Fixed-price negotiations
now search the remaining negotiable conditions. New allowed fields and must-haves
are retained; changes to the published cost function or API require code updates.
Missing history remains unknown. Load Sunday data before starting the final:
inside `data/release-sunday`, beside the data directory, or via `ARENA_RELEASE`.
All resolved release files are hashed for recovery; duplicate base fact IDs fail.
New case waves are discovered automatically, but local data is frozen during a run.
Old run journals from before this data-hash change cannot be resumed; start a new
local experiment after updating, never start a duplicate final for this reason.

Both model configs now enforce 16,384 input bytes per request in addition to call
and output-token limits. GPT-4.1 mini prices are recorded for usage estimates:
$0.40/M input, $1.60/M output (official model page, checked 2026-10-10).
Latest bounded local test: 12 calls, 24,616 input / 243 output tokens, estimated
$0.0102352 before cache discounts. These are per-run request limits, not a shared
account spending cap. Budget exhaustion uses deterministic negotiation text.
Current verification: 95 offline tests passed; 2 hosted tests deliberately skipped.
See PROFESSIONAL_POLICY.md for detailed research, costs, new-data tests and limits.
