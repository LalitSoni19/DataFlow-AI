# DataFlow AI

> A lightweight, local-first data engineering platform and conversational analytics warehouse.

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://lalitsoni-dataflow.streamlit.app/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![DuckDB](https://img.shields.io/badge/duckdb-warehouse-yellow.svg)](https://duckdb.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-backend-009688.svg)](https://fastapi.tiangolo.com/)

---

## Overview

DataFlow AI is an end-to-end data platform built with **Python**, **DuckDB**, **FastAPI**, and **Streamlit**. 

Setting up modern data stacks often requires stitching together heavy cloud services (Snowflake, Databricks, orchestrators) that are expensive and complex to run locally. DataFlow AI packages the entire data lifecycle into a single lightweight repository:

1. **Ingest & Validate**: Reads messy incoming files (CSV, TSV, Excel, JSON) or REST APIs, validates data contracts, and routes invalid records into quarantine.
2. **Model & Store**: Transforms raw records into a dimensional star schema inside an embedded DuckDB warehouse (or MotherDuck in the cloud).
3. **Serve & Visualize**: Exposes metrics through high-performance FastAPI endpoints and an interactive Streamlit dashboard.
4. **Query with AI**: Allows stakeholders to ask questions in plain English, safely converting questions to sandboxed, read-only SQL with PII masking and offline fallback.

---

## Architecture

```text
       CSV / TSV / Excel / JSON / REST API
                       │
                       ▼
         ┌───────────────────────────┐
         │       Raw Data Lake       │  (Immutable append-only lake with SHA-256 hashes)
         └─────────────┬─────────────┘
                       │
                       ▼
         ┌───────────────────────────┐
         │ Contract & Schema Quality │───► [ Quarantine / Errors ]
         └─────────────┬─────────────┘
                       │ Valid rows
                       ▼
         ┌───────────────────────────┐
         │ DuckDB Analytics Database │  (Staging layer, star schema, monthly marts)
         │  (Local file or MotherDuck)│
         └─────────────┬─────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
      [ FastAPI Backend ]  [ Streamlit UI ]
              ▲
              │
      [ AI Sales Assistant ] (Sandboxed Text-to-SQL + PII Redaction)
```

---

## Quickstart

### 1. Setup Environment

```bash
# Clone the repository
git clone https://github.com/LalitSoni19/DataFlow-AI.git
cd DataFlow-AI

# Create and activate a virtual environment
python -m venv .venv

# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install dependencies in editable mode
pip install -e .[dev]
```

### 2. Run the Pipeline

Generate sample data, run validation checks, quarantine bad records, and build the analytical warehouse:

```bash
python scripts/run_pipeline.py --regenerate-sample
```

The database is built at `data/warehouse/dataflow.duckdb`.

> **Custom Data**: To import your own folder of sales files:
> ```bash
> python scripts/run_pipeline.py --source-dir "/path/to/your/sales/folder"
> ```

### 3. Start the Apps

Open two terminals to launch both the backend and frontend:

**Terminal 1 — FastAPI Server:**
```bash
python scripts/run_api.py
```
*API docs (Swagger UI): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)*

**Terminal 2 — Streamlit Dashboard:**
```bash
streamlit run streamlit_app/app.py
```
*Dashboard: [http://localhost:8501](http://localhost:8501)*

---

## Core Capabilities

### 1. Flexible Ingestion & Automatic Profiling
- Ingests CSV, TSV, XLSX, JSON, and JSON Lines exports.
- Smart column detection maps common business headers (`invoice date`, `unit price`, `qty`, `customer`, `region`, etc.) to canonical warehouse columns.
- Non-sales files are safely rejected with helpful diagnostic messages.
- Files are saved with SHA-256 checksums and per-run manifest logs.

### 2. Data Quality & Quarantining
- Pre-load schema validation, null checks, format validations, and referential integrity checks.
- Rows failing validation are written to isolated JSON Lines quarantine files with error reasons—preventing pipeline crashes and preserving data integrity.

### 3. Serverless Analytical Warehouse (DuckDB)
- Runs completely in-process using DuckDB. No separate database server required.
- Organized into clean layers: staging tables, dimensional tables (`dim_customers`, `dim_products`, `dim_date`, `fact_orders`), and aggregate reporting marts.
- Supports **MotherDuck** cloud execution by simply setting `MOTHERDUCK_TOKEN` in your environment.

### 4. Sandboxed AI Query Assistant
- Ask questions in conversational English (e.g., *"What was our total revenue in Q3?"*).
- **Safety First**:
  - Live schema awareness: queries are generated only against approved warehouse tables.
  - Read-only execution: blocks DDL/DML, external scans, Cartesian joins, and SQL comments.
  - Hard query timeout and row limit caps (max 200 rows).
  - Customer PII (names, emails) is automatically redacted in prompts and responses.
  - Multi-model fallback chain: if the primary model is busy (e.g. HTTP 503), it automatically tries fallback models.
  - 100% offline fallback: deterministic keyword-based rule engine works even without an internet connection.

---

## Configuration

Copy `.env.example` to `.env` to configure optional credentials:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `DATAFLOW_DATA_DIR` | `data` | Base directory for data lake & local storage |
| `DATAFLOW_DATABASE_PATH` | `data/warehouse/dataflow.duckdb` | Path to local DuckDB file |
| `MOTHERDUCK_TOKEN` | - | (Optional) MotherDuck token for cloud storage & compute |
| `DATAFLOW_DATABASE_URL` | - | MotherDuck database URI (e.g. `md:dataflow_ai`) |
| `LLM_BASE_URL` | - | OpenAI-compatible endpoint (e.g. Google Gemini endpoint) |
| `LLM_API_KEY` | - | API key for external LLM provider |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | Primary LLM model |
| `LLM_FALLBACK_MODELS` | `gemini-3.5-flash-lite,gemini-3.8-flash` | Comma-separated fallback models on transient errors |
| `LLM_TIMEOUT_SECONDS` | `20` | Request timeout per LLM call |
| `LLM_MAX_RETRIES` | `2` | Number of retry attempts on rate limit / 503 spikes |
| `AI_QUERY_TIMEOUT_SECONDS`| `8` | Warehouse execution timeout for AI-generated queries |

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health status |
| `GET` | `/api/kpis/overview` | Core KPIs (revenue, orders, active customers, average order value) |
| `GET` | `/api/sales` | Daily aggregated sales trends |
| `GET` | `/api/products/top` | Top-performing products ranked by revenue |
| `GET` | `/api/pipeline/status` | Last pipeline run execution summary and row counts |
| `GET` | `/api/data-quality` | Quality check results and quarantine statistics |
| `GET` | `/api/metadata/tables` | Schema definitions for analytics tables |
| `GET` | `/api/exports/sales.csv` | Export summarized sales dataset to CSV |
| `POST` | `/api/ask` | Natural language Text-to-SQL endpoint |
| `POST` | `/api/privacy/customers/{id}/delete` | GDPR customer deletion & anonymization |

---

## Optional Integrations

### dbt Transformations
Run dbt models against your DuckDB warehouse:
```bash
cp dbt/profiles.yml.example ~/.dbt/profiles.yml
dbt build --project-dir dbt
```

### Apache Airflow DAG
An Airflow TaskFlow DAG is included in `airflow/dags/sales_pipeline.py`. To run:
1. Install orchestration dependencies: `pip install -e .[orchestration]`
2. Set `DATAFLOW_PROJECT_ROOT` to this project directory.
3. Link `airflow/dags/sales_pipeline.py` into your Airflow DAGs directory.

### Docker Deployment
Run the platform as a containerized stack:
```bash
docker compose --env-file .env up --build
```

---

## Repository Structure

```text
├── airflow/           # Airflow TaskFlow pipeline DAGs
├── config/            # Schema definitions & contract rules
├── data/              # Raw data lake, processed partitions, quarantine & DuckDB warehouse
├── dbt/               # dbt transformation models and tests
├── docs/              # System architecture, data dictionary, and runbooks
├── scripts/           # Execution scripts for pipeline, API, and dashboard
├── src/dataflow_ai/   # Main package
│   ├── ai/            # Safe Text-to-SQL engine, prompts, and LLM clients
│   ├── api/           # FastAPI routers, auth, rate-limiting, and schemas
│   ├── ingestion/     # File loaders & flexible sales profiler
│   ├── warehouse/     # DuckDB repository, schemas, and queries
│   ├── pipeline.py    # Pipeline runner & quality validation
│   ├── sample_data.py # Synthetic demo data generator
│   ├── security.py    # PII masking & authentication
│   └── settings.py    # App configuration & environment loader
├── streamlit_app/     # Multi-page dashboard
└── tests/             # Pytest test suite (130+ tests)
```

---

## Testing

Run the test suite with pytest:

```bash
pytest
```

---

## License

This project is licensed under the Apache 2.0 License.
