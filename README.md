# DataFlow AI

A lightweight, end-to-end data engineering and analytics platform built with **Python**, **DuckDB**, **FastAPI**, and **Streamlit**. 

DataFlow AI handles multi-source data ingestion, schema contract validation with automatic quarantining, star-schema modeling in an embedded DuckDB warehouse, REST API endpoints, an interactive analytics dashboard, and a sandboxed Text-to-SQL query assistant.

---

## Key Features

- **Multi-source Ingestion**: Ingest curated source batches or arbitrary sales exports (CSV, TSV, XLSX, JSON, JSON Lines) into an append-only raw data lake with SHA-256 checksums and per-run manifests.
- **Flexible Sales Import**: Profile unknown sales-file columns, detect common business aliases (invoice date, amount, SKU, quantity, customer, region, and more), retain the original layout, and normalize a canonical sales fact for analytics. Non-sales files are rejected with an actionable message.
- **Data Quality & Quarantine**: Schema contracts, null/type checks, and referential integrity rules. Malformed or invalid rows are safely isolated into JSON Lines quarantine files.
- **Embedded DuckDB Warehouse**: Zero-server local data warehouse featuring a staging layer, dimensional star schema (`dim_customers`, `dim_products`, `dim_date`, `fact_orders`), and monthly sales marts.
- **FastAPI Backend**: High-performance REST API for KPI metrics, data exports, table metadata, quality reports, and AI query answering.
- **Streamlit Dashboard**: Multi-page UI for data ingestion, pipeline health monitoring, warehouse exploration, and conversational analytics.
- **AI-Powered Sales Assistant**: Ask data-grounded questions about the active sales dataset or request general sales guidance. The assistant uses a typed intent contract:
  - `warehouse_query` produces SQL only from a live catalog of the currently active schema.
  - `sales_guidance` provides sales-only advice and is clearly marked as not data-grounded.
  - `clarify` and `out_of_scope` avoid invented answers.
  - AST-based SQL validation blocks DDL/DML, external scans, table functions, Cartesian joins, unapproved relations, comments, and stacked queries.
  - Enforced read-only execution, query interruption timeout, row caps, PII masking, audit logging, and one bounded SQL-repair attempt.
  - Automatic PII redaction and query audit logging.
  - Works 100% offline as fallback (deterministic parser) or connects to Gemini / Ollama / OpenAI-compatible models.
- **Modular Orchestration**: Includes optional dbt project models and an Apache Airflow 3 TaskFlow DAG.

---

## Architecture

```text
  CSV / TSV / XLSX / JSON / REST API
           │
           ▼
┌─────────────────────────────────────────┐
│   Raw Data Lake (Immutable + Hashes)    │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│ Curated contracts OR sales-file profiler │───► [ Quarantine / mapping warnings ]
└────────────────────┬────────────────────┘
                     │ Valid records
                     ▼
┌─────────────────────────────────────────┐
│       DuckDB Analytics Warehouse        │
│  - Staging Layer                        │
│  - Star Schema (Facts & Dimensions)     │
│  - Aggregated Sales Marts               │
└────────────────────┬────────────────────┘
                     │
           ┌─────────┴─────────┐
           ▼                   ▼
    [ FastAPI Backend ]  [ Streamlit UI ]
           ▲
           │
    [ AI Text-to-SQL ] (Read-only + Sandboxed)
```

---

## Quick Start

### Prerequisites
- Python 3.10 or higher

### 1. Set Up Environment

```bash
# Clone the repository and navigate into the directory
cd dataflow-ai

# Create and activate virtual environment
python -m venv .venv

# On Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# On macOS/Linux:
source .venv/bin/activate

# Install package and dependencies
pip install -e .[dev]
```

### 2. Run the Data Pipeline

Run the pipeline to generate sample data, validate records, quarantine bad rows, and build the DuckDB analytical warehouse:

```bash
python scripts/run_pipeline.py --regenerate-sample
```

The database will be written to `data/warehouse/dataflow.duckdb`.

To import a real sales export with any supported filename or column layout:

```bash
python scripts/run_pipeline.py --source-dir "C:\path\to\sales-export-folder"
```

Use the **Data ingestion** dashboard page to see the detected mapping and any quality warnings after upload. A full five-file curated batch still uses its stricter source contract; a folder without that batch automatically uses the flexible sales importer.

### 3. Launch the Web Applications

Start the FastAPI backend and Streamlit dashboard in separate terminals:

**Terminal 1 — FastAPI Server:**
```bash
python scripts/run_api.py
```
> API Docs available at: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

**Terminal 2 — Streamlit Dashboard:**
```bash
streamlit run streamlit_app/app.py
```
> Dashboard available at: [http://localhost:8501](http://localhost:8501)

---

## Configuration

Copy `.env.example` to `.env` to customize settings:

```bash
cp .env.example .env
```

Key environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` | - | (Optional) MotherDuck service token for 100% cloud warehouse storage & compute |
| `DATAFLOW_DATABASE_URL` | - | Database connection URI (e.g. `md:dataflow_ai` for MotherDuck cloud) |
| `DATAFLOW_DATA_DIR` | `data` | Base directory for data lake & local storage |
| `DATAFLOW_DATABASE_PATH` | `data/warehouse/dataflow.duckdb` | Path to local DuckDB database file (fallback) |
| `DATAFLOW_LOG_LEVEL` | `INFO` | Application log level |
| `OLLAMA_BASE_URL` | - | (Optional) Ollama endpoint; configure only when it is running |
| `OLLAMA_MODEL` | - | (Optional) Ollama model name |
| `LLM_BASE_URL` | - | OpenAI-compatible base URL (e.g. Gemini: `https://generativelanguage.googleapis.com/v1beta/openai/`) |
| `LLM_API_KEY` | - | API key (free Gemini key from [AI Studio](https://aistudio.google.com/app/apikey)) |
| `LLM_MODEL` | provider default only when a Gemini key is supplied | LLM model name; explicit values are never silently rewritten |
| `LLM_TIMEOUT_SECONDS` | `20` | Per-provider request timeout |
| `LLM_MAX_RETRIES` | `1` | Bounded retry count for transient provider failures |
| `AI_QUERY_TIMEOUT_SECONDS` | `8` | Read-only warehouse query interruption timeout |
| `AI_SYNTHESIZE_INSIGHTS` | `false` | Opt in to a second narrative model call after query execution |

---

## API Reference

The FastAPI service exposes the following main endpoints:

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Service health status |
| `GET` | `/api/kpis/overview` | High-level business KPIs (revenue, orders, customers) |
| `GET` | `/api/sales` | Daily sales aggregated by date and region |
| `GET` | `/api/products/top` | Top-selling products by revenue |
| `GET` | `/api/pipeline/status` | Last pipeline run execution summary |
| `GET` | `/api/data-quality` | Data quality checks and quarantine stats |
| `GET` | `/api/metadata/tables` | Schema metadata for approved analytical tables |
| `GET` | `/api/exports/sales.csv` | Export sales summary as CSV |
| `GET` | `/api/reports/monthly` | Summary text report of monthly performance |
| `POST` | `/api/ask` | Natural-language query interface |
| `POST` | `/api/privacy/customers/{id}/delete` | GDPR customer deletion & anonymization |

---

## AI Guardrails & Security

The natural-language query interface is designed to prevent prompt injection and data leaks:

1. **Live Table Whitelisting**: Only tables in the currently active curated or flexible analytics model can be queried.
2. **Read-Only Execution**: The database connection used by the executor is strictly read-only.
3. **AST Validation**: Statements with `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `ATTACH`, `COPY`, external scans, table functions, Cartesian joins, multi-statements, or SQL comments are rejected before execution.
4. **Work Bounds**: Result sets are capped (max 200 rows) and a real DuckDB interruption timer limits query work.
5. **PII Masking**: Customer names, emails, and sensitive identifiers are masked in query responses and audit logs.

---

## Optional Integrations

### dbt Modeling
To run transformations using dbt instead of the Python warehouse builder:
```bash
cp dbt/profiles.yml.example ~/.dbt/profiles.yml   # Configure your dbt profile
dbt build --project-dir dbt
```

### Apache Airflow Orchestration
An Airflow TaskFlow DAG is provided under `airflow/dags/sales_pipeline.py`. To use:
1. Install orchestration extras: `pip install -e .[orchestration]`
2. Set `DATAFLOW_PROJECT_ROOT` to the repository directory.
3. Symlink or copy `airflow/dags/sales_pipeline.py` into your Airflow DAGs directory.

Set `DATAFLOW_SOURCE_DIR` in the Airflow worker to a mounted/persisted sales-source directory. The DAG accepts either the curated batch or flexible sales exports, limits concurrent runs to one, and retries transient failures.

## Production deployment

The repository includes a non-root [Dockerfile](Dockerfile), [Docker Compose configuration](docker-compose.yml), and CI workflow. For a local container deployment:

```bash
docker compose --env-file .env up --build
```

Use a real `DATAFLOW_JWT_SECRET`, explicit HTTPS `DATAFLOW_CORS_ORIGINS`, and persistent volumes or object storage for raw/processed/quarantine artifacts. MotherDuck moves warehouse storage/compute to the cloud, but application audit artifacts still require durable storage.

### Security & Secret Rotation Policy

> [!WARNING]
> **Rotate Exposed Credentials Immediately:** If any API keys, database URLs, webhook secrets, or service tokens were ever previously committed or hardcoded in version control or test files, they must be considered compromised. Rotate and revoke any previously hardcoded or exposed secrets immediately prior to production launch.

Production deployments must inject credentials via environment variables or cloud secret managers. Never commit `.env` files containing real secrets to version control.

---

## Project Structure

```text
├── airflow/               # Airflow TaskFlow DAG definitions
├── config/                # Pipeline schemas and contract definitions
├── data/                  # Generated raw lake, quarantine, and DuckDB warehouse
├── dbt/                   # Optional dbt models, tests, and documentation
├── docs/                  # Architecture notes, data dictionary, runbooks
├── scripts/               # CLI runners for data generation, pipeline, and API
├── src/dataflow_ai/       # Core package
│   ├── api/               # FastAPI application & routes
│   ├── ingestion/         # Source adapters (CSV, JSON, HTTP API)
│   ├── quality/           # Data contracts, validation & quarantine rules
│   ├── warehouse/         # DuckDB staging & star-schema builder
│   ├── ai/                # Text-to-SQL engine, guardrails & LLM adapters
│   ├── security/          # PII masking & hashing utilities
│   └── utils/             # Logging and helper utilities
├── streamlit_app/         # Streamlit multi-page dashboard
└── tests/                 # Unit and integration test suite
```

---

## Running Tests

Execute the test suite with pytest:

```bash
pytest
```

---

## Documentation

- [Architecture Design](docs/architecture.md)
- [Data Dictionary](docs/data_dictionary.md)
- [Operational Runbook](docs/runbook.md)
- [AI Safety & Guardrails](docs/ai_safety.md)

