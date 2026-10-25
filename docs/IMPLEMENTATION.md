# First vertical slice

Work in `/Users/frank/mygit/IncidentCourt`. There is no remote as of 2026-10-25.

1. Import three bounded RCAEval RE2-OB cases, pin source revision and file hashes, normalize telemetry, and isolate evaluation labels and original case paths.
2. Serve a local Python application with an auditable read-only query layer. Local SQLite is explicitly an offline development mode; Zetaris is a separate configured backend, with no silent fallback.
3. Rank metric changes against a baseline, let an evidence critic request logs and traces, inspect dependencies, and report supporting evidence, counterevidence, alternatives and uncertainty. Local execution is deterministic and is not an LLM demonstration.
4. Use the official Meterless H-MEM reference for verified earlier findings. Exclude the current incident and held-out investigations. Recheck historical suggestions against current telemetry. Nightshift remains the primary configured LLM provider; failures are visible.
5. Verify actual imported data, critique-driven queries, citation integrity, missing-data behavior, read-only boundaries, memory effects and browser interactions. Preserve receipts with exact execution mode.

Live Zetaris and Nightshift require independently verified connectivity; configuration alone is insufficient. No deployment, push, billing changes, credential creation or submission is part of this slice.
