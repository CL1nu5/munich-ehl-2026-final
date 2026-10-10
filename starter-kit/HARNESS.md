# Arena harness

One CLI for local experiments, explicitly authorized hosted runs and the final.
Supports Python 3.12 on macOS/Linux. Commands below run from `starter-kit/`.

## Install and run locally

```bash
uv sync --frozen --extra sim --group dev
uv run arena-harness doctor
uv run arena-harness agents list
uv run arena-harness agents check --agent example
uv run arena-harness run --agent baseline
uv run arena-harness run --agent example
uv run arena-harness compare RUN_A RUN_B
uv run arena-harness report RUN_A --format csv
```

The default `sim` profile starts a loopback-only simulator, plays S01–S03 and
shuts it down. It uses no Arena key or model. `--case` selects a case; `--max-cases`
bounds the selection. Results are in the repo's ignored `.runs/RUN_ID/` directory.
`report` writes `report.json` and prints JSON or CSV; run IDs or directory paths
work for comparison. Missing scores stay missing. Different profiles cannot be
compared; different case/data sets require `--allow-mismatch` and are labelled.
Simulator scores measure the scripted opponent, not expected live performance.

## Add an agent

Copy `agents/example.py` to `agents/my_agent.py`, keep the `Agent` class, then use
`--agent my_agent`. Put experiment settings under `[parameters]` in a TOML file
and pass `--config configs/my_experiment.toml`. Do not put keys in TOML.

```python
class Agent:
    def prepare(self, context):
        # context: {case, parameters}; runs before a session opens.
        return {"round": 0}  # explicit JSON state

    def decide(self, observation, state, services):
        # observation: case, all threads (including attachment text),
        # ready_suppliers, seconds_left per ready supplier, wave_seconds_left.
        # services.data: read-only query/lookup/document; services.cost(...).
        # services.complete(messages): centrally budgeted OpenAI-compatible call.
        return {"actions": [
            {"supplier_id": "sup:...", "message": {
                "subject": "...", "body": "...", "term_sheets": [{"terms": {}}]
            }}
        ], "state": {"round": state["round"] + 1}}
```

Use the actual supplier IDs and all negotiable terms from the observation; the
snippet illustrates the interface, not a valid offer. Return one message for
each ready supplier when doing an RFQ. Busy suppliers cannot receive a second
message. A closing action must be the only action in its batch:
`{"action": {"type": "accept", "term_sheet_id": "offer:..."}}` or
`{"action": {"type": "walk_away"}}` inside `message`.

The harness validates outputs and can replace an invalid/late decision with a
safe closure. Standard terms must be readable before acceptance. Cost estimates
do not reveal the supplier's hidden economics. Plugins are trusted Python code,
not an OS security sandbox; use `services.complete` for all model calls so limits
and metrics apply. Worker stdout is reserved for the harness protocol; ordinary
plugin prints are discarded. Keep durable state in the returned JSON.

Built-ins: `baseline` (batch RFQ example), `starter_naive`, `starter_llm`.
`agents check` validates three local opening observations without external calls;
model access is disabled there, so model-only custom agents should support a
mock/deterministic path. It is a contract check, not a strength evaluation.

## Hosted experiments and models

Set `ARENA_API_KEY` in the environment. The harness does **not** implicitly load
`.env`, and presence of keys never selects a live profile. `ARENA_URL` optionally
selects an HTTPS Arena host; default is `https://arena.tacto.ai`.

```bash
uv run arena-harness doctor --profile sandbox
# Consumes one real demo session; no model call with baseline:
uv run arena-harness run --profile sandbox --case case:D01 --agent baseline --allow-arena
# Practice also defaults to at most one session:
uv run arena-harness run --profile practice --case case:P01 --agent baseline --allow-arena
```

`doctor` performs read-only account/session/data checks, never opens a session and
never calls a model. Sandbox is outside the practice quota, but a configured
buyer model can still cost money. Increase `--max-cases` explicitly for a larger
practice batch. Preflight refuses unowned open sessions and insufficient quota.

For an LLM, copy `configs/model.example.toml`, select a real model and endpoint,
and set `LLM_API_KEY` in the environment. Configure positive `max_calls` and
`max_output_tokens`; `parallel` and `timeout` control the shared model gateway.
Optional `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`)
targets OpenAI reasoning models: the request then carries `reasoning_effort` and
uses `max_completion_tokens` instead of `max_tokens`. Without it the request is
unchanged.
Remote models require `--allow-model`, independently of `--allow-arena`.
Loopback HTTP endpoints are supported without remote-model permission.

Limits are runwide and survive restarts. Ambiguous model failures consume an
attempt and are not retried. Reports show provider-reported usage and unknown
usage counts. Optional token prices produce an estimate for **known usage**,
not a guaranteed billing cap. Internal multi-step agents share the same limits.

## Final and recovery

Use the current [official submission requirements](../brief/submission.md).
Load the official Sunday release into `data/release-sunday/`; overlapping fact
IDs are rejected. Commit the selected code, agent, configuration and data before
the freeze. Ensure the machine stays awake and connected. Only one machine per
Arena key should run live: the lock covers one OS user on one machine and does
not coordinate execution across different computers. The legacy `arena` CLI is
independent of this lock; use `arena-harness` for experiments and the final.

```bash
uv run arena-harness doctor --profile final --config configs/final.toml
uv run arena-harness run --profile final --config configs/final.toml \
  --allow-arena --allow-model --commit FULL_HEAD_SHA
# If the supervisor itself was interrupted, before human intervention is forbidden:
uv run arena-harness resume RUN_ID
```

Create `configs/final.toml` from the example and choose deliberate model limits
large enough for the final. The code must be clean and `--commit` must match HEAD.
Startup permission is persisted: the runner polls five waves and resumes without
confirmation dialogs. It never requests leaderboard/events/results/backoffice.
The final uses server deadlines, up to ten sessions, nine requests/second and
concurrent supplier requests. Quotes and worker decisions have bounded processing
budgets. Near the wave cap it closes conservatively.

The supervisor holds a per-user OS lock outside the checkout (under
`$XDG_STATE_HOME/arena-harness`, or `~/.local/state/arena-harness`) and restarts a
crashed engine at most three times. The engine inherits the lock descriptor, so
killing only the supervisor does not permit a competing run. Every POST is
journaled before sending; retries use its original `X-Move-Id`. Never delete a run
journal to retry a final case. Resume requires unchanged commit, code, agent and
data, plus the original Arena key. Config and remaining model budgets are restored.

First Ctrl-C requests controlled closure; a second forces interruption. After an
unresolved request, resume the same run instead of starting a fresh one. Exit 0
means all selected cases have results; exit 2 means incomplete/error. Review the
report and journal. Simulator pickle files are private local state; never import
untrusted ones. Machine/power/network failure and provider outages cannot be
eliminated by the harness; keep the supervisor running on the final machine.

## Validation

```bash
uv run --frozen pytest -q
uv run --frozen ruff check arena_kit/harness agents tests
```

Default tests block external sockets and skip hosted tests even when real keys
are set. `--arena-live` explicitly opts into the legacy hosted tests and can use
multiple sessions; it is **not** the single-case smoke test. Use the sandbox CLI
command above for that, only after explicit authorization. No live Arena or paid
model validation is implied by passing the offline suite.
