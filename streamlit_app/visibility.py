"""Visibility rules for data returned to the Streamlit client."""
from __future__ import annotations

import re
from secrets import token_hex

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from dataflow_ai.ingestion.flexible_sales import SEMANTIC_ALIASES

_ID_WORD = re.compile(r"(?:^|_)(?:id|ids|uuid|guid|identifier|key|sku|code|number|no)(?:_|$)")
_ID_VALUE = re.compile(
    r"(?i)\b(?:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|"
    r"[0-9a-f]{32,64}|(?:ORD|INV|SKU|CUST|EVT|CMP|P|C)-\d+(?:-\d+)*)\b"
)


def public_text(value):
    return _ID_VALUE.sub("[hidden]", value) if isinstance(value, str) else value


def private_column(name):
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()
    if name in {"date_key", "currency_code"}:
        return False
    return bool(_ID_WORD.search(name)) or name in {
        "attributes_json", "source_file", "source_row", "email", "address",
    }


class DisplayPolicy:
    def __init__(self, repository):
        profile = repository.flexible_sales_profile() or {}
        sources = set(profile.get("column_types", {}))
        self.hidden = set()
        for field in ("customer", "product"):
            if any(private_column(col) for col in sources.intersection(SEMANTIC_ALIASES[field])):
                self.hidden.update((field, field + "_name"))
        self.upload_columns = {col for names in SEMANTIC_ALIASES.values() for col in names}

    def visible(self, name, table=""):
        return (not private_column(name) and name not in self.hidden
                and (table != "sales_upload" or name in self.upload_columns))

    def rows(self, records, table=""):
        return [{k: public_text(v) for k, v in row.items()
                 if self.visible(k, table) and not isinstance(v, (dict, list, tuple))}
                for row in records]

    def prepare_query(self, sql, repository):
        """Resolve output expressions before allowing the browser or AI to see results."""
        tables = {name.split(".")[-1] for name in repository.ai_allowed_tables()}
        schema = {}
        for row in repository.fetch_all(
            "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='analytics'"
        ):
            table, field = row["table_name"], row["column_name"]
            if table in tables:
                schema.setdefault(table, {})[field] = row["data_type"]
        tree = qualify(sqlglot.parse_one(sql, read="duckdb"), dialect="duckdb",
                       schema={"analytics": schema}, infer_schema=False)
        # Expand SELECT * through the catalog. Reject unresolved dynamic projections.
        if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
            raise ValueError("Choose named reporting fields.")
        internal_fields = set()
        folded_fields = {}
        salt = exp.Literal.string(token_hex(32))

        def digest(value):
            return exp.func("SHA256", exp.func("CONCAT", salt.copy(), value))

        for col in list(tree.find_all(exp.Column)):
            field = col.name
            if self.visible(field) and (field not in schema.get("sales_upload", {})
                                        or field in self.upload_columns):
                continue
            parent = col.parent
            if (isinstance(parent, exp.Nullif) and parent.this is col
                    and isinstance(parent.expression, exp.Literal)):
                parent = parent.parent
            if isinstance(parent, exp.Distinct):
                parent = parent.parent
            if isinstance(parent, exp.Count) and not parent.args.get("expressions"):
                internal_fields.add(field)
                if isinstance(col.parent, exp.Nullif):
                    literal = col.parent.expression
                    literal.replace(digest(literal.copy()))
                continue
            if isinstance(col.parent, exp.Is) and isinstance(col.parent.expression, exp.Null):
                internal_fields.add(field)
                continue
            if isinstance(col.parent, exp.Group):
                internal_fields.add(field)
                continue
            operand = col.parent if isinstance(col.parent, exp.Lower) else col
            comparison = operand.parent
            if isinstance(comparison, exp.EQ) and comparison.find_ancestor(exp.Where):
                other = comparison.expression if comparison.this is operand else comparison.this
                if isinstance(other, (exp.Placeholder, exp.Literal)):
                    internal_fields.add(field)
                    if operand is not col:
                        alias = folded_fields.setdefault(field, "_lookup_" + token_hex(12))
                        operand.replace(exp.column(alias, table=col.table, quoted=True))
                    other.replace(digest(other.copy()))
                    continue
            if isinstance(col.parent, exp.EQ) and isinstance(col.parent.parent, exp.Join):
                internal_fields.add(field)
                continue
            col.replace(exp.Null())
        # Keep raw keys out of every relation available to the outer query.
        # A per-query digest preserves equality for joins and distinct counts.
        for table in list(tree.find_all(exp.Table)):
            if table.db != "analytics":
                continue
            projections = []
            for name in schema[table.name]:
                column = exp.column(name, quoted=True)
                if self.visible(name, table.name):
                    projections.append(column)
                elif name in internal_fields:
                    value = exp.Case().when(column.copy().is_(exp.Null()), exp.Null()).else_(digest(column.copy()))
                    projections.append(value.as_(name, quoted=True))
                else:
                    projections.append(exp.Null().as_(name, quoted=True))
                if name in folded_fields:
                    folded = digest(exp.Lower(this=column.copy()))
                    projections.append(folded.as_(folded_fields[name], quoted=True))
            source = exp.table_(table.name, db="analytics", quoted=True)
            table.replace(exp.select(*projections).from_(source).subquery(table.alias_or_name))
        return tree.sql(dialect="duckdb")
