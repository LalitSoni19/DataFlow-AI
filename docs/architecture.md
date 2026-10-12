# DataFlow AI architecture

DataFlow AI keeps the data-engineering foundation separate from the optional AI layer. Raw source bytes are immutable and hash-tracked. A full curated batch follows strict contracts; arbitrary sales exports follow a profiler-to-canonical-sales path that preserves source columns and publishes a reviewed semantic mapping.

```mermaid
flowchart LR
    A[Curated CSV / JSON sources] --> I[Strict contracts]
    B[CSV / TSV / XLSX / JSON sales export] --> P[Sales profiler + canonical mapper]
    C[HTTP JSON API] --> I
    I --> R[Immutable raw lake]
    P --> R
    R --> V[Contracts and quality rules]
    R --> P
    V -->|accepted| S[Curated staging tables]
    P --> F[Flexible canonical sales + source-shaped table]
    V -->|rejected| Q[Quarantine JSON Lines]
    S --> W[DuckDB curated star schema]
    F --> W
    W --> D[Streamlit dashboard]
    W --> F[FastAPI]
    W --> G[AST SQL guard + timeout]
    M[Live catalog + business metadata] --> G
    G --> AI[Typed sales AI]
```

The local runner is the executable reference pipeline. The optional Airflow DAG invokes that same runner on a schedule, avoiding two divergent ETL implementations. dbt models mirror the curated transformation logic for teams that prefer dbt build and test workflows.

