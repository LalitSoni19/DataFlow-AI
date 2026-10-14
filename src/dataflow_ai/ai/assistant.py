"""Natural-language analytics assistant and query planner."""

from __future__ import annotations

import calendar
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from dataflow_ai.ai.llm_client import LLMClient, LLMError, OllamaClient
from dataflow_ai.ai.prompts import (
    INSIGHT_SYSTEM_PROMPT,
    build_insight_system_prompt,
    build_sql_repair_prompt,
    build_sql_router_system_prompt,
)
from dataflow_ai.ai.retrieval import MetadataRetriever, RetrievedDocument
from dataflow_ai.ai.sql_guard import SafeSQLExecutor, SQLSafetyError
from dataflow_ai.settings import Settings
from dataflow_ai.utils import mask_record_pii, redact_pii_text, sanitize_secret_string
from dataflow_ai.warehouse.repository import WarehouseRepository

logger = logging.getLogger(__name__)

# Sentinel value returned by the LLM when a question is outside the data scope
_OFF_TOPIC_MARKER = "OFF_TOPIC"
_OFF_TOPIC_ANSWER = (
    "I can only answer questions about your e-commerce analytics data — "
    "such as sales, revenue, customers, products, orders, regions, and trends. "
    "Please ask a question related to your warehouse data."
)


class _OffTopicError(Exception):
    """Raised when the LLM determines a question is outside the project data scope."""


@dataclass(frozen=True)
class AssistantResponse:
    answer: str
    sql: str | None
    parameters: list[Any]
    rows: list[dict[str, Any]]
    source_documents: list[str]
    provider: str
    request_id: str
    warning: str | None = None
    mode: str = "warehouse_query"
    data_grounded: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueryPlan:
    kind: str
    sql: str
    parameters: list[Any]


@dataclass(frozen=True)
class ModelDecision:
    """Parsed decision returned by the router LLM."""

    intent: str
    sql: str | None = None
    answer: str | None = None


class AnalyticsAssistant:
    """Translates natural language questions into safe SQL and executes them."""

    def __init__(self, settings: Settings, repository: WarehouseRepository | None = None) -> None:
        self.settings = settings
        self.repository = repository or WarehouseRepository(settings)
        self.retriever = MetadataRetriever.from_project_docs(settings.project_root / "docs")
        self.executor = SafeSQLExecutor(self.repository)
        self.llm = self._configured_llm()

    def ask(self, question: str) -> AssistantResponse:
        normalized = question.strip()
        if not normalized:
            raise ValueError("A question is required.")

        sources = self.retriever.search(normalized)
        logged_question = redact_pii_text(normalized)
        warning: str | None = None

        # First ask the provider for a typed intent, rather than assuming every
        # sales question must be SQL.  This unlocks useful general sales advice
        # while keeping unrelated prompts out of both the model and warehouse.
        if self.llm is not None:
            provider = type(self.llm).__name__
            try:
                decision = self._decision_from_llm(normalized, sources)
                if decision.intent == "warehouse_query" and decision.sql:
                    return self._execute_and_respond(
                        QueryPlan("llm", decision.sql, []), normalized, sources, provider, logged_question
                    )
                if decision.intent == "sales_guidance":
                    return self._respond_without_query(
                        answer=decision.answer or "I can help with a sales strategy question. Could you share your goal, audience, and sales channel?",
                        status="guidance_completed",
                        provider=provider,
                        logged_question=logged_question,
                        sources=sources,
                        mode="sales_guidance",
                        data_grounded=False,
                    )
                if decision.intent == "clarify":
                    return self._respond_without_query(
                        answer=decision.answer or "What sales metric, timeframe, or segment should I focus on?",
                        status="clarification_requested",
                        provider=provider,
                        logged_question=logged_question,
                        sources=sources,
                        mode="clarify",
                        data_grounded=False,
                    )
                return self._respond_without_query(
                    answer=_OFF_TOPIC_ANSWER,
                    status="off_topic",
                    provider=provider,
                    logged_question=logged_question,
                    sources=sources,
                    mode="out_of_scope",
                    data_grounded=False,
                )
            except LLMError as error:
                clean_err = sanitize_secret_string(str(error))
                warning = f"LLM unavailable, falling back to built-in rules ({clean_err})."
                logger.warning("LLM routing failed, falling back to local rules: %s", clean_err)
            except Exception as error:  # noqa: BLE001
                clean_err = sanitize_secret_string(str(error))
                warning = f"LLM query failed, falling back to built-in rules ({clean_err})."
                logger.warning("LLM routing failed, falling back to local rules: %s", clean_err)

        provider = "local_rules"
        plan = self._plan_local_query(normalized)

        if plan is None:
            if not self._is_sales_domain_question(normalized):
                return self._respond_without_query(
                    answer=_OFF_TOPIC_ANSWER,
                    status="off_topic",
                    provider=provider,
                    logged_question=logged_question,
                    sources=sources,
                    mode="out_of_scope",
                    data_grounded=False,
                    warning=warning,
                )
            return self._respond_without_query(
                answer=(
                    "I need the configured sales AI provider to answer that open-ended sales question. "
                    "I can still answer KPI overview, top products, revenue by region, and dated month-over-month changes offline."
                ),
                status="not_supported",
                provider=provider,
                logged_question=logged_question,
                sources=sources,
                mode="clarify",
                data_grounded=False,
                warning=warning,
            )

        return self._execute_and_respond(plan, normalized, sources, provider, logged_question, warning)

    def _respond_without_query(
        self,
        *,
        answer: str,
        status: str,
        provider: str,
        logged_question: str,
        sources: list[RetrievedDocument],
        mode: str,
        data_grounded: bool,
        warning: str | None = None,
    ) -> AssistantResponse:
        request_id = self.repository.audit_ai_query(
            question=logged_question,
            generated_sql=None,
            execution_status=status,
            provider=provider,
        )
        return AssistantResponse(
            answer=answer,
            sql=None,
            parameters=[],
            rows=[],
            source_documents=[item.source for item in sources],
            provider=provider,
            request_id=request_id,
            warning=warning,
            mode=mode,
            data_grounded=data_grounded,
        )

    def _execute_and_respond(
        self,
        plan: QueryPlan,
        question: str,
        sources: list[RetrievedDocument],
        provider: str,
        logged_question: str,
        warning: str | None = None,
        *,
        allow_repair: bool = True,
    ) -> AssistantResponse:
        """Execute a query plan and build a response with optional LLM insight synthesis."""

        try:
            result = self.executor.execute(plan.sql, plan.parameters)

            # Try LLM-powered insight synthesis, fall back to rule-based explanation
            answer = self._generate_insight(question, plan, result.rows)

            request_id = self.repository.audit_ai_query(
                question=logged_question,
                generated_sql=result.sql,
                execution_status="completed",
                provider=provider,
                row_count=result.row_count,
            )
            return AssistantResponse(
                answer=answer,
                sql=result.sql,
                parameters=result.parameters,
                rows=result.rows,
                source_documents=[item.source for item in sources],
                provider=provider,
                request_id=request_id,
                warning=warning,
            )
        except Exception as error:  # noqa: BLE001
            execution_status = "rejected" if isinstance(error, SQLSafetyError) else "failed"
            clean_error = sanitize_secret_string(str(error))
            # One constrained repair attempt makes an ordinary model mistake
            # recoverable without turning the endpoint into an unbounded agent.
            # It is intentionally available only for model-generated SQL, not
            # parameterized local plans.
            if allow_repair and plan.kind == "llm" and self.llm is not None:
                repaired_sql = self._repair_sql(question, plan.sql, clean_error)
                if repaired_sql:
                    repaired_warning = _append_warning(
                        warning,
                        "The initial AI query was rejected and a single safe repair was attempted.",
                    )
                    return self._execute_and_respond(
                        QueryPlan("llm_repaired", repaired_sql, []),
                        question,
                        sources,
                        provider,
                        logged_question,
                        repaired_warning,
                        allow_repair=False,
                    )
            request_id = self.repository.audit_ai_query(
                question=logged_question,
                generated_sql=plan.sql,
                execution_status=execution_status,
                provider=provider,
                error_message=clean_error[:2_000],
            )
            return AssistantResponse(
                answer="I could not safely execute that analytical query.",
                sql=plan.sql,
                parameters=plan.parameters,
                rows=[],
                source_documents=[item.source for item in sources],
                provider=provider,
                request_id=request_id,
                warning=clean_error,
            )

    def _configured_llm(self) -> LLMClient | OllamaClient | None:
        if self.settings.llm_base_url and self.settings.llm_model:
            return LLMClient(
                self.settings.llm_base_url,
                self.settings.llm_model,
                self.settings.llm_api_key,
                timeout_seconds=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
                fallback_models=self.settings.llm_fallback_models,
            )
        if self.settings.ollama_base_url and self.settings.ollama_model:
            return OllamaClient(
                self.settings.ollama_base_url,
                self.settings.ollama_model,
                timeout_seconds=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
            )
        return None

    def _decision_from_llm(self, question: str, sources: list[RetrievedDocument]) -> ModelDecision:
        """Route sales requests through a typed, live-schema provider contract."""

        if self.llm is None:
            raise RuntimeError("LLM is not configured.")

        safe_question = redact_pii_text(question)
        schema_catalog = self.repository.ai_schema_catalog()
        system = build_sql_router_system_prompt(schema_catalog)
        user_prompt = f"Sales question: {safe_question}"
        # Documentation is trusted project context, but keep it bounded so a
        # long document cannot crowd out the live schema that controls SQL.
        context = "\n\n".join(f"[{item.source}]\n{item.text[:500]}" for item in sources)
        if context.strip():
            user_prompt += f"\n\nRelevant business definitions:\n{context}"

        response = self.llm.complete(system, user_prompt)
        decision = _parse_model_decision(response)
        if decision.intent == "warehouse_query" and not decision.sql:
            raise LLMError("The AI marked this as a data query but did not return SQL.")
        return decision

    def _repair_sql(self, question: str, sql: str, safe_error: str) -> str | None:
        """Make at most one schema-constrained repair of rejected model SQL."""

        if self.llm is None:
            return None
        try:
            system = build_sql_repair_prompt(
                schema_catalog=self.repository.ai_schema_catalog(),
                question=redact_pii_text(question),
                sql=sanitize_secret_string(sql),
                error=safe_error,
            )
            payload = _parse_json_object(self.llm.complete(system, "Return the corrected JSON only."))
            repaired = payload.get("sql")
            if not isinstance(repaired, str) or not repaired.strip():
                return None
            return repaired.strip()
        except (LLMError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("LLM SQL repair failed; retaining the original safe failure.")
            return None

    @staticmethod
    def _is_sales_domain_question(question: str) -> bool:
        terms = {
            "sales", "sell", "selling", "seller", "revenue", "profit", "margin", "lead", "prospect",
            "pipeline", "customer", "client", "buyer", "deal", "quota", "pricing", "discount", "upsell",
            "cross-sell", "conversion", "retention", "churn", "product", "order", "invoice", "crm",
            "territory", "account", "commerce", "retail", "campaign",
        }
        tokens = set(re.findall(r"[a-z]+(?:-[a-z]+)?", question.lower()))
        return bool(tokens & terms)

    def _generate_insight(self, question: str, plan: QueryPlan, rows: list[dict[str, Any]]) -> str:
        """Synthesize a business insight from query results using the LLM.

        Falls back to the deterministic rule-based explanation if the LLM is
        unavailable or the plan was generated by local rules.
        """

        # For local-rule plans, try LLM insight but always have the rule-based fallback
        rule_based = self._explain(plan.kind, rows)

        # SQL generation already costs one provider call.  A second call for
        # prose doubles latency and failure opportunities, so it is explicitly
        # opt-in for deployments that value richer narrative over response time.
        if self.llm is None or not self.settings.ai_synthesize_insights:
            return rule_based

        if not rows:
            return rule_based

        try:
            # Results have already been PII-masked. Anonymize personal customer names
            # before transmitting sample rows to external LLM providers.
            sample_rows = [mask_record_pii(r, mask_names=True) for r in rows[:12]]
            rows_text = "\n".join(str(row) for row in sample_rows)
            if len(rows) > 12:
                rows_text += f"\n... ({len(rows)} total rows, showing first 12)"

            safe_question = redact_pii_text(question)
            user_prompt = (
                f"Question: {safe_question}\n\n"
                f"SQL executed: {plan.sql}\n\n"
                f"Results ({len(rows)} rows):\n{rows_text}"
            )

            curr_sym = self.repository.active_currency_symbol()
            curr_code = self.repository.active_currency_code()
            insight_prompt = build_insight_system_prompt(curr_sym, curr_code)
            insight = self.llm.complete(insight_prompt, user_prompt)
            return insight.strip()
        except LLMError:
            logger.warning("LLM insight synthesis failed, using rule-based explanation.")
            return rule_based

    def _plan_local_query(self, question: str) -> QueryPlan | None:
        """Cover dependable common questions when no provider is reachable."""

        lower = question.lower()
        if self.repository.active_sales_mode() == "flexible":
            return self._plan_flexible_local_query(lower)
        if any(phrase in lower for phrase in ("overview", "kpi", "total sales", "total revenue")):
            return QueryPlan(
                "overview",
                """SELECT SUM(net_revenue)::DOUBLE AS total_revenue, COUNT(DISTINCT order_id)::BIGINT AS total_orders,
                          COUNT(DISTINCT customer_id)::BIGINT AS active_customers,
                          AVG(net_revenue)::DOUBLE AS average_line_value
                   FROM analytics.fact_sales""",
                [],
            )
        limit_match = re.search(r"top\s+(\d+)", lower)
        if "top" in lower and any(term in lower for term in ("product", "products")):
            limit = min(int(limit_match.group(1)) if limit_match else 5, 25)
            return QueryPlan(
                "top_products",
                """SELECT p.product_name, p.category, SUM(f.net_revenue)::DOUBLE AS revenue,
                          SUM(f.quantity)::BIGINT AS units_sold
                   FROM analytics.fact_sales AS f
                   JOIN analytics.dim_product AS p ON p.product_id = f.product_id
                   GROUP BY p.product_name, p.category ORDER BY revenue DESC LIMIT ?""",
                [limit],
            )
        period = _question_month(lower)
        if ("why" in lower or "decrease" in lower or "increase" in lower or "change" in lower) and period:
            current_start, next_start, previous_start = period
            return QueryPlan(
                "change_drivers",
                """SELECT region,
                          SUM(CASE WHEN date_key >= ? AND date_key < ? THEN net_revenue ELSE 0 END)::DOUBLE AS current_revenue,
                          SUM(CASE WHEN date_key >= ? AND date_key < ? THEN net_revenue ELSE 0 END)::DOUBLE AS previous_revenue,
                          (SUM(CASE WHEN date_key >= ? AND date_key < ? THEN net_revenue ELSE 0 END) -
                           SUM(CASE WHEN date_key >= ? AND date_key < ? THEN net_revenue ELSE 0 END))::DOUBLE AS revenue_change
                   FROM analytics.fact_sales
                   WHERE date_key >= ? AND date_key < ?
                   GROUP BY region ORDER BY revenue_change ASC""",
                [current_start, next_start, previous_start, current_start, current_start, next_start, previous_start, current_start, previous_start, next_start],
            )
        if any(term in lower for term in ("revenue", "sales")) and "region" in lower:
            return QueryPlan(
                "revenue_by_region",
                """SELECT region, SUM(net_revenue)::DOUBLE AS revenue, COUNT(DISTINCT order_id)::BIGINT AS orders
                   FROM analytics.fact_sales GROUP BY region ORDER BY revenue DESC""",
                [],
            )
        cust_match = re.search(r"customer\s+(?:id\s*)?(?:=|is|:)?\s*([a-zA-Z0-9_-]+)", lower)
        if cust_match:
            cust_val = cust_match.group(1).strip()
            return QueryPlan(
                "customer_records",
                """SELECT c.customer_id, COALESCE(c.customer_name, 'Unknown') AS customer_name, c.email,
                          COALESCE(c.city, 'Unspecified') AS city, COALESCE(c.region, 'Unspecified') AS region,
                          COALESCE(c.segment, 'Standard') AS segment,
                          COUNT(DISTINCT f.order_id)::BIGINT AS total_orders,
                          COALESCE(SUM(f.net_revenue), 0)::DOUBLE AS total_spent
                   FROM analytics.dim_customer AS c
                   LEFT JOIN analytics.fact_sales AS f ON f.customer_id = c.customer_id
                   WHERE LOWER(c.customer_id) = ? OR LOWER(COALESCE(c.customer_name, 'unknown')) = ?
                   GROUP BY c.customer_id, c.customer_name, c.email, c.city, c.region, c.segment
                   LIMIT 25""",
                [cust_val, cust_val],
            )
        ord_match = re.search(r"order\s+(?:id\s*)?(?:=|is|:)?\s*([a-zA-Z0-9_-]+)", lower)
        if ord_match:
            ord_val = ord_match.group(1).strip()
            return QueryPlan(
                "order_records",
                """SELECT f.order_id, f.date_key AS order_date, f.customer_id, p.product_name,
                          f.quantity, f.unit_price, f.net_revenue, f.status
                   FROM analytics.fact_sales AS f
                   JOIN analytics.dim_product AS p ON p.product_id = f.product_id
                   WHERE LOWER(f.order_id) = ?
                   LIMIT 25""",
                [ord_val],
            )
        return None

    def _plan_flexible_local_query(self, lower: str) -> QueryPlan | None:
        profile = self.repository.flexible_sales_profile() or {}
        mapping = profile.get("semantic_mapping", {})
        if any(phrase in lower for phrase in ("overview", "kpi", "total sales", "total revenue")):
            return QueryPlan(
                "overview",
                """SELECT COALESCE(SUM(revenue), 0)::DOUBLE AS total_revenue,
                          COUNT(DISTINCT order_id)::BIGINT AS total_orders,
                          COUNT(DISTINCT customer)::BIGINT AS active_customers,
                          COALESCE(AVG(revenue), 0)::DOUBLE AS average_line_value
                   FROM analytics.flex_sales""",
                [],
            )
        limit_match = re.search(r"top\s+(\d+)", lower)
        if "top" in lower and any(term in lower for term in ("product", "products", "item", "items")):
            limit = min(int(limit_match.group(1)) if limit_match else 5, 25)
            return QueryPlan(
                "top_products",
                """SELECT product AS product_name, category, SUM(revenue)::DOUBLE AS revenue,
                          COALESCE(SUM(quantity), 0)::DOUBLE AS units_sold
                   FROM analytics.flex_sales
                   WHERE product IS NOT NULL
                   GROUP BY product, category ORDER BY revenue DESC LIMIT ?""",
                [limit],
            )
        period = _question_month(lower)
        if (
            "sales_date" in mapping
            and ("why" in lower or "decrease" in lower or "increase" in lower or "change" in lower)
            and period
        ):
            current_start, next_start, previous_start = period
            return QueryPlan(
                "change_drivers",
                """SELECT COALESCE(region, 'Unspecified') AS region,
                          SUM(CASE WHEN sales_date >= ? AND sales_date < ? THEN revenue ELSE 0 END)::DOUBLE AS current_revenue,
                          SUM(CASE WHEN sales_date >= ? AND sales_date < ? THEN revenue ELSE 0 END)::DOUBLE AS previous_revenue,
                          (SUM(CASE WHEN sales_date >= ? AND sales_date < ? THEN revenue ELSE 0 END) -
                           SUM(CASE WHEN sales_date >= ? AND sales_date < ? THEN revenue ELSE 0 END))::DOUBLE AS revenue_change
                   FROM analytics.flex_sales
                   WHERE sales_date >= ? AND sales_date < ?
                   GROUP BY COALESCE(region, 'Unspecified') ORDER BY revenue_change ASC""",
                [
                    current_start, next_start, previous_start, current_start,
                    current_start, next_start, previous_start, current_start,
                    previous_start, next_start,
                ],
            )
        if any(term in lower for term in ("revenue", "sales")) and "region" in lower:
            return QueryPlan(
                "revenue_by_region",
                """SELECT COALESCE(region, 'Unspecified') AS region, SUM(revenue)::DOUBLE AS revenue,
                          COUNT(DISTINCT order_id)::BIGINT AS orders
                   FROM analytics.flex_sales
                   GROUP BY COALESCE(region, 'Unspecified') ORDER BY revenue DESC""",
                [],
            )
        cust_match = re.search(r"customer\s+(?:id\s*)?(?:=|is|:)?\s*([a-zA-Z0-9_-]+)", lower)
        if cust_match:
            cust_val = cust_match.group(1).strip()
            return QueryPlan(
                "customer_records",
                """SELECT COALESCE(customer, 'Unknown') AS customer, COALESCE(product, 'Unknown Product') AS product,
                          COALESCE(category, 'Unspecified') AS category, COALESCE(region, 'Unspecified') AS region,
                          sales_date, order_id, revenue, COALESCE(status, 'completed') AS status
                   FROM analytics.flex_sales
                   WHERE LOWER(COALESCE(customer, 'unknown')) = ?
                   LIMIT 25""",
                [cust_val],
            )
        ord_match = re.search(r"order\s+(?:id\s*)?(?:=|is|:)?\s*([a-zA-Z0-9_-]+)", lower)
        if ord_match:
            ord_val = ord_match.group(1).strip()
            return QueryPlan(
                "order_records",
                """SELECT order_id, sales_date, customer, product, category, quantity, unit_price, revenue, status
                   FROM analytics.flex_sales
                   WHERE LOWER(order_id) = ?
                   LIMIT 25""",
                [ord_val],
            )
        return None

    def _explain(self, kind: str, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "The approved warehouse query returned no rows for that question."
        curr_sym = self.repository.active_currency_symbol()
        if kind == "overview":
            row = rows[0]
            return (
                f"Trusted warehouse overview: revenue is {curr_sym}{row['total_revenue']:,.2f} across "
                f"{row['total_orders']:,} completed orders and {row['active_customers']:,} active customers."
            )
        if kind == "top_products":
            first = rows[0]
            return f"The top product is {first['product_name']} with revenue of {curr_sym}{first['revenue']:,.2f}."
        if kind == "revenue_by_region":
            return f"Revenue by region ({curr_sym}) is shown below, ordered from highest to lowest trusted warehouse revenue."
        if kind == "customer_records":
            cust_name = rows[0].get("customer") or rows[0].get("customer_name") or "customer"
            return f"Found {len(rows)} matching record(s) for customer {cust_name} in the active warehouse."
        if kind == "order_records":
            ord_id = rows[0].get("order_id") or "specified order"
            return f"Found {len(rows)} matching line item(s) for order {ord_id} in the active warehouse."
        if kind == "change_drivers":
            total_current = sum(float(row["current_revenue"]) for row in rows)
            total_previous = sum(float(row["previous_revenue"]) for row in rows)
            change = total_current - total_previous
            percent = (100 * change / total_previous) if total_previous else 0
            direction = "decreased" if change < 0 else "increased"
            biggest = (
                min(rows, key=lambda row: float(row["revenue_change"]))
                if change < 0
                else max(rows, key=lambda row: float(row["revenue_change"]))
            )
            return (
                f"Revenue {direction} by {abs(percent):.1f}% month-over-month. "
                f"The largest regional driver was {biggest['region']} with a change of {curr_sym}{biggest['revenue_change']:,.2f}."
            )
        return f"The approved query returned {len(rows)} row(s) from the warehouse."


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a small model response without accepting prose as executable SQL."""

    candidate = raw.strip()
    # 1. Direct JSON parse
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict):
            return payload
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Extract from markdown code fence ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence_match:
        try:
            payload = json.loads(fence_match.group(1).strip())
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Locate outermost curly braces { ... }
    first_brace = candidate.find("{")
    last_brace = candidate.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        snippet = candidate[first_brace : last_brace + 1].strip()
        try:
            payload = json.loads(snippet)
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, TypeError):
            pass

    raise LLMError("The AI returned malformed structured output.")


def _parse_model_decision(raw: str) -> ModelDecision:
    payload = _parse_json_object(raw)
    intent = str(payload.get("intent", "")).strip().lower()
    if intent not in {"warehouse_query", "sales_guidance", "clarify", "out_of_scope"}:
        raise LLMError("The AI returned an unsupported request intent.")
    sql = payload.get("sql")
    answer = payload.get("answer")
    if sql is not None and (not isinstance(sql, str) or len(sql) > 12_000):
        raise LLMError("The AI returned an invalid SQL field.")
    if answer is not None and (not isinstance(answer, str) or len(answer) > 4_000):
        raise LLMError("The AI returned an invalid answer field.")
    return ModelDecision(intent=intent, sql=sql.strip() if isinstance(sql, str) else None, answer=answer.strip() if isinstance(answer, str) else None)


def _append_warning(current: str | None, additional: str) -> str:
    return f"{current} {additional}" if current else additional


def _question_month(question: str) -> tuple[str, str, str] | None:
    match = re.search(
        r"\b(" + "|".join(calendar.month_name[1:]) + r")\s*(20\d{2})?\b",
        question,
        re.IGNORECASE,
    )
    if not match:
        return None
    month = list(calendar.month_name).index(match.group(1).title())
    # Never quietly assume a hard-coded year.  A provider can reason about a
    # relative period using live data; the offline fallback instead asks for a
    # year through its normal unsupported/clarification response.
    if not match.group(2):
        return None
    year = int(match.group(2))
    current = date(year, month, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    previous = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    return current.isoformat(), next_month.isoformat(), previous.isoformat()
