# Implementation overview

IncidentCourt is a local investigation workspace. Start with the [README](../README.md) for setup and an offline sample run.

## Execution flow

1. `run.py` starts the local server or executes a JSON input from the command line. Sample mode restores a SQLite database from the committed fixture without a data download.
2. `incidentcourt/server.py` serves the browser interface and API. `incidentcourt/workspace.py` stores case history, background-job progress, personal notes and review state in a private SQLite workspace, separate from generated reports.
3. `incidentcourt/investigate.py` compares metric windows, gathers logs and traces, challenges a hypothesis and builds a report with evidence and query receipts. Local mode uses deterministic heuristics. Nightshift mode uses planner, critic and explanation calls and rejects malformed outputs or invalid references.
4. `incidentcourt/store.py` provides the configured telemetry backend. Local SQLite and Zetaris are distinct modes; a remote failure does not silently fall back to SQLite. See [Zetaris](ZETARIS.md) for the table contract.
5. The H-MEM adapter invokes the vendored Meterless reference through `scripts/hmem_bridge.ts`. Eligible earlier observations can suggest services to check, but fresh telemetry must support current evidence. Current incidents and holdouts are excluded from memory. See [integrations](INTEGRATIONS.md).
6. The browser displays saved report data, including comparisons, coverage, dependencies, evidence and the query trail. Exploring visuals does not issue telemetry queries or model calls.

## Workspace and API

The workspace registry is stored in ignored `data/workspace.sqlite3` and partitioned by source. Run one server process per workspace database. Background investigations share an execution lock with the synchronous API, use idempotency keys to avoid duplicate dispatch, and are marked interrupted rather than automatically restarted after a server restart. Imported reports remain unverified and unlinked to case-queue status.

The server exposes JSON routes for cases, investigations, review, import, refresh and connection checks. The synchronous `/run` and `/api/investigate` routes remain available. See the [README](../README.md#persistent-workspace) for route names. The server checks loopback host names and rejects cross-origin POST requests; it is a local tool, not a public multi-user service.

## Evidence and reproducibility

The committed sample is a bounded subset of controlled RCAEval experiments; missing values and incomplete trace coverage remain visible. Offline sample behavior does not verify a remote provider or establish causal accuracy. See [data provenance](DATA.md) for sampling, units and licensing.

The October 25, 2026 live verification used real Zetaris queries and hosted Nightshift responses with the official H-MEM reference. Its scope, private receipts and model-output limitation are documented in [live verification](LIVE_VERIFICATION.md). Configuration or a successful metadata check alone does not establish an end-to-end live run. [Handoff](HANDOFF.md) records historical project-specific context.

Run the offline application suite from the repository root:

```sh
python3.12 -m unittest discover -s tests -v
```

Some HTTP tests bind a loopback port. The README also lists the upstream H-MEM smoke suite and optional real-data memory verification.
