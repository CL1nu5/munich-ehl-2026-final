# Python workspace

This directory contains the Python 3.12 project. Agent implementations are in
`agents/`, experiment configuration in `configs/`, and execution infrastructure
in `arena_kit/harness/`. The original starter-kit helpers are in `arena_kit/`.

## Install and check

Run these commands from this directory:

```bash
uv sync --frozen --extra sim --group dev
uv run --frozen arena-harness doctor
uv run --frozen arena-harness agents check --agent example
uv run --frozen arena-harness run --agent baseline
```

This uses the local simulator and makes no external Arena or model calls. Keys
in the environment do not enable a hosted run. The harness does not load `.env`.

## Develop an agent

```bash
cp agents/example.py agents/my_agent.py
cp configs/baseline.toml configs/my_agent.toml
```

Set `agent = "my_agent"` in the copied configuration. Implement `Agent.prepare`
and `Agent.decide`, then check and run it:

```bash
uv run --frozen arena-harness agents check --agent my_agent
uv run --frozen arena-harness run --config configs/my_agent.toml
uv run --frozen arena-harness compare RUN_A RUN_B
```

`baseline.toml` selects one case for a short experiment; the CLI without a config
plays all three simulator cases. Use `--max-cases 3` to compare all three with
your configuration. Compare matching case selections and data versions.

Use `services.data`, `services.cost` and `services.complete` for shared data,
cost calculation and budgeted model access. Keep state in the JSON returned by
the agent. Agent implementations and their configurations can be added
independently of the scheduler and transport.

## Navigation

| Location | Purpose |
|---|---|
| [`HARNESS.md`](HARNESS.md) | Agent contract, CLI options, model limits, final start and recovery |
| [`agents/example.py`](agents/example.py) | Copyable agent example |
| [`configs/`](configs/) | Per-experiment TOML; no keys |
| [`arena_kit/harness/`](arena_kit/harness/) | Scheduler, transport, workers, journal, validation and reports |
| [`arena_kit/`](arena_kit/) | Reusable cost, data, document and simulator helpers |
| [`tests/`](tests/) | Local contract and failure tests |
| [`REFERENCE.md`](REFERENCE.md) | Original library/CLI examples, index argument example and MCP setup |
| [`../brief/api.md`](../brief/api.md) | Official Arena protocol |
| [`../data/README.md`](../data/README.md) | Official procurement data package |

## Validate changes

```bash
uv run --frozen pytest -q
uv run --frozen ruff check arena_kit/harness agents tests
uv run --frozen ruff format --check arena_kit/harness agents tests/test_harness.py
```

Default tests block external connections. Hosted tests and hosted smoke runs
need explicit authorization; follow [HARNESS.md](HARNESS.md) for those commands.
The Dockerfile serves the original local simulator; use the Python/uv setup
above for the full harness and agent implementations.
