# Negotiation Arena

Python harness for buyer agents in the Tacto negotiation challenge. It supports
local experiments, agent comparisons and execution of the live final. Hosted
profiles require explicit authorization.

## Start locally

You need Git, Python 3.12 and [uv](https://docs.astral.sh/uv/). After cloning this
repository, run:

```bash
cd starter-kit
uv sync --frozen --extra sim --group dev
uv run --frozen arena-harness doctor
uv run --frozen arena-harness run --agent baseline
```

The default runs three scripted simulator cases with no external Arena or model
calls. Results, journals and reports go to the ignored `.runs/` directory at the
repo root. Simulator scores help compare local behavior; they do not predict
performance against live suppliers.

## Repository structure

| Location | Purpose |
|---|---|
| [`starter-kit/agents/`](starter-kit/agents/) | Agent implementations and a copyable `example.py` |
| [`starter-kit/configs/`](starter-kit/configs/) | Experiment configuration: agent, parameters, model and limits; no keys |
| [`starter-kit/arena_kit/harness/`](starter-kit/arena_kit/harness/) | API communication, execution, recovery and reporting |
| [`starter-kit/arena_kit/`](starter-kit/arena_kit/) | Existing cost calculation, data access, document parsing and simulator |
| [`starter-kit/tests/`](starter-kit/tests/) | Local tests; hosted tests require explicit selection |
| [`brief/`](brief/) | Official challenge, API and submission requirements |
| [`data/`](data/) | Official procurement data, facts, SQLite, contracts and notes |
| [`example_inputs/`](example_inputs/) | Recorded simulator inputs and attachments for inspection |
| `.runs/` (ignored) | Local run state and exports |

There is one Python project, with its `pyproject.toml` and `uv.lock` in
`starter-kit/`. Run Python commands there. New agent implementations belong in
`starter-kit/agents/`; `starter-kit/arena_kit/agents/` contains the original
starter examples, exposed through harness adapters.

## Development

Follow the [Python workspace guide](starter-kit/README.md) to add an agent and
configuration. Keep each approach in its own agent/config pair, run it locally,
and compare matching cases and data versions using `arena-harness compare`.
Changes go through a feature branch and pull request; commit agent code and
configuration so runs can be reproduced. Do not commit keys, `.env` or run state.

The harness permits one run per OS user and machine. Coordinate hosted runs
across machines using the same Arena key and quota. The original `arena`
CLI has separate execution and configuration; use `arena-harness` for
experiments and final execution.

## Hosted runs and the final

[HARNESS.md](starter-kit/HARNESS.md) documents the full CLI, agent interface,
model limits, final start and recovery. Hosted sessions require `--allow-arena`;
remote models separately require `--allow-model` and configured call/output
limits. Keys come from `ARENA_API_KEY` and `LLM_API_KEY` in the environment.
Having a key does not activate a hosted profile, and the harness does not load
`.env` automatically.

Before the final, commit the selected agent, configuration and data, check a
clean checkout and use `--commit` with the full HEAD SHA. Start before the first
wave and follow the [final/recovery instructions](starter-kit/HARNESS.md#final-and-recovery).
The final runs without confirmation dialogs. Passing local tests does not
validate the live connection; perform a hosted smoke test only after explicit
authorization.

## Challenge and reference

- [Official brief and rules](brief/brief.md)
- [Official API protocol](brief/api.md)
- [Submission and pitch requirements](brief/submission.md)
- [Data package](data/README.md)
- [Original starter library examples and MCP setup](starter-kit/REFERENCE.md)
- [Upstream starter repository](https://github.com/tacto-ai/negotiation-arena-kit)

Code: [MIT](LICENSE). Data and official docs: [CC BY 4.0](LICENSE-data).
