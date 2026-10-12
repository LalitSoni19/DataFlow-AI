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

- Set `MOTHERDUCK_TOKEN` and `DATAFLOW_DATABASE_URL=md:<database>` only through server-side secrets.
- MotherDuck loads run in an explicit transaction, so readers see the prior model or the completed new model rather than an intermediate delete/rebuild state.
- Keep `data/raw`, `data/processed`, `data/quarantine`, and `logs` on a persistent volume or object-storage-backed mount. MotherDuck hosts the warehouse but does not persist these application artifacts for you.
- Use one Airflow run at a time for a shared warehouse. Set `DATAFLOW_SOURCE_DIR` to the persisted directory that receives scheduled source snapshots.
- Monitor `/health`, `/api/pipeline/status`, LLM-provider error warnings, and the AI audit log. A slow query should terminate at `AI_QUERY_TIMEOUT_SECONDS`; an LLM call is bounded by `LLM_TIMEOUT_SECONDS` and `LLM_MAX_RETRIES`.

