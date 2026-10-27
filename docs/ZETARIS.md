# Zetaris data path

**The complete live data path passed on October 25, 2026.** Four anonymous Parquet files were uploaded through the authenticated NDP File System; their displayed sizes matched the prepared files, native preview succeeded, and all four registered table counts were independently verified. Two actual investigations then used the real Zetaris adapter for every query. The live verifier retained 45 remote SELECTs including preflight, with returned-row hashes and resolving evidence citations. It reports all 11 acceptance layers passed in `artifacts/private/live-flow-20261025-nvidia-01/receipt.json`.

The initial private-S3 registration failed because the engine could not obtain AWS credentials from its default provider chain. Read-only discovery confirmed that no table was created by that attempt. The working onboarding route uses the existing Zetaris login and NDP File System; no AWS session credentials, new access keys, IAM grants or public objects are needed. The four prepared files contain **44,627 genuine sampled RCAEval rows / 685,826 bytes**. Sampling is disclosed in [DATA.md](DATA.md) and the public sample manifest.

The app uses the **SQL UI proxy** contract from the [official connection helper](https://github.com/zetaris/getting-started/blob/main/scripts/zetaris_api.py) and [query helper](https://github.com/zetaris/getting-started/blob/main/scripts/query_zetaris.py). `ZETARIS_BASE_URL` is the SQL UI origin. Direct REST and JDBC use separate connection routes. The response decoder supports the documented `headers`/`data` shape, converts numeric strings explicitly, preserves identifier strings and NULLs, and rejects malformed/error envelopes.

## Existing-instance workflow

1. Keep existing connection settings in ignored `.env.local`: `ZETARIS_BASE_URL`, numeric `ZETARIS_ORG_ID`, and an existing token or account username/password. Secrets are never part of registration SQL, screenshots or public receipts.
2. Prepare typed anonymous Parquet with `.venv/bin/python -m scripts.prepare_zetaris_import --source sample --engine-path dffs://YOUR_CONFIRMED_FOLDER --database incidentcourt_live_UNIQUE_NAME`. This writes four local files and a canonical plan under ignored `data/staging/zetaris/`; it performs no upload or registration. Explicit Arrow schemas preserve all-NULL severity as a nullable string, rather than inferring an unsupported null-only type.
3. In NDP File System, open `file-store`, create the intended project folder, upload the four prepared files and verify their sizes. Use the native File Store picker to obtain the actual engine URI. The contract observed for that instance was `dffs://folder/table.parquet`, with the folder in the URI authority. Do not substitute an assumed physical EFS path or rewrite it as a triple-slash URI. [Official upload guide](https://kbase.zetaris.com/knowledge/file-system-overview), [registration walkthrough](https://kbase.zetaris.com/knowledge/how-to-ccnnect-to-json-from-local-machine).
4. Reconcile the existing namespace before further registration:

   ```sh
   .venv/bin/python -m scripts.resume_zetaris_registration --path-column source_table
   ```

   This issues fresh `SELECT 1`, `SHOW LIGHTNING TABLES <namespace>` and count checks. The live listing supplies `name`, `source_table` and `format`. Existing tables must match the canonical path, Parquet format and prepared count. Unknown metadata or a mismatch stops the workflow.
5. Register one confirmed-missing table at a time using the same helper and `--execute-one metrics`, `--execute-one logs` or `--execute-one traces`. It never recreates the database or an existing table, drops/replaces objects, or retries an uncertain mutation. Each operation retains a private receipt and verifies fresh metadata/counts. All four application tables existed and were verified during the October 25 run; check current state before operating. This helper is only for a later table proved missing after fresh reconciliation; **do not rerun the original five-statement batch**. After a timeout, reconcile read-only before deciding whether another operation is needed.
6. Set `ZETARIS_TABLE_PREFIX` to the verified namespace plus a trailing dot, `SAMPLE_MODE=false` and `INCIDENTCOURT_DATA_MODE=zetaris`, then restart the application. Run `.venv/bin/python -m scripts.check_integrations` and an investigation. Every query receipt must identify `backend: zetaris`; a complete investigation is a separate acceptance layer from file upload, table creation or `SELECT 1`.

The data stored in Zetaris is a sampled subset of real controlled-experiment telemetry. The live backend setting remains `zetaris`; the offline `SAMPLE_MODE=true` path is available independently.

## Required tables

| Table | Columns | Verified remote rows |
| --- | --- | ---: |
| incidents | id, title, source, start_ts, end_ts, split | 3 |
| metrics | id, incident_id, ts, service, name, value | 28,126 |
| logs | id, incident_id, ts, service, severity, message | 1,498 |
| traces | id, incident_id, ts, service, span_id, parent_id, trace_id, duration_ms, status | 15,000 |

Timestamps are epoch seconds, durations milliseconds, and IDs strings. Missing values remain NULL. Answer keys, injection metadata and original answer-revealing case paths are excluded from these tables. Source provenance and any raw HTTP diagnostics stay in ignored private artifacts. SQL errors are redacted and redirects cannot forward authentication headers.

The passed live run used history `IC-001` (`3c24d86ae0a5`, 14 queries, 21 evidence records) and holdout `IC-003` (`99f570d82daa`, 16 queries, 33 evidence records). The target retrieved one H-MEM historical match and issued two additional current-incident queries, `Q03` and `Q09`; holdout findings were not added to history. Six distinct uncached Nightshift responses reported the exact approved NVIDIA Lightning model, with zero runtime-reported actual cost and 890 output tokens in total.

This is real CLI integration and provenance evidence on the disclosed sampled telemetry. It is not root-cause accuracy, production/deployment, browser-flow or account-invoice proof. Browser import, citation-row expansion, query-trail inspection and matching JSON export of the actual saved live record passed; browser-triggered generation was not repeated. The six-call hosted allowance is exhausted and persistent inference is disabled; checking saved receipts or performing read-only SQL does not require another model generation.
