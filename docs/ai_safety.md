# AI analytics safety design

The AI assistant is a convenience layer on top of trusted data, not an independent source of business facts. It can also provide general sales guidance, but that mode is clearly marked as non-data-grounded.

## Typed routing and execution

1. **Typed request routing**: The provider returns JSON with exactly one intent: `warehouse_query`, `sales_guidance`, `clarify`, or `out_of_scope`. Malformed output is rejected before it can become SQL.
2. **Live-schema Text-to-SQL**: A `warehouse_query` receives only the active curated or flexible-import catalog. The catalog is read from `information_schema`, so an uploaded sales file never relies on stale Markdown schema documentation.
3. **Optional synthesis**: Query results use a deterministic explanation by default. `AI_SYNTHESIZE_INSIGHTS=true` enables a bounded second model call over masked, sampled results when richer narrative is worth the extra latency.

## Safety guardrails

- Metadata retrieval supplies trusted business definitions alongside the live schema.
- Built-in question patterns work offline and execute only approved parameterized read queries.
- AST validation permits exactly one `SELECT` or read-only CTE statement against the active analytics-table allow-list.
- SQL comments, multiple statements, DDL, DML, imports, exports, extensions, external scans, table-valued functions, implicit Cartesian joins, and non-approved functions/tables are rejected.
- The query connection is opened read-only, result sets are capped at 200 rows, and a DuckDB interruption timer enforces `AI_QUERY_TIMEOUT_SECONDS`.
- Each question, SQL trace, execution state, provider, and result count is recorded in `governance.ai_audit_log`.
- PII (emails, names, phone numbers, addresses, and common flexible-import PII fields) is masked in query results and redacted from logged questions.
- A rejected model query gets at most one constrained repair attempt using the safe error and the live catalog. There is no unbounded agent loop.

## Offline fallback

If the LLM is unavailable (network error, rate limit, or not configured), the assistant falls back to deterministic keyword-matching rules that handle KPI overview, top products, revenue by region, and month-over-month change questions. These rules execute hardcoded parameterized SQL and produce data-grounded explanations. General sales guidance requires a configured provider rather than being fabricated by offline rules.

## Provider configuration

The assistant uses Google's OpenAI-compatible endpoint when a Gemini key is configured. Any OpenAI-compatible provider (Groq, OpenRouter, self-hosted vLLM) or local Ollama can be substituted via environment variables. Providers are disabled by default, so a copied configuration does not repeatedly attempt a nonexistent localhost service. The API key is stored in `.env` and never logged, rendered in the UI, or committed to version control.

The API already supplies authentication hooks, rate limits, and production configuration validation. A production deployment must still provide trusted identity integration, least-privilege MotherDuck roles, platform secrets management, durable artifact storage, PII retention policy, network controls, and monitoring.
