# Implementation overview

IncidentCourt is a local investigation workspace. Its browser interface, Python HTTP server, investigation pipeline and telemetry adapters run together; external services are used only when configured. Start with the [README](../README.md) for setup and an offline sample run.

## Execution flow

1. `run.py` starts the local server or executes a JSON input from the command line. Sample mode restores a SQLite database from the committed fixture without downloading data.
2. `incidentcourt/server.py` serves the interface and API. `incidentcourt/workspace.py` keeps case history, background-job progress, personal notes and review state in a private SQLite workspace. Generated reports remain separate from notes and review state.
3. `incidentcourt/investigate.py` compares metric windows, gathers logs and traces, challenges a hypothesis and builds a report with evidence and query receipts. Local mode uses deterministic heuristics. Nightshift mode uses structured planner, critic and explanation calls; malformed outputs or invalid references fail explicitly.
4. `incidentcourt/store.py` provides the configured telemetry backend. Local SQLite and Zetaris are distinct modes; the application does not silently fall back to SQLite after a remote failure. See [Zetaris](ZETARIS.md) for the remote table contract.
5. The H-MEM adapter in `incidentcourt/integrations.py` invokes the vendored Meterless reference through `scripts/hmem_bridge.ts`. Eligible earlier observations can suggest services to check, but fresh telemetry must support current evidence. Current incidents and holdouts are excluded from history. See [integrations](INTEGRATIONS.md) for provenance and limitations.
6. The browser displays saved report data, including comparisons, coverage, dependencies, evidence and the query trail. Visual exploration does not issue new telemetry queries or model calls.

## Workspace and API

The workspace is source-partitioned and stores its registry in ignored `data/workspace.sqlite3`. Run one server process per workspace database. Background investigations share an execution lock with the synchronous API, use idempotency keys to avoid duplicate dispatch, and are marked interrupted rather than automatically restarted after a server restart. Imported reports remain unverified and unlinked to case-queue status.

The server exposes local JSON routes for cases, investigations, review, import, refresh and connection checks. The older synchronous `/run` and `/api/investigate` routes remain available. See the [README API overview](../README.md#persistent-workspace) for route names and request fields. The server checks loopback host names and rejects cross-origin POST requests; it is designed as a local tool, not a public multi-user service.

## Evidence and reproducibility

The committed sample is a bounded subset of controlled RCAEval experiments; missing values and incomplete trace coverage remain visible. A sample investigation is useful for offline development, but it does not verify a remote provider or establish causal accuracy. See [data provenance](DATA.md) for sampling, units, leakage boundaries and licensing.

The October 25, 2026 live verification used real Zetaris queries and hosted Nightshift responses with the official H-MEM reference. Its scope, receipts, known model-output limitation and exhausted usage allowance are documented in [live verification](LIVE_VERIFICATION.md). Configuration or a successful metadata check alone does not establish an end-to-end live run. [Handoff](HANDOFF.md) records historical project-specific setup and delivery context; verify time-sensitive state before relying on it.

Run the offline application suite from the repository root:

```sh
python3.12 -m unittest discover -s tests -v
```

Some HTTP tests bind a loopback port and require an environment that permits local sockets. The README also lists the upstream H-MEM smoke suite and the optional real-data memory verification.
