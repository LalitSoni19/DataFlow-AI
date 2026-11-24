# Local runbook

1. Create and activate a Python 3.10+ virtual environment.
2. Install the project with `pip install -e .[dev]`.
3. Run `python scripts/run_pipeline.py --regenerate-sample` from the repository root, or run `python scripts/run_pipeline.py --source-dir <folder>` for a real sales export folder.
4. Start the API with `python scripts/run_api.py` and open `http://127.0.0.1:8000/docs`.
5. In a second terminal, start the dashboard with `streamlit run streamlit_app/app.py`.

## Operating checks

- Query `GET /health` to verify the API process.
- Query `GET /api/pipeline/status` to confirm the last pipeline run completed.
- Query `GET /api/data-quality` to inspect accepted and quarantined rows.
- Inspect `data/quarantine/<run_id>/` when a source contract fails.
- Inspect `data/processed/<run_id>/sales-import-profile.json` after a flexible import. It records detected column types, semantic mapping, and warnings without changing the original raw file.

## Recovery

The raw lake is append-only. Correct the source snapshot, rerun the pipeline, and compare manifests by run ID. The current active warehouse model is updated transactionally; raw source copies and prior mapping profiles remain available for audit.

## Cloud operation

- Set `MOTHERDUCK_TOKEN` and `DATAFLOW_DATABASE_URL=md:<database>` only through server-side secrets. Use the name of an existing writable MotherDuck database, such as `md:dataflow_ai`. A share URL or copied ATTACH command cannot be used as the writable warehouse.
- MotherDuck loads run in an explicit transaction, so readers see the prior model or the completed new model rather than an intermediate delete/rebuild state.
- Keep `data/raw`, `data/processed`, `data/quarantine`, and `logs` on a persistent volume or object-storage-backed mount. MotherDuck hosts the warehouse but does not persist these application artifacts for you.
- Use one Airflow run at a time for a shared warehouse. Set `DATAFLOW_SOURCE_DIR` to the persisted directory that receives scheduled source snapshots.
- Monitor `/health`, `/api/pipeline/status`, LLM-provider error warnings, and the AI audit log. A slow query should terminate at `AI_QUERY_TIMEOUT_SECONDS`; an LLM call is bounded by `LLM_TIMEOUT_SECONDS` and `LLM_MAX_RETRIES`.


## Customer workspaces

The public Streamlit demo uses a temporary directory per session. Reconnecting can start a fresh demo. For customer use, set DATAFLOW_AUTH_REQUIRED=true or DATAFLOW_ENV=production, mount persistent storage at DATAFLOW_DATA_DIR, and configure your identity provider. For multiple server processes, point them at the same volume with functioning file locks. Multi-host deployments need a shared database and a separately verified concurrency strategy.

- Configure Streamlit's native OIDC sign-in in its auth secrets (redirect_uri, cookie_secret, client_id, client_secret, server_metadata_url). Register the dashboard callback ending in /oauth2callback. Supply DATAFLOW_OIDC_ISSUER matching the ID token issuer.
- For FastAPI set DATAFLOW_OIDC_ISSUER, DATAFLOW_OIDC_AUDIENCE and DATAFLOW_OIDC_JWKS_URL (HTTPS). Supply the required deployment settings in docker-compose.yml. Clients send RS256 access tokens for that audience. Use the same issuer and stable subject across both clients for access to the same workspace. The optional provider-issued dataflow_role claim controls analyst, admin and dpo permissions.
- In managed mode, provisioning, password recovery, email delivery and account removal belong to the configured identity provider. The local demonstration login/reset routes are disabled. Verify sign-in, recovery delivery and sign-out against your provider before onboarding customers.
- With MOTHERDUCK_TOKEN and DATAFLOW_DATABASE_URL=md:<base>, authenticated API and dashboard sessions use the same private MotherDuck database per issuer/subject. The service token needs database creation and write permission. Public demo sessions get distinct cloud databases; operators should remove retired demo databases under their retention policy. Direct pipeline jobs use the configured base database. Import history, source snapshots and processing artifacts for new cloud runs are stored in MotherDuck. Python parsing and Arrow upload use temporary host resources. Offline development still supports local DuckDB.
- To initialize an existing customer's workspace from trusted original files, use Settings.from_environment().workspace(issuer, subject) with PipelineRunner. Existing single-warehouse jobs keep their own database. A prior import that lost leading zeros or fractional amounts must be rebuilt from the original source.

## Reviewing and restoring imports

Upload files or fetch a JSON snapshot, inspect its mappings and coverage, then publish. Use one invoice total per invoice, or a distinct line amount per item. Currency is never converted. Select the reporting currency in the dashboard or pass the currency query parameter (e.g. USD) to analytics and AI endpoints. The default is the alphabetically first currency code in the dataset; UNKNOWN groups rows without a currency. The overview also returns all currency subtotals and coverage. Missing status columns assume completed sales; explicit statuses must be completed, paid or fulfilled to contribute.

Import history republishes the immutable source files for a selected completed run as a new run after verifying their hashes. Back up the warehouse and raw/processed directories together. A restore replays the current importer and retains the history of earlier runs.
