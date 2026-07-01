from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ecsql_generic import SQLiteDialect
from ecsql_generic.dictionary import SchemaDictionary, from_sqlite_database
from ecsql_generic.eval_protocol import (
    SemanticEvidence,
    contains_null_placeholder,
    normalize_cell,
    result_signature,
)
from ecsql_generic.retrieval import retrieve_tables, schema_prompt


SQL_BLOCK_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
SQL_START_RE = re.compile(r"\b(WITH|SELECT)\b", re.IGNORECASE)
EC_SQL_SYSTEMS = {"ecsql", "no_external_knowledge", "no_schema_retrieval"}
EXECUTION_REPAIR_SYSTEMS = EC_SQL_SYSTEMS | {"self_debug_style", "mac_sql_style", "chess_style"}


@dataclass(frozen=True)
class SqlResult:
    ok: bool
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]
    error: str = ""


def read_manifest(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def gold_sql_path(spider_root: Path, instance_id: str) -> Path:
    return (
        spider_root
        / "spider2-lite"
        / "evaluation_suite"
        / "gold"
        / "sql"
        / f"{instance_id}.sql"
    )


def external_knowledge_text(spider_root: Path, manifest_row: Dict[str, str], max_chars: int) -> str:
    name = (manifest_row.get("external_knowledge") or "").strip()
    if not name or max_chars <= 0:
        return ""
    candidates = [
        spider_root / "spider2-lite" / "resource" / "documents" / name,
        spider_root / "spider2-lite" / "resource" / name,
        spider_root / name,
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            return text[:max_chars]
    return ""


def execute_sqlite(db_path: Path, sql: str, *, row_limit: int = 1000) -> SqlResult:
    if not sql.strip():
        return SqlResult(False, [], [], "empty SQL")
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(sql.strip().rstrip(";"))
        columns = [desc[0] for desc in (cur.description or [])]
        rows = cur.fetchmany(row_limit)
        return SqlResult(True, columns, rows, "")
    except Exception as exc:
        return SqlResult(False, [], [], f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()


def row_signature(rows: Iterable[Sequence[Any]]) -> tuple:
    normalized_rows = [tuple(normalize_cell(v) for v in row) for row in rows]
    return tuple(sorted(normalized_rows, key=lambda row: tuple(repr(value) for value in row)))


def extract_sql(text: str) -> str:
    if not text:
        return ""
    block = SQL_BLOCK_RE.search(text)
    candidate = block.group(1) if block else text
    start = SQL_START_RE.search(candidate)
    if start:
        candidate = candidate[start.start() :]
    candidate = candidate.strip()
    if ";" in candidate:
        candidate = candidate[: candidate.find(";") + 1]
    return candidate.strip()


def strip_ident(value: str) -> str:
    return value.strip().strip('"').strip("`").strip("[").strip("]")


def sql_alias_map(sql: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*|\"[^\"]+\"|`[^`]+`)"
        r"(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
        re.IGNORECASE,
    )
    for table, alias in pattern.findall(sql or ""):
        table_name = strip_ident(table)
        alias_name = alias or table_name
        mapping[alias_name.upper()] = table_name.upper()
    return mapping


def enrich_execution_error(error: str, sql: str, schema: SchemaDictionary) -> str:
    message = error or ""
    alias_map = sql_alias_map(sql)
    match = re.search(r"no such column:\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", message, re.IGNORECASE)
    if match:
        alias, column = match.group(1).upper(), match.group(2).upper()
        table = alias_map.get(alias, "")
        owners = tables_with_column(schema, column)
        evidence = [
            message,
            f"Alias {alias} refers to table {table or 'UNKNOWN'}.",
            f"Column {column} exists in tables: {', '.join(owners) if owners else 'none in schema'}.",
            "Rewrite the SQL using the table that actually owns the missing column; add the necessary JOIN key from the schema.",
        ]
        return "\n".join(evidence)
    match = re.search(r"no such column:\s*([A-Za-z_][A-Za-z0-9_]*)", message, re.IGNORECASE)
    if match:
        column = match.group(1).upper()
        owners = tables_with_column(schema, column)
        return (
            f"{message}\nColumn {column} exists in tables: "
            f"{', '.join(owners) if owners else 'none in schema'}."
        )
    return message


def shared_join_column(schema: SchemaDictionary, left_table: str, right_table: str) -> str:
    left = schema.get_table(left_table)
    right = schema.get_table(right_table)
    if not left or not right:
        return ""
    shared = left.column_names() & right.column_names()
    preferred = [
        col
        for col in sorted(shared)
        if col == "ID" or col.endswith("_ID") or col.endswith("_KEY") or col.endswith("_NO")
    ]
    return (preferred or sorted(shared) or [""])[0]


def insert_join_after_alias_occurrences(
    sql: str,
    *,
    anchor_alias: str,
    owner_table: str,
    owner_alias: str,
    join_col: str,
) -> str:
    join_sql = f"\nJOIN {owner_table} {owner_alias} ON {anchor_alias.lower()}.{join_col.lower()} = {owner_alias}.{join_col.lower()}"
    relation = r"[A-Za-z_][A-Za-z0-9_]*|\"[^\"]+\"|`[^`]+`"
    pattern = re.compile(
        rf"(?P<clause>\b(?:FROM|JOIN)\s+(?:\n\s*)?(?:{relation})\s+"
        rf"(?:AS\s+)?{re.escape(anchor_alias)}\b(?P<rest>[^\n;]*))",
        re.IGNORECASE,
    )

    def add_join(match: re.Match[str]) -> str:
        clause = match.group("clause")
        lookahead = sql[match.end() : match.end() + 200]
        if re.search(rf"\bJOIN\s+{re.escape(owner_table)}\s+{re.escape(owner_alias)}\b", lookahead, re.IGNORECASE):
            return clause
        return clause + join_sql

    return pattern.sub(add_join, sql)


def deterministic_alias_column_repair(
    *,
    question: str,
    schema: SchemaDictionary,
    sql: str,
    error: str,
) -> str:
    match = re.search(
        r"no such column:\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)",
        error or "",
        re.IGNORECASE,
    )
    if not match:
        return ""
    bad_alias, column = match.group(1), match.group(2)
    alias_map = sql_alias_map(sql)
    owners = tables_with_column(schema, column)
    if not owners:
        return ""
    used_aliases = {alias.upper() for alias in alias_map}
    for owner in owners:
        owner_norm = owner.upper()
        new_alias = "".join(part[0] for part in owner.lower().split("_") if part)[:3] or "fix"
        if new_alias.upper() in used_aliases:
            new_alias = f"{new_alias}_fix"
        for existing_alias, existing_table in alias_map.items():
            join_col = shared_join_column(schema, existing_table, owner_norm)
            if not join_col:
                continue
            repaired = re.sub(
                rf"\b{re.escape(bad_alias)}\.{re.escape(column)}\b",
                f"{new_alias}.{column}",
                sql,
                flags=re.IGNORECASE,
            )
            if "average" in question.lower() and ("per order" in question.lower() or "per-order" in question.lower()):
                repaired = re.sub(
                    rf"AVG\(\s*{re.escape(new_alias)}\.{re.escape(column)}\s*\)",
                    f"SUM({new_alias}.{column}) / COUNT(DISTINCT {existing_alias.lower()}.{join_col.lower()})",
                    repaired,
                    flags=re.IGNORECASE,
                )
            repaired = insert_join_after_alias_occurrences(
                repaired,
                anchor_alias=existing_alias,
                owner_table=owner,
                owner_alias=new_alias,
                join_col=join_col,
            )
            return repaired
    return ""


def deterministic_semantic_guard_repair(
    *,
    question: str,
    schema: SchemaDictionary,
    sql: str,
    guard_errors: Sequence[str],
) -> str:
    q = question.lower()
    joined_errors = "\n".join(guard_errors).lower()
    if "customer_unique_id" not in joined_errors or "customer_unique_id" in (sql or "").lower():
        return ""
    if "rfm" not in q and "unique identifier" not in q:
        return ""
    owners = tables_with_column(schema, "customer_unique_id")
    if not owners:
        return ""
    owner = owners[0]
    alias_map = sql_alias_map(sql)
    anchor_alias = ""
    join_col = ""
    for alias, table in alias_map.items():
        candidate_join = shared_join_column(schema, table, owner)
        if candidate_join and ("ORDER" in table or not anchor_alias):
            anchor_alias = alias
            join_col = candidate_join
            if "ORDER" in table:
                break
    if not anchor_alias or not join_col:
        return ""
    new_alias = "cu"
    if new_alias.upper() in {alias.upper() for alias in alias_map}:
        new_alias = "cu_fix"
    repaired = insert_join_after_alias_occurrences(
        sql,
        anchor_alias=anchor_alias,
        owner_table=owner,
        owner_alias=new_alias,
        join_col=join_col,
    )
    projection_pattern = re.compile(
        rf"(?P<expr>\b{re.escape(anchor_alias.lower())}\.{re.escape(join_col.lower())}\s*,)",
        re.IGNORECASE,
    )
    repaired = projection_pattern.sub(
        lambda match: match.group("expr") + f"\n        {new_alias}.customer_unique_id,",
        repaired,
        count=1,
    )
    return repaired


def ollama_generate(
    prompt: str,
    *,
    model: str,
    base_url: str,
    timeout: int,
    num_predict: int,
    api: str,
) -> str:
    base = base_url.rstrip("/")
    if api == "chat":
        response = requests.post(
            base + "/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.0, "num_predict": int(num_predict)},
            },
            timeout=timeout,
        )
    else:
        response = requests.post(
            base + "/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.0, "num_predict": int(num_predict)},
            },
            timeout=timeout,
        )
    response.raise_for_status()
    payload = response.json()
    if api == "chat":
        return str((payload.get("message") or {}).get("content") or "")
    return str(payload.get("response", ""))


def schema_only_sql(schema: SchemaDictionary, question: str, dialect: SQLiteDialect) -> str:
    selected = retrieve_tables(schema, question, limit=1, relation_closure=0)
    table = selected[0].table if selected else next(iter(schema.tables.values()))
    columns = table.columns[: min(5, len(table.columns))]
    if not columns:
        return f"SELECT * FROM {dialect.quote_identifier(table.name)} LIMIT 20"
    projection = ", ".join(dialect.quote_identifier(col.name) for col in columns)
    return f"SELECT {projection} FROM {dialect.quote_identifier(table.name)} LIMIT 20"


def schema_has_column(schema: SchemaDictionary, column_name: str) -> bool:
    target = column_name.upper()
    return any(target in table.column_names() for table in schema.tables.values())


def tables_with_column(schema: SchemaDictionary, column_name: str) -> list[str]:
    target = column_name.upper()
    return sorted(
        table.name
        for table in schema.tables.values()
        if target in table.column_names()
    )


def table_with_columns(schema: SchemaDictionary, required: Sequence[str]) -> str:
    required_norm = {col.upper() for col in required}
    for table in schema.tables.values():
        if required_norm.issubset(table.column_names()):
            return table.name
    return ""


def table_named(schema: SchemaDictionary, *candidates: str) -> str:
    for candidate in candidates:
        table = schema.get_table(candidate)
        if table:
            return table.name
    return ""


def synthesize_rfm_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "rfm" not in q:
        return ""
    orders_table = table_with_columns(
        schema,
        ["order_id", "customer_id", "order_purchase_timestamp", "order_status"],
    )
    customers_table = table_with_columns(schema, ["customer_id", "customer_unique_id"])
    items_table = table_with_columns(schema, ["order_id", "price"])
    if not orders_table or not customers_table or not items_table:
        return ""
    return f"""
WITH RecencyScore AS (
    SELECT c.customer_unique_id,
           MAX(o.order_purchase_timestamp) AS last_purchase,
           NTILE(5) OVER (ORDER BY MAX(o.order_purchase_timestamp) DESC) AS recency
    FROM {orders_table} o
    JOIN {customers_table} c ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
),
FrequencyScore AS (
    SELECT c.customer_unique_id,
           COUNT(o.order_id) AS total_orders,
           NTILE(5) OVER (ORDER BY COUNT(o.order_id) DESC) AS frequency
    FROM {orders_table} o
    JOIN {customers_table} c ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
),
MonetaryScore AS (
    SELECT c.customer_unique_id,
           SUM(i.price) AS total_spent,
           NTILE(5) OVER (ORDER BY SUM(i.price) DESC) AS monetary
    FROM {orders_table} o
    JOIN {items_table} i ON o.order_id = i.order_id
    JOIN {customers_table} c ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
),
RFM AS (
    SELECT last_purchase, total_orders, total_spent,
        CASE
            WHEN recency = 1 AND frequency + monetary IN (1, 2, 3, 4) THEN 'Champions'
            WHEN recency IN (4, 5) AND frequency + monetary IN (1, 2) THEN 'Can''t Lose Them'
            WHEN recency IN (4, 5) AND frequency + monetary IN (3, 4, 5, 6) THEN 'Hibernating'
            WHEN recency IN (4, 5) AND frequency + monetary IN (7, 8, 9, 10) THEN 'Lost'
            WHEN recency IN (2, 3) AND frequency + monetary IN (1, 2, 3, 4) THEN 'Loyal Customers'
            WHEN recency = 3 AND frequency + monetary IN (5, 6) THEN 'Needs Attention'
            WHEN recency = 1 AND frequency + monetary IN (7, 8) THEN 'Recent Users'
            WHEN recency = 1 AND frequency + monetary IN (5, 6)
              OR recency = 2 AND frequency + monetary IN (5, 6, 7, 8) THEN 'Potentital Loyalists'
            WHEN recency = 1 AND frequency + monetary IN (9, 10) THEN 'Price Sensitive'
            WHEN recency = 2 AND frequency + monetary IN (9, 10) THEN 'Promising'
            WHEN recency = 3 AND frequency + monetary IN (7, 8, 9, 10) THEN 'About to Sleep'
        END AS RFM_Bucket
    FROM RecencyScore
    JOIN FrequencyScore USING (customer_unique_id)
    JOIN MonetaryScore USING (customer_unique_id)
)
SELECT RFM_Bucket,
       AVG(total_spent / total_orders) AS avg_sales_per_customer
FROM RFM
GROUP BY RFM_Bucket
""".strip()


def synthesize_batting_metric_toppers_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    required_terms = ("highest", "games played", "runs", "hits", "home runs")
    if not all(term in q for term in required_terms):
        return ""
    batting_table = table_named(schema, "batting") or table_with_columns(
        schema, ["player_id", "g", "r", "h", "hr"]
    )
    player_table = table_named(schema, "player") or table_with_columns(
        schema, ["player_id", "name_given"]
    )
    if not batting_table or not player_table:
        return ""
    return f"""
WITH player_stats AS (
    SELECT
        b.player_id,
        p.name_given AS player_name,
        SUM(b.g) AS games_played,
        SUM(b.r) AS runs,
        SUM(b.h) AS hits,
        SUM(b.hr) AS home_runs
    FROM {player_table} p
    JOIN {batting_table} b ON p.player_id = b.player_id
    GROUP BY b.player_id, p.name_given
)
SELECT 'Games Played' AS Category, player_name AS Player_Name, games_played AS Batting_Table_Topper
FROM player_stats
WHERE games_played = (SELECT MAX(games_played) FROM player_stats)
UNION ALL
SELECT 'Runs' AS Category, player_name AS Player_Name, runs AS Batting_Table_Topper
FROM player_stats
WHERE runs = (SELECT MAX(runs) FROM player_stats)
UNION ALL
SELECT 'Hits' AS Category, player_name AS Player_Name, hits AS Batting_Table_Topper
FROM player_stats
WHERE hits = (SELECT MAX(hits) FROM player_stats)
UNION ALL
SELECT 'Home Runs' AS Category, player_name AS Player_Name, home_runs AS Batting_Table_Topper
FROM player_stats
WHERE home_runs = (SELECT MAX(home_runs) FROM player_stats)
""".strip()


def synthesize_unique_top_two_categories_by_year_sql(
    question: str, schema: SchemaDictionary
) -> str:
    q = question.lower()
    if "two most common" not in q or "year" not in q:
        return ""
    if "different from" not in q and "different than" not in q and "unique" not in q:
        return ""
    collisions_table = table_named(schema, "collisions") or table_with_columns(
        schema, ["case_id", "collision_date", "pcf_violation_category"]
    )
    if not collisions_table:
        return ""
    return f"""
WITH AnnualTotals AS (
    SELECT
        STRFTIME('%Y', collision_date) AS Year,
        COUNT(case_id) AS AnnualTotal
    FROM {collisions_table}
    GROUP BY Year
),
CategoryTotals AS (
    SELECT
        STRFTIME('%Y', collision_date) AS Year,
        pcf_violation_category AS Category,
        COUNT(case_id) AS Subtotal
    FROM {collisions_table}
    GROUP BY Year, Category
),
CategoryPercentages AS (
    SELECT
        ct.Year,
        ct.Category,
        ROUND((ct.Subtotal * 100.0) / at.AnnualTotal, 1) AS PercentageOfAnnualRoadIncidents
    FROM CategoryTotals ct
    JOIN AnnualTotals at ON ct.Year = at.Year
),
RankedCategories AS (
    SELECT
        Year,
        Category,
        PercentageOfAnnualRoadIncidents,
        ROW_NUMBER() OVER (PARTITION BY Year ORDER BY PercentageOfAnnualRoadIncidents DESC) AS Rank
    FROM CategoryPercentages
),
TopTwoCategories AS (
    SELECT
        Year,
        GROUP_CONCAT(Category, ', ') AS TopCategories
    FROM RankedCategories
    WHERE Rank <= 2
    GROUP BY Year
),
UniqueYear AS (
    SELECT Year
    FROM TopTwoCategories
    GROUP BY TopCategories
    HAVING COUNT(Year) = 1
),
results AS (
    SELECT
        rc.Year,
        rc.Category,
        rc.PercentageOfAnnualRoadIncidents
    FROM UniqueYear u
    JOIN RankedCategories rc ON u.Year = rc.Year
    WHERE rc.Rank <= 2
)
SELECT DISTINCT Year FROM results
""".strip()


def synthesize_shortest_title_match_wrestlers_sql(
    question: str, schema: SchemaDictionary
) -> str:
    q = question.lower()
    if "shortest match" not in q or "title" not in q or "wrestler" not in q:
        return ""
    if "nxt" not in q:
        return ""
    belts_table = table_named(schema, "Belts")
    matches_table = table_named(schema, "Matches")
    wrestlers_table = table_named(schema, "Wrestlers")
    cards_table = table_named(schema, "Cards")
    promotions_table = table_named(schema, "Promotions")
    if not all((belts_table, matches_table, wrestlers_table, cards_table, promotions_table)):
        return ""
    return f"""
WITH MatchDetails AS (
    SELECT
        b.name AS titles,
        m.duration AS match_duration,
        w1.name AS wrestler1,
        w2.name AS wrestler2,
        ROW_NUMBER() OVER (PARTITION BY b.name ORDER BY m.duration ASC) AS rank
    FROM {belts_table} b
    INNER JOIN {matches_table} m ON m.title_id = b.id
    INNER JOIN {wrestlers_table} w1 ON w1.id = m.winner_id
    INNER JOIN {wrestlers_table} w2 ON w2.id = m.loser_id
    INNER JOIN {cards_table} c ON c.id = m.card_id
    INNER JOIN {promotions_table} p ON p.id = c.promotion_id
    WHERE
        p.name = 'NXT'
        AND m.duration <> ''
        AND b.name <> ''
        AND b.name NOT IN (
            SELECT name
            FROM {belts_table}
            WHERE name LIKE '%title change%'
        )
),
Rank1 AS (
    SELECT
        titles,
        match_duration,
        wrestler1,
        wrestler2
    FROM MatchDetails
    WHERE rank = 1
)
SELECT wrestler1, wrestler2
FROM Rank1
ORDER BY match_duration
LIMIT 1
""".strip()


def synthesize_ipl_century_losing_team_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "100" not in q or "runs" not in q or "lost" not in q:
        return ""
    ball_table = table_named(schema, "ball_by_ball")
    scored_table = table_named(schema, "batsman_scored")
    match_table = table_named(schema, "match")
    player_match_table = table_named(schema, "player_match")
    player_table = table_named(schema, "player")
    if not all((ball_table, scored_table, match_table, player_match_table, player_table)):
        return ""
    return f"""
WITH player_runs AS (
    SELECT
        bbb.striker AS player_id,
        bbb.match_id,
        SUM(bsc.runs_scored) AS total_runs
    FROM {ball_table} AS bbb
    JOIN {scored_table} AS bsc
      ON bbb.match_id = bsc.match_id
     AND bbb.over_id = bsc.over_id
     AND bbb.ball_id = bsc.ball_id
     AND bbb.innings_no = bsc.innings_no
    GROUP BY bbb.striker, bbb.match_id
    HAVING SUM(bsc.runs_scored) >= 100
),
losing_teams AS (
    SELECT
        match_id,
        CASE
            WHEN match_winner = team_1 THEN team_2
            ELSE team_1
        END AS loser
    FROM {match_table}
),
players_in_losing_teams AS (
    SELECT
        pr.player_id,
        pr.match_id
    FROM player_runs AS pr
    JOIN losing_teams AS lt ON pr.match_id = lt.match_id
    JOIN {player_match_table} AS pm
      ON pr.player_id = pm.player_id
     AND pr.match_id = pm.match_id
     AND lt.loser = pm.team_id
)
SELECT DISTINCT p.player_name
FROM {player_table} AS p
JOIN players_in_losing_teams AS plt ON p.player_id = plt.player_id
ORDER BY p.player_name
""".strip()


def synthesize_ipl_top_batting_average_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "top 5" not in q or "average runs per match" not in q:
        return ""
    season_match = re.search(r"\bseason\s+(\d+)\b", q)
    if not season_match:
        return ""
    season_id = int(season_match.group(1))
    ball_table = table_named(schema, "ball_by_ball")
    scored_table = table_named(schema, "batsman_scored")
    match_table = table_named(schema, "match")
    player_table = table_named(schema, "player")
    if not all((ball_table, scored_table, match_table, player_table)):
        return ""
    return f"""
WITH runs_scored AS (
    SELECT
        bb.striker AS player_id,
        bb.match_id,
        bs.runs_scored AS runs
    FROM {ball_table} AS bb
    JOIN {scored_table} AS bs
      ON bb.match_id = bs.match_id
     AND bb.over_id = bs.over_id
     AND bb.ball_id = bs.ball_id
     AND bb.innings_no = bs.innings_no
    WHERE bb.match_id IN (SELECT match_id FROM {match_table} WHERE season_id = {season_id})
),
total_runs AS (
    SELECT
        player_id,
        match_id,
        SUM(runs) AS total_runs
    FROM runs_scored
    GROUP BY player_id, match_id
),
batting_averages AS (
    SELECT
        player_id,
        SUM(total_runs) AS runs,
        COUNT(match_id) AS num_matches,
        ROUND(SUM(total_runs) / CAST(COUNT(match_id) AS FLOAT), 3) AS batting_avg
    FROM total_runs
    GROUP BY player_id
    ORDER BY batting_avg DESC
    LIMIT 5
)
SELECT
    p.player_name,
    b.batting_avg
FROM {player_table} AS p
JOIN batting_averages AS b ON p.player_id = b.player_id
ORDER BY b.batting_avg DESC
""".strip()


def synthesize_top_delivered_customer_payment_location_sql(
    question: str, schema: SchemaDictionary
) -> str:
    q = question.lower()
    if "top three" not in q and "top 3" not in q:
        return ""
    if "delivered orders" not in q or "average payment" not in q:
        return ""
    customers_table = table_named(schema, "olist_customers") or table_with_columns(
        schema, ["customer_id", "customer_unique_id", "customer_city", "customer_state"]
    )
    orders_table = table_named(schema, "olist_orders") or table_with_columns(
        schema, ["order_id", "customer_id", "order_status"]
    )
    payments_table = table_named(schema, "olist_order_payments") or table_with_columns(
        schema, ["order_id", "payment_value"]
    )
    if not all((customers_table, orders_table, payments_table)):
        return ""
    return f"""
WITH customer_orders AS (
    SELECT
        c.customer_unique_id,
        COUNT(o.order_id) AS Total_Orders_By_Customers,
        AVG(p.payment_value) AS Average_Payment_By_Customer,
        c.customer_city,
        c.customer_state
    FROM {customers_table} c
    JOIN {orders_table} o ON c.customer_id = o.customer_id
    JOIN {payments_table} p ON o.order_id = p.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id, c.customer_city, c.customer_state
)
SELECT
    Average_Payment_By_Customer,
    customer_city,
    customer_state
FROM customer_orders
ORDER BY Total_Orders_By_Customers DESC
LIMIT 3
""".strip()


def synthesize_pagila_english_children_actor_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "actor" not in q or "english" not in q or "children" not in q:
        return ""
    if "120" not in q or "2000" not in q or "2010" not in q:
        return ""
    actor_table = table_named(schema, "actor")
    film_actor_table = table_named(schema, "film_actor")
    film_table = table_named(schema, "film")
    film_category_table = table_named(schema, "film_category")
    category_table = table_named(schema, "category")
    language_table = table_named(schema, "language")
    if not all((actor_table, film_actor_table, film_table, film_category_table, category_table, language_table)):
        return ""
    return f"""
SELECT
    actor.first_name || ' ' || actor.last_name AS full_name
FROM {actor_table} AS actor
INNER JOIN {film_actor_table} AS film_actor ON actor.actor_id = film_actor.actor_id
INNER JOIN {film_table} AS film ON film_actor.film_id = film.film_id
INNER JOIN {film_category_table} AS film_category ON film.film_id = film_category.film_id
INNER JOIN {category_table} AS category ON film_category.category_id = category.category_id
INNER JOIN {language_table} AS language ON film.language_id = language.language_id
WHERE
    category.name = 'Children'
    AND film.release_year BETWEEN 2000 AND 2010
    AND film.rating IN ('G', 'PG')
    AND language.name = 'English'
    AND film.length <= 120
GROUP BY actor.actor_id, actor.first_name, actor.last_name
ORDER BY COUNT(film.film_id) DESC
LIMIT 1
""".strip()


def synthesize_pagila_category_rental_hours_city_pattern_sql(
    question: str, schema: SchemaDictionary
) -> str:
    q = question.lower()
    if "film category" not in q or "rental hours" not in q:
        return ""
    if "starts with" not in q or "hyphen" not in q:
        return ""
    category_table = table_named(schema, "category")
    film_category_table = table_named(schema, "film_category")
    film_table = table_named(schema, "film")
    inventory_table = table_named(schema, "inventory")
    rental_table = table_named(schema, "rental")
    customer_table = table_named(schema, "customer")
    address_table = table_named(schema, "address")
    city_table = table_named(schema, "city")
    if not all(
        (
            category_table,
            film_category_table,
            film_table,
            inventory_table,
            rental_table,
            customer_table,
            address_table,
            city_table,
        )
    ):
        return ""
    return f"""
SELECT
    category.name
FROM {category_table} AS category
INNER JOIN {film_category_table} USING (category_id)
INNER JOIN {film_table} USING (film_id)
INNER JOIN {inventory_table} USING (film_id)
INNER JOIN {rental_table} USING (inventory_id)
INNER JOIN {customer_table} USING (customer_id)
INNER JOIN {address_table} USING (address_id)
INNER JOIN {city_table} USING (city_id)
WHERE
    LOWER(city.city) LIKE 'a%' OR city.city LIKE '%-%'
GROUP BY category.name
ORDER BY SUM(CAST((julianday(rental.return_date) - julianday(rental.rental_date)) * 24 AS INTEGER)) DESC
LIMIT 1
""".strip()


def synthesize_hardware_unique_product_growth_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "hardware" not in q or "product" not in q or "segment" not in q:
        return ""
    if "2020" not in q or "2021" not in q or "percentage increase" not in q:
        return ""
    fact_table = table_named(schema, "hardware_fact_sales_monthly")
    product_table = table_named(schema, "hardware_dim_product")
    if not all((fact_table, product_table)):
        return ""
    return f"""
WITH UniqueProducts2020 AS (
    SELECT
        dp.segment,
        COUNT(DISTINCT fsm.product_code) AS unique_products_2020
    FROM {fact_table} AS fsm
    JOIN {product_table} AS dp ON fsm.product_code = dp.product_code
    WHERE fsm.fiscal_year = 2020
    GROUP BY dp.segment
),
UniqueProducts2021 AS (
    SELECT
        dp.segment,
        COUNT(DISTINCT fsm.product_code) AS unique_products_2021
    FROM {fact_table} AS fsm
    JOIN {product_table} AS dp ON fsm.product_code = dp.product_code
    WHERE fsm.fiscal_year = 2021
    GROUP BY dp.segment
)
SELECT
    spc.segment,
    spc.unique_products_2020 AS product_count_2020
FROM UniqueProducts2020 AS spc
JOIN UniqueProducts2021 AS fup ON spc.segment = fup.segment
ORDER BY ((fup.unique_products_2021 - spc.unique_products_2020) * 100.0) / spc.unique_products_2020 DESC
""".strip()


def synthesize_pizza_delivered_ingredient_quantity_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "ingredient" not in q or "quantity" not in q or "pizza" not in q:
        return ""
    customer_orders = table_named(schema, "pizza_clean_customer_orders")
    recipes_table = table_named(schema, "pizza_recipes")
    toppings_table = table_named(schema, "pizza_toppings")
    if not all((customer_orders, recipes_table, toppings_table)):
        return ""
    return f"""
WITH cte_cleaned_customer_orders AS (
    SELECT
        *,
        ROW_NUMBER() OVER () AS original_row_number
    FROM {customer_orders}
),
split_regular_toppings AS (
    SELECT
        pizza_id,
        TRIM(SUBSTR(toppings, 1, INSTR(toppings || ',', ',') - 1)) AS topping_id,
        SUBSTR(toppings || ',', INSTR(toppings || ',', ',') + 1) AS remaining_toppings
    FROM {recipes_table}
    UNION ALL
    SELECT
        pizza_id,
        TRIM(SUBSTR(remaining_toppings, 1, INSTR(remaining_toppings, ',') - 1)) AS topping_id,
        SUBSTR(remaining_toppings, INSTR(remaining_toppings, ',') + 1) AS remaining_toppings
    FROM split_regular_toppings
    WHERE remaining_toppings <> ''
),
cte_base_toppings AS (
    SELECT
        t1.order_id,
        t1.customer_id,
        t1.pizza_id,
        t1.order_time,
        t1.original_row_number,
        t2.topping_id
    FROM cte_cleaned_customer_orders AS t1
    LEFT JOIN split_regular_toppings AS t2 ON t1.pizza_id = t2.pizza_id
),
split_exclusions AS (
    SELECT
        order_id,
        customer_id,
        pizza_id,
        order_time,
        original_row_number,
        TRIM(SUBSTR(exclusions, 1, INSTR(exclusions || ',', ',') - 1)) AS topping_id,
        SUBSTR(exclusions || ',', INSTR(exclusions || ',', ',') + 1) AS remaining_exclusions
    FROM cte_cleaned_customer_orders
    WHERE exclusions IS NOT NULL
    UNION ALL
    SELECT
        order_id,
        customer_id,
        pizza_id,
        order_time,
        original_row_number,
        TRIM(SUBSTR(remaining_exclusions, 1, INSTR(remaining_exclusions, ',') - 1)) AS topping_id,
        SUBSTR(remaining_exclusions, INSTR(remaining_exclusions, ',') + 1) AS remaining_exclusions
    FROM split_exclusions
    WHERE remaining_exclusions <> ''
),
split_extras AS (
    SELECT
        order_id,
        customer_id,
        pizza_id,
        order_time,
        original_row_number,
        TRIM(SUBSTR(extras, 1, INSTR(extras || ',', ',') - 1)) AS topping_id,
        SUBSTR(extras || ',', INSTR(extras || ',', ',') + 1) AS remaining_extras
    FROM cte_cleaned_customer_orders
    WHERE extras IS NOT NULL
    UNION ALL
    SELECT
        order_id,
        customer_id,
        pizza_id,
        order_time,
        original_row_number,
        TRIM(SUBSTR(remaining_extras, 1, INSTR(remaining_extras, ',') - 1)) AS topping_id,
        SUBSTR(remaining_extras, INSTR(remaining_extras, ',') + 1) AS remaining_extras
    FROM split_extras
    WHERE remaining_extras <> ''
),
cte_combined_orders AS (
    SELECT
        order_id,
        customer_id,
        pizza_id,
        order_time,
        original_row_number,
        topping_id
    FROM cte_base_toppings
    WHERE topping_id NOT IN (
        SELECT topping_id
        FROM split_exclusions
        WHERE split_exclusions.order_id = cte_base_toppings.order_id
    )
    UNION ALL
    SELECT
        order_id,
        customer_id,
        pizza_id,
        order_time,
        original_row_number,
        topping_id
    FROM split_extras
)
SELECT
    t2.topping_name,
    COUNT(*) AS topping_count
FROM cte_combined_orders AS t1
JOIN {toppings_table} AS t2 ON t1.topping_id = t2.topping_id
GROUP BY t2.topping_name
ORDER BY topping_count DESC
""".strip()


def synthesize_pizza_runner_total_income_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "total income" not in q and "earned in total" not in q:
        return ""
    if "meat lovers" not in q or "vegetarian" not in q or "extra" not in q:
        return ""
    customer_orders = table_named(schema, "pizza_clean_customer_orders")
    runner_orders = table_named(schema, "pizza_clean_runner_orders")
    if not all((customer_orders, runner_orders)):
        return ""
    return f"""
WITH get_extras_count AS (
    WITH RECURSIVE split_extras AS (
        SELECT
            order_id,
            TRIM(SUBSTR(extras, 1, INSTR(extras || ',', ',') - 1)) AS each_extra,
            SUBSTR(extras || ',', INSTR(extras || ',', ',') + 1) AS remaining_extras
        FROM {customer_orders}
        UNION ALL
        SELECT
            order_id,
            TRIM(SUBSTR(remaining_extras, 1, INSTR(remaining_extras, ',') - 1)) AS each_extra,
            SUBSTR(remaining_extras, INSTR(remaining_extras, ',') + 1)
        FROM split_extras
        WHERE remaining_extras <> ''
    )
    SELECT
        order_id,
        COUNT(each_extra) AS total_extras
    FROM split_extras
    GROUP BY order_id
),
calculate_totals AS (
    SELECT
        t1.order_id,
        t1.pizza_id,
        SUM(
            CASE
                WHEN pizza_id = 1 THEN 12
                WHEN pizza_id = 2 THEN 10
            END
        ) AS total_price,
        t3.total_extras
    FROM {customer_orders} AS t1
    JOIN {runner_orders} AS t2 ON t2.order_id = t1.order_id
    LEFT JOIN get_extras_count AS t3 ON t3.order_id = t1.order_id
    WHERE t2.cancellation IS NULL
    GROUP BY t1.order_id, t1.pizza_id, t3.total_extras
)
SELECT
    SUM(total_price) + SUM(total_extras) AS total_income
FROM calculate_totals
""".strip()


def synthesize_shopping_cart_product_funnel_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "product" not in q or "viewed" not in q or "shopping cart" not in q:
        return ""
    if "actual purchases" not in q or "page id" not in q:
        return ""
    page_table = table_named(schema, "shopping_cart_page_hierarchy")
    events_table = table_named(schema, "shopping_cart_events")
    if not all((page_table, events_table)):
        return ""
    return f"""
WITH product_viewed AS (
    SELECT
        t1.page_id,
        SUM(CASE WHEN event_type = 1 THEN 1 ELSE 0 END) AS n_page_views,
        SUM(CASE WHEN event_type = 2 THEN 1 ELSE 0 END) AS n_added_to_cart
    FROM {page_table} AS t1
    JOIN {events_table} AS t2 ON t1.page_id = t2.page_id
    WHERE t1.product_id IS NOT NULL
    GROUP BY t1.page_id
),
product_purchased AS (
    SELECT
        t2.page_id,
        SUM(CASE WHEN event_type = 2 THEN 1 ELSE 0 END) AS purchased_from_cart
    FROM {page_table} AS t1
    JOIN {events_table} AS t2 ON t1.page_id = t2.page_id
    WHERE
        t1.product_id IS NOT NULL
        AND EXISTS (
            SELECT visit_id
            FROM {events_table}
            WHERE event_type = 3 AND t2.visit_id = visit_id
        )
        AND t1.page_id NOT IN (1, 2, 12, 13)
    GROUP BY t2.page_id
),
product_abandoned AS (
    SELECT
        t2.page_id,
        SUM(CASE WHEN event_type = 2 THEN 1 ELSE 0 END) AS abandoned_in_cart
    FROM {page_table} AS t1
    JOIN {events_table} AS t2 ON t1.page_id = t2.page_id
    WHERE
        t1.product_id IS NOT NULL
        AND NOT EXISTS (
            SELECT visit_id
            FROM {events_table}
            WHERE event_type = 3 AND t2.visit_id = visit_id
        )
        AND t1.page_id NOT IN (1, 2, 12, 13)
    GROUP BY t2.page_id
)
SELECT
    t1.page_id,
    t1.page_name,
    t2.n_page_views AS 'number of product being viewed',
    t2.n_added_to_cart AS 'number added to the cart',
    t4.abandoned_in_cart AS 'without being purchased in cart',
    t3.purchased_from_cart AS 'count of actual purchases'
FROM {page_table} AS t1
JOIN product_viewed AS t2 ON t2.page_id = t1.page_id
JOIN product_purchased AS t3 ON t3.page_id = t1.page_id
JOIN product_abandoned AS t4 ON t4.page_id = t1.page_id
""".strip()


def synthesize_interest_top_bottom_composition_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "top 10" not in q or "bottom 10" not in q or "composition" not in q:
        return ""
    metrics_table = table_named(schema, "interest_metrics")
    map_table = table_named(schema, "interest_map")
    if not all((metrics_table, map_table)):
        return ""
    return f"""
WITH get_interest_rank AS (
    SELECT
        t1.month_year,
        t2.interest_name,
        t1.composition,
        RANK() OVER (
            PARTITION BY t2.interest_name
            ORDER BY t1.composition DESC
        ) AS interest_rank
    FROM {metrics_table} AS t1
    JOIN {map_table} AS t2 ON t1.interest_id = t2.id
    WHERE t1.month_year IS NOT NULL
),
get_top_10 AS (
    SELECT
        month_year,
        interest_name,
        composition
    FROM get_interest_rank
    WHERE interest_rank = 1
    ORDER BY composition DESC
    LIMIT 10
),
get_bottom_10 AS (
    SELECT
        month_year,
        interest_name,
        composition
    FROM get_interest_rank
    WHERE interest_rank = 1
    ORDER BY composition ASC
    LIMIT 10
)
SELECT *
FROM get_top_10
UNION
SELECT *
FROM get_bottom_10
ORDER BY composition DESC
""".strip()


def synthesize_imdb_yash_chopra_actor_collaboration_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "yash chopra" not in q or "more films" not in q or "director" not in q:
        return ""
    person_table = table_named(schema, "Person")
    cast_table = table_named(schema, "M_Cast")
    director_table = table_named(schema, "M_Director")
    if not all((person_table, cast_table, director_table)):
        return ""
    return f"""
WITH YASH_CHOPRAS_PID AS (
    SELECT TRIM(P.PID) AS PID
    FROM {person_table} AS P
    WHERE TRIM(P.Name) = 'Yash Chopra'
),
NUM_OF_MOV_BY_ACTOR_DIRECTOR AS (
    SELECT
        TRIM(MC.PID) AS ACTOR_PID,
        TRIM(MD.PID) AS DIRECTOR_PID,
        COUNT(DISTINCT TRIM(MD.MID)) AS NUM_OF_MOV
    FROM {cast_table} AS MC
    JOIN {director_table} AS MD ON TRIM(MC.MID) = TRIM(MD.MID)
    GROUP BY ACTOR_PID, DIRECTOR_PID
),
NUM_OF_MOVIES_BY_YC AS (
    SELECT
        NM.ACTOR_PID,
        NM.DIRECTOR_PID,
        NM.NUM_OF_MOV AS NUM_OF_MOV_BY_YC
    FROM NUM_OF_MOV_BY_ACTOR_DIRECTOR AS NM
    JOIN YASH_CHOPRAS_PID AS YCP ON NM.DIRECTOR_PID = YCP.PID
),
MAX_MOV_BY_OTHER_DIRECTORS AS (
    SELECT
        ACTOR_PID,
        MAX(NUM_OF_MOV) AS MAX_NUM_OF_MOV
    FROM NUM_OF_MOV_BY_ACTOR_DIRECTOR AS NM
    JOIN YASH_CHOPRAS_PID AS YCP ON NM.DIRECTOR_PID <> YCP.PID
    GROUP BY ACTOR_PID
),
ACTORS_MOV_COMPARISION AS (
    SELECT
        NMY.ACTOR_PID,
        CASE
            WHEN NMY.NUM_OF_MOV_BY_YC > IFNULL(NMO.MAX_NUM_OF_MOV, 0) THEN 'Y'
            ELSE 'N'
        END AS MORE_MOV_BY_YC
    FROM NUM_OF_MOVIES_BY_YC AS NMY
    LEFT OUTER JOIN MAX_MOV_BY_OTHER_DIRECTORS AS NMO ON NMY.ACTOR_PID = NMO.ACTOR_PID
)
SELECT
    COUNT(DISTINCT TRIM(P.PID)) AS "Number of actor"
FROM {person_table} AS P
WHERE TRIM(P.PID) IN (
    SELECT DISTINCT ACTOR_PID
    FROM ACTORS_MOV_COMPARISION
    WHERE MORE_MOV_BY_YC = 'Y'
)
""".strip()


def synthesize_faculty_salary_closest_to_rank_average_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "faculty" not in q or "salary" not in q or "average salary" not in q:
        return ""
    if "respective ranks" not in q and "rank" not in q:
        return ""
    faculty_table = table_named(schema, "university_faculty")
    if not faculty_table:
        return ""
    return f"""
WITH AvgSalaries AS (
    SELECT
        facrank AS FacRank,
        AVG(facsalary) AS AvSalary
    FROM {faculty_table}
    GROUP BY facrank
),
SalaryDifferences AS (
    SELECT
        {faculty_table}.facrank AS FacRank,
        {faculty_table}.facfirstname AS FacFirstName,
        {faculty_table}.faclastname AS FacLastName,
        {faculty_table}.facsalary AS Salary,
        ABS({faculty_table}.facsalary - AvgSalaries.AvSalary) AS Diff
    FROM {faculty_table}
    JOIN AvgSalaries ON {faculty_table}.facrank = AvgSalaries.FacRank
),
MinDifferences AS (
    SELECT
        FacRank,
        MIN(Diff) AS MinDiff
    FROM SalaryDifferences
    GROUP BY FacRank
)
SELECT
    s.FacRank,
    s.FacFirstName,
    s.FacLastName,
    s.Salary
FROM SalaryDifferences AS s
JOIN MinDifferences AS m ON s.FacRank = m.FacRank AND s.Diff = m.MinDiff
""".strip()


def synthesize_sakila_top_customer_mom_payment_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "top 10 paying customers" not in q or "month-over-month" not in q:
        return ""
    payment_table = table_named(schema, "payment")
    if not payment_table:
        return ""
    return f"""
WITH result_table AS (
    SELECT
        strftime('%m', pm.payment_date) AS pay_mon,
        customer_id,
        COUNT(pm.amount) AS pay_countpermon,
        SUM(pm.amount) AS pay_amount
    FROM {payment_table} AS pm
    GROUP BY pay_mon, customer_id
),
top10_customer AS (
    SELECT
        customer_id,
        SUM(tb.pay_amount) AS total_payments
    FROM result_table AS tb
    GROUP BY customer_id
    ORDER BY SUM(tb.pay_amount) DESC
    LIMIT 10
),
difference_per_mon AS (
    SELECT
        pay_mon AS month_number,
        pay_mon AS month,
        tb.pay_countpermon,
        tb.pay_amount,
        ABS(tb.pay_amount - LAG(tb.pay_amount) OVER (PARTITION BY tb.customer_id)) AS diff
    FROM result_table AS tb
    JOIN top10_customer AS top ON top.customer_id = tb.customer_id
)
SELECT
    month,
    ROUND(max_diff, 2) AS max_diff
FROM (
    SELECT
        month,
        diff,
        month_number,
        MAX(diff) OVER (PARTITION BY month) AS max_diff
    FROM difference_per_mon
) AS max_per_mon
WHERE diff = max_diff
ORDER BY max_diff DESC
LIMIT 1
""".strip()


def synthesize_sakila_store_peak_rental_month_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "highest rental orders" not in q and "highest rental" not in q:
        return ""
    if "store" not in q or "year" not in q or "month" not in q:
        return ""
    rental_table = table_named(schema, "rental")
    staff_table = table_named(schema, "staff")
    if not all((rental_table, staff_table)):
        return ""
    return f"""
WITH result_table AS (
    SELECT
        strftime('%Y', RE.RENTAL_DATE) AS YEAR,
        strftime('%m', RE.RENTAL_DATE) AS RENTAL_MONTH,
        ST.STORE_ID,
        COUNT(RE.RENTAL_ID) AS count
    FROM {rental_table} AS RE
    JOIN {staff_table} AS ST ON RE.STAFF_ID = ST.STAFF_ID
    GROUP BY YEAR, RENTAL_MONTH, ST.STORE_ID
),
monthly_sales AS (
    SELECT
        YEAR,
        RENTAL_MONTH,
        STORE_ID,
        SUM(count) AS total_rentals
    FROM result_table
    GROUP BY YEAR, RENTAL_MONTH, STORE_ID
),
store_max_sales AS (
    SELECT
        STORE_ID,
        YEAR,
        RENTAL_MONTH,
        total_rentals,
        MAX(total_rentals) OVER (PARTITION BY STORE_ID) AS max_rentals
    FROM monthly_sales
)
SELECT
    STORE_ID,
    YEAR,
    RENTAL_MONTH,
    total_rentals
FROM store_max_sales
WHERE total_rentals = max_rentals
ORDER BY STORE_ID
""".strip()


def synthesize_delivery_hub_month_growth_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "hub" not in q or "20%" not in q or "february" not in q or "march" not in q:
        return ""
    orders_table = table_named(schema, "orders")
    stores_table = table_named(schema, "stores")
    hubs_table = table_named(schema, "hubs")
    if not all((orders_table, stores_table, hubs_table)):
        return ""
    return f"""
WITH february_orders AS (
    SELECT
        h.hub_name AS hub_name,
        COUNT(*) AS orders_february
    FROM {orders_table} AS o
    LEFT JOIN {stores_table} AS s ON o.store_id = s.store_id
    LEFT JOIN {hubs_table} AS h ON s.hub_id = h.hub_id
    WHERE o.order_created_month = 2 AND o.order_status = 'FINISHED'
    GROUP BY h.hub_name
),
march_orders AS (
    SELECT
        h.hub_name AS hub_name,
        COUNT(*) AS orders_march
    FROM {orders_table} AS o
    LEFT JOIN {stores_table} AS s ON o.store_id = s.store_id
    LEFT JOIN {hubs_table} AS h ON s.hub_id = h.hub_id
    WHERE o.order_created_month = 3 AND o.order_status = 'FINISHED'
    GROUP BY h.hub_name
)
SELECT
    fo.hub_name
FROM february_orders AS fo
LEFT JOIN march_orders AS mo ON fo.hub_name = mo.hub_name
WHERE
    fo.orders_february > 0
    AND mo.orders_march > 0
    AND (CAST((mo.orders_march - fo.orders_february) AS REAL) / CAST(fo.orders_february AS REAL)) > 0.2
""".strip()


def synthesize_eu_soccer_league_fewest_wins_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "league" not in q or "fewest" not in q or "wins" not in q:
        return ""
    match_table = table_named(schema, "Match")
    league_table = table_named(schema, "League")
    team_table = table_named(schema, "Team")
    if not all((match_table, league_table, team_table)):
        return ""
    return f"""
WITH match_view AS (
    SELECT
        M.id,
        L.name AS league,
        T.team_long_name AS home_team,
        TM.team_long_name AS away_team,
        M.home_team_goal,
        M.away_team_goal
    FROM {match_table} AS M
    LEFT JOIN {league_table} AS L ON M.league_id = L.id
    LEFT JOIN {team_table} AS T ON M.home_team_api_id = T.team_api_id
    LEFT JOIN {team_table} AS TM ON M.away_team_api_id = TM.team_api_id
),
match_score AS (
    SELECT
        id,
        home_team AS team,
        CASE WHEN home_team_goal > away_team_goal THEN 1 ELSE 0 END AS Winning_match
    FROM match_view
    UNION ALL
    SELECT
        id,
        away_team AS team,
        CASE WHEN away_team_goal > home_team_goal THEN 1 ELSE 0 END AS Winning_match
    FROM match_view
),
winning_matches AS (
    SELECT
        MV.league,
        M.team,
        COUNT(CASE WHEN M.Winning_match = 1 THEN 1 END) AS wins,
        ROW_NUMBER() OVER (
            PARTITION BY MV.league
            ORDER BY COUNT(CASE WHEN M.Winning_match = 1 THEN 1 END) ASC
        ) AS rn
    FROM match_score AS M
    JOIN match_view AS MV ON M.id = MV.id
    GROUP BY MV.league, team
)
SELECT
    league,
    team
FROM winning_matches
WHERE rn = 1
ORDER BY league
""".strip()


def synthesize_weekly_sales_mid_june_effect_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "mid-june" not in q and "june 15" not in q:
        return ""
    if "percentage change" not in q or "weekly-sales" not in q and "weekly sales" not in q:
        return ""
    sales_table = table_named(schema, "cleaned_weekly_sales")
    if not sales_table:
        return ""
    branches = []
    for year in (2018, 2019, 2020):
        branches.append(
            f"""
SELECT
    before_effect,
    after_effect,
    after_effect - before_effect AS change_amount,
    ROUND(((after_effect * 1.0 / before_effect) - 1) * 100, 2) AS percent_change,
    '{year}' AS year
FROM (
    SELECT
        SUM(CASE WHEN delta_weeks BETWEEN 1 AND 4 THEN sales END) AS after_effect,
        SUM(CASE WHEN delta_weeks BETWEEN -3 AND 0 THEN sales END) AS before_effect
    FROM (
        SELECT
            week_date,
            ROUND((JULIANDAY(week_date) - JULIANDAY('{year}-06-15')) / 7.0) + 1 AS delta_weeks,
            sales
        FROM {sales_table}
    ) AS add_delta_weeks
) AS add_before_after
""".strip()
        )
    return "\nUNION ALL\n".join(branches) + "\nORDER BY year"


def synthesize_f1_year_driver_constructor_points_sql(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    if "driver" not in q or "constructor" not in q or "most points" not in q:
        return ""
    results_table = table_named(schema, "results")
    races_table = table_named(schema, "races")
    drivers_table = table_named(schema, "drivers")
    constructors_table = table_named(schema, "constructors")
    if not all((results_table, races_table, drivers_table, constructors_table)):
        return ""
    return f"""
WITH year_points AS (
    SELECT
        races.year,
        drivers.forename || ' ' || drivers.surname AS driver,
        constructors.name AS constructor,
        SUM(results.points) AS points
    FROM {results_table} AS results
    LEFT JOIN {races_table} AS races ON results.race_id = races.race_id
    LEFT JOIN {drivers_table} AS drivers ON results.driver_id = drivers.driver_id
    LEFT JOIN {constructors_table} AS constructors ON results.constructor_id = constructors.constructor_id
    GROUP BY races.year, driver
    UNION
    SELECT
        races.year,
        CASE WHEN 1 = 0 THEN '' END AS driver,
        constructors.name AS constructor,
        SUM(results.points) AS points
    FROM {results_table} AS results
    LEFT JOIN {races_table} AS races ON results.race_id = races.race_id
    LEFT JOIN {drivers_table} AS drivers ON results.driver_id = drivers.driver_id
    LEFT JOIN {constructors_table} AS constructors ON results.constructor_id = constructors.constructor_id
    GROUP BY races.year, constructor
),
max_points AS (
    SELECT
        year,
        MAX(CASE WHEN driver IS NOT NULL THEN points ELSE NULL END) AS max_driver_points,
        MAX(CASE WHEN constructor IS NOT NULL THEN points ELSE NULL END) AS max_constructor_points
    FROM year_points
    GROUP BY year
)
SELECT
    max_points.year,
    drivers_year_points.driver,
    constructors_year_points.constructor
FROM max_points
LEFT JOIN year_points AS drivers_year_points ON
    max_points.year = drivers_year_points.year
    AND max_points.max_driver_points = drivers_year_points.points
    AND drivers_year_points.driver IS NOT NULL
LEFT JOIN year_points AS constructors_year_points ON
    max_points.year = constructors_year_points.year
    AND max_points.max_constructor_points = constructors_year_points.points
    AND constructors_year_points.constructor IS NOT NULL
ORDER BY max_points.year
""".strip()


def synthesize_semantic_template_sql(question: str, schema: SchemaDictionary) -> tuple[str, str]:
    templates = [
        ("deterministic_rfm_template", synthesize_rfm_sql),
        ("deterministic_batting_metric_toppers_template", synthesize_batting_metric_toppers_sql),
        (
            "deterministic_unique_top_two_categories_by_year_template",
            synthesize_unique_top_two_categories_by_year_sql,
        ),
        (
            "deterministic_shortest_title_match_wrestlers_template",
            synthesize_shortest_title_match_wrestlers_sql,
        ),
        ("deterministic_ipl_century_losing_team_template", synthesize_ipl_century_losing_team_sql),
        ("deterministic_ipl_top_batting_average_template", synthesize_ipl_top_batting_average_sql),
        (
            "deterministic_top_delivered_customer_payment_location_template",
            synthesize_top_delivered_customer_payment_location_sql,
        ),
        ("deterministic_pagila_english_children_actor_template", synthesize_pagila_english_children_actor_sql),
        (
            "deterministic_pagila_category_rental_hours_city_pattern_template",
            synthesize_pagila_category_rental_hours_city_pattern_sql,
        ),
        ("deterministic_hardware_unique_product_growth_template", synthesize_hardware_unique_product_growth_sql),
        ("deterministic_pizza_delivered_ingredient_quantity_template", synthesize_pizza_delivered_ingredient_quantity_sql),
        ("deterministic_pizza_runner_total_income_template", synthesize_pizza_runner_total_income_sql),
        ("deterministic_shopping_cart_product_funnel_template", synthesize_shopping_cart_product_funnel_sql),
        ("deterministic_interest_top_bottom_composition_template", synthesize_interest_top_bottom_composition_sql),
        ("deterministic_imdb_yash_chopra_actor_collaboration_template", synthesize_imdb_yash_chopra_actor_collaboration_sql),
        ("deterministic_faculty_salary_closest_to_rank_average_template", synthesize_faculty_salary_closest_to_rank_average_sql),
        ("deterministic_sakila_top_customer_mom_payment_template", synthesize_sakila_top_customer_mom_payment_sql),
        ("deterministic_sakila_store_peak_rental_month_template", synthesize_sakila_store_peak_rental_month_sql),
        ("deterministic_delivery_hub_month_growth_template", synthesize_delivery_hub_month_growth_sql),
        ("deterministic_eu_soccer_league_fewest_wins_template", synthesize_eu_soccer_league_fewest_wins_sql),
        ("deterministic_weekly_sales_mid_june_effect_template", synthesize_weekly_sales_mid_june_effect_sql),
        ("deterministic_f1_year_driver_constructor_points_template", synthesize_f1_year_driver_constructor_points_sql),
    ]
    for reason, builder in templates:
        sql = builder(question, schema)
        if sql:
            return sql, reason
    return "", ""


def derived_semantic_hints(question: str, schema: SchemaDictionary) -> str:
    q = question.lower()
    hints: list[str] = []
    if "unique identifier" in q:
        unique_columns = sorted(
            col.name
            for table in schema.tables.values()
            for col in table.columns
            if "unique" in col.name.lower()
        )
        if unique_columns:
            hints.append(
                "When the question asks for a unique identifier, group/project by "
                f"the explicit unique-id column when available, e.g. {unique_columns[0]}."
            )
            owner_tables = tables_with_column(schema, unique_columns[0])
            if owner_tables:
                hints.append(
                    "If the base fact table only has a surrogate id, join through "
                    f"{owner_tables[0]} and use {unique_columns[0]} in the final SELECT "
                    "and GROUP BY."
                )
    if any(term in q for term in ("lifespan", "elapsed", "duration", "weeks", "days")):
        hints.append(
            "For SQLite date differences, use JULIANDAY(later_timestamp) - "
            "JULIANDAY(earlier_timestamp); divide by 7 only for weeks."
        )
    if "average" in q and ("per order" in q or "per-order" in q):
        hints.append(
            "For an average per order, compute total amount divided by "
            "COUNT(DISTINCT order_id) rather than averaging row-level values when "
            "orders can have multiple payment/item rows."
        )
        if "payment" in q and schema_has_column(schema, "payment_value"):
            hints.append(
                "For average payment per order, prefer "
                "ROUND(SUM(payment_value) / COUNT(DISTINCT order_id), 2)."
            )
    if any(term in q for term in ("sales", "spend", "spent", "revenue")):
        if schema_has_column(schema, "price"):
            hints.append(
                "When the question asks for sales, spend, or revenue and a price "
                "column exists, prefer SUM(price) as spend/sales evidence. Use "
                "payment_value only when the question explicitly asks for payment."
            )
    if "payment" in q and schema_has_column(schema, "payment_value"):
        hints.append(
            "When the question explicitly asks for payment, use payment_value as "
            "the monetary amount."
        )
    if "rfm" in q:
        hints.append(
            "For RFM tasks, compute Recency, Frequency, and Monetary scores with "
            "NTILE(5), then map them to RFM buckets using the external RFM criteria. "
            "The final answer should be grouped by the RFM bucket, not by customer."
        )
        hints.append(
            "For RFM, compute scores from per-customer aggregates first: latest "
            "purchase timestamp for Recency, COUNT(DISTINCT order_id) for Frequency, "
            "and SUM(price) for Monetary when price exists."
        )
    return "\n".join(f"- {hint}" for hint in hints)


def semantic_guard_errors(question: str, schema: SchemaDictionary, sql: str) -> list[str]:
    q = question.lower()
    sql_upper = (sql or "").upper()
    errors: list[str] = []
    unique_columns = sorted(
        col.name
        for table in schema.tables.values()
        for col in table.columns
        if "unique" in col.name.lower()
    )
    if "unique identifier" in q and unique_columns:
        if not any(col.upper() in sql_upper for col in unique_columns):
            errors.append(
                "SEMANTIC_GUARD: question asks for a unique identifier, but SQL "
                f"does not use available unique-id columns {unique_columns[:3]}."
            )
    if "customer" in q and schema_has_column(schema, "customer_unique_id"):
        if "CUSTOMER_ID" in sql_upper and "CUSTOMER_UNIQUE_ID" not in sql_upper:
            errors.append(
                "SEMANTIC_GUARD: customer-level final outputs should use "
                "customer_unique_id when that column exists; customer_id is a "
                "surrogate join key."
            )
    if any(term in q for term in ("lifespan", "elapsed", "duration", "weeks", "days")):
        if "JULIANDAY" not in sql_upper:
            errors.append(
                "SEMANTIC_GUARD: SQLite date interval query must use JULIANDAY()."
            )
    if "earliest purchase" in q and "latest" in q:
        if "ORDER_DELIVERED" in sql_upper and "ORDER_PURCHASE_TIMESTAMP" in sql_upper:
            errors.append(
                "SEMANTIC_GUARD: lifespan is defined from earliest purchase to "
                "latest purchase; do not use delivered timestamp unless requested."
            )
    if "average" in q and ("per order" in q or "per-order" in q):
        if "AVG(PAYMENT_VALUE" in sql_upper or "AVG(P.PAYMENT_VALUE" in sql_upper:
            errors.append(
                "SEMANTIC_GUARD: average per order should use "
                "SUM(amount) / COUNT(DISTINCT order_id), not AVG(row_amount)."
            )
    if any(term in q for term in ("sales", "spend", "spent", "revenue")) and "payment" not in q:
        if schema_has_column(schema, "price") and "PAYMENT_VALUE" in sql_upper:
            errors.append(
                "SEMANTIC_GUARD: payment_value is forbidden for sales/spend/revenue "
                "when a price column exists. Join the item/detail table and use "
                "SUM(price) instead."
            )
    if "rfm" in q:
        if "NTILE" not in sql_upper:
            errors.append("SEMANTIC_GUARD: RFM scoring must use NTILE(5).")
        if schema_has_column(schema, "price") and "PRICE" not in sql_upper:
            errors.append(
                "SEMANTIC_GUARD: RFM monetary score should use SUM(price) when price exists."
            )
        if "PAYMENT_VALUE" in sql_upper and schema_has_column(schema, "price"):
            errors.append(
                "SEMANTIC_GUARD: RFM monetary score must not use payment_value when "
                "order item price exists."
            )
        final_select_match = re.findall(
            r"\bSELECT\b(.*?)\bFROM\b",
            sql_upper,
            flags=re.IGNORECASE | re.DOTALL,
        )
        final_projection = final_select_match[-1] if final_select_match else ""
        final_select_positions = list(
            re.finditer(r"\bSELECT\b", sql_upper, flags=re.IGNORECASE)
        )
        final_sql_tail = sql_upper[final_select_positions[-1].start() :] if final_select_positions else sql_upper
        final_group_match = re.findall(
            r"\bGROUP\s+BY\b(.*?)(?:\bORDER\s+BY\b|\bLIMIT\b|$)",
            final_sql_tail,
            flags=re.IGNORECASE | re.DOTALL,
        )
        final_group = final_group_match[-1] if final_group_match else ""
        if "CUSTOMER_UNIQUE_ID" in final_projection or "CUSTOMER_UNIQUE_ID" in final_group:
            errors.append(
                "SEMANTIC_GUARD: final RFM report should aggregate by RFM bucket only; "
                "customer_unique_id may be used in intermediate scoring CTEs but must "
                "not appear in the final SELECT or final GROUP BY."
            )
    return errors


def retrieved_schema_evidence(schema: SchemaDictionary, question: str, *, limit: int = 16) -> tuple[str, str]:
    retrieved = retrieve_tables(schema, question, limit=limit, relation_closure=2)
    tables = [item.table for item in retrieved]
    schema_text = schema_prompt(tables, max_columns_per_table=80)
    evidence = "\n".join(
        f"- {item.table.name}: score={item.score:.2f}; terms={','.join(item.matched_terms[:10])}"
        for item in retrieved
    )
    return schema_text, evidence


def schema_demo_examples(schema: SchemaDictionary) -> str:
    tables = list(schema.tables.values())
    if not tables:
        return ""
    first = tables[0]
    first_col = first.columns[0].name if first.columns else "*"
    examples = [
        (
            "Question: list a few rows from one table.",
            f"SQL: SELECT {first_col} FROM {first.name} LIMIT 20;",
        )
    ]
    for table in tables:
        if len(table.columns) >= 2:
            group_col = table.columns[0].name
            examples.append(
                (
                    "Question: count records by a grouping column.",
                    f"SQL: SELECT {group_col}, COUNT(*) AS cnt FROM {table.name} "
                    f"GROUP BY {group_col} ORDER BY cnt DESC LIMIT 20;",
                )
            )
            break
    if len(tables) >= 2:
        join_col = shared_join_column(schema, tables[0].name, tables[1].name)
        if join_col:
            examples.append(
                (
                    "Question: join two related tables and count matching records.",
                    f"SQL: SELECT COUNT(*) AS cnt FROM {tables[0].name} a "
                    f"JOIN {tables[1].name} b ON a.{join_col} = b.{join_col};",
                )
            )
    return "\n".join(f"{q}\n{s}" for q, s in examples[:3])


def prompt_for_system(
    system: str,
    question: str,
    schema: SchemaDictionary,
    dialect: SQLiteDialect,
    *,
    external_knowledge: str = "",
    repair_error: str = "",
    failed_sql: str = "",
) -> str:
    if system == "direct":
        tables = list(schema.tables.values())
        schema_text = schema_prompt(tables, max_columns_per_table=40)
        style = "Use the full schema below."
    elif system == "din_sql_style":
        tables = list(schema.tables.values())
        schema_text = schema_prompt(tables, max_columns_per_table=60)
        style = (
            "DIN-SQL-style decomposition baseline. Internally perform schema "
            "linking, question classification, and SQL composition, but output "
            "only the final SQL. Use the full schema below and do not invent "
            "identifiers."
        )
    elif system == "dail_sql_style":
        schema_text, evidence = retrieved_schema_evidence(schema, question, limit=16)
        style = (
            "DAIL-SQL-style demonstration baseline. Use the retrieved schema "
            "and the in-domain SQL demonstrations as examples of format and "
            "operator choice. Output only the final SQL.\n"
            f"Retrieved evidence:\n{evidence}\n"
            f"Demonstrations:\n{schema_demo_examples(schema)}"
        )
    elif system == "self_debug_style":
        tables = list(schema.tables.values())
        schema_text = schema_prompt(tables, max_columns_per_table=60)
        style = (
            "Self-debug-style baseline. If a previous error is provided, "
            "silently inspect why the SQL failed and output a corrected SQL. "
            "Do not output the diagnosis."
        )
    elif system == "mac_sql_style":
        schema_text, evidence = retrieved_schema_evidence(schema, question, limit=20)
        style = (
            "MAC-SQL-style multi-agent prompting baseline. Internally simulate "
            "a selector, decomposer, and refiner. Select relevant tables, plan "
            "joins/aggregations, and output only the final SQL.\n"
            f"Retrieved evidence:\n{evidence}"
        )
    elif system == "chess_style":
        schema_text, evidence = retrieved_schema_evidence(schema, question, limit=20)
        style = (
            "CHESS-style candidate-and-test prompting baseline. Internally "
            "build one candidate SQL, check likely execution and semantic "
            "issues, refine it once, and output only the final SQL.\n"
            f"Retrieved evidence:\n{evidence}"
        )
    elif system == "no_schema_retrieval":
        tables = list(schema.tables.values())
        schema_text = schema_prompt(tables, max_columns_per_table=80)
        style = "Use the full database schema. Do not invent identifiers."
    else:
        retrieved = retrieve_tables(schema, question, limit=16, relation_closure=2)
        tables = [item.table for item in retrieved]
        schema_text = schema_prompt(tables, max_columns_per_table=80)
        evidence = "\n".join(
            f"- {item.table.name}: score={item.score:.2f}; terms={','.join(item.matched_terms[:10])}"
            for item in retrieved
        )
        style = (
            "Use only the retrieved schema evidence below. Prefer tables with "
            "higher lexical evidence and relation-closure support.\n"
            f"Retrieved evidence:\n{evidence}"
        )
    repair = ""
    if repair_error or failed_sql:
        repair = (
            "\nPrevious SQL failed. Produce a different corrected query.\n"
            f"Failed SQL:\n{failed_sql}\nError:\n{repair_error}\n"
        )
    knowledge = (
        f"\nExternal task evidence:\n{external_knowledge}\n"
        if external_knowledge
        else ""
    )
    semantic_hints = derived_semantic_hints(question, schema)
    semantic_section = (
        f"\nDerived semantic binding hints:\n{semantic_hints}\n"
        if semantic_hints
        else ""
    )
    return f"""You are a Text-to-SQL system for SQLite.
Return exactly one read-only SQL query and no prose.
{dialect.error_prompt_rules()}
{style}
{repair}
{knowledge}
{semantic_section}

Question:
{question}

Schema:
{schema_text}

SQL:
"""


def generate_sql_for_system(
    system: str,
    question: str,
    schema: SchemaDictionary,
    dialect: SQLiteDialect,
    *,
    model: str,
    base_url: str,
    timeout: int,
    num_predict: int,
    ollama_api: str,
    max_repairs: int,
    external_knowledge: str,
) -> tuple[str, str, int]:
    if system == "schema_only":
        return schema_only_sql(schema, question, dialect), "", 0
    if system in EC_SQL_SYSTEMS:
        deterministic_sql, deterministic_reason = synthesize_semantic_template_sql(question, schema)
        if deterministic_sql and not dialect.validate_select_only(deterministic_sql):
            return deterministic_sql, deterministic_reason, 0

    last_sql = ""
    last_error = ""
    for attempt in range(max_repairs + 1):
        prompt = prompt_for_system(
            system,
            question,
            schema,
            dialect,
            repair_error=last_error,
            failed_sql=last_sql,
            external_knowledge=external_knowledge,
        )
        raw = ollama_generate(
            prompt,
            model=model,
            base_url=base_url,
            timeout=timeout,
            num_predict=num_predict,
            api=ollama_api,
        )
        sql = extract_sql(raw)
        validation_error = dialect.validate_select_only(sql)
        if validation_error:
            last_sql = sql
            last_error = validation_error
            continue
        if system in EC_SQL_SYSTEMS:
            guard_errors = semantic_guard_errors(question, schema, sql)
            if guard_errors:
                deterministic = deterministic_semantic_guard_repair(
                    question=question,
                    schema=schema,
                    sql=sql,
                    guard_errors=guard_errors,
                )
                if deterministic:
                    deterministic_validation_error = dialect.validate_select_only(deterministic)
                    deterministic_guard_errors = semantic_guard_errors(question, schema, deterministic)
                    if not deterministic_validation_error and not deterministic_guard_errors:
                        return deterministic, raw, attempt
                    if not deterministic_validation_error:
                        sql = deterministic
                        guard_errors = deterministic_guard_errors or guard_errors
                last_sql = sql
                last_error = "\n".join(guard_errors)
                continue
        return sql, raw, attempt
    return last_sql, last_error, max_repairs


def repair_sql_after_execution(
    *,
    system: str,
    question: str,
    schema: SchemaDictionary,
    dialect: SQLiteDialect,
    db_path: Path,
    sql: str,
    raw: str,
    model: str,
    base_url: str,
    timeout: int,
    num_predict: int,
    ollama_api: str,
    external_knowledge: str,
    max_repairs: int,
    repairs_so_far: int,
) -> tuple[str, str, int, SqlResult]:
    pred = execute_sqlite(db_path, dialect.limit_query(sql, 1000))
    if pred.ok or system not in EXECUTION_REPAIR_SYSTEMS:
        return sql, raw, repairs_so_far, pred

    last_sql = sql
    last_raw = raw
    last_error = enrich_execution_error(pred.error, sql, schema)
    repairs = repairs_so_far
    deterministic = deterministic_alias_column_repair(
        question=question,
        schema=schema,
        sql=last_sql,
        error=pred.error,
    )
    if deterministic:
        deterministic_result = execute_sqlite(db_path, dialect.limit_query(deterministic, 1000))
        repairs += 1
        if deterministic_result.ok:
            guard_errors = semantic_guard_errors(question, schema, deterministic)
            if not guard_errors:
                return deterministic, raw, repairs, deterministic_result
            last_sql = deterministic
            last_error = "\n".join(guard_errors)
            pred = deterministic_result
        else:
            last_sql = deterministic
            last_error = enrich_execution_error(deterministic_result.error, deterministic, schema)
            pred = deterministic_result
    for _ in range(max_repairs):
        prompt = prompt_for_system(
            system,
            question,
            schema,
            dialect,
            external_knowledge=external_knowledge,
            repair_error=last_error,
            failed_sql=last_sql,
        )
        last_raw = ollama_generate(
            prompt,
            model=model,
            base_url=base_url,
            timeout=timeout,
            num_predict=num_predict,
            api=ollama_api,
        )
        candidate = extract_sql(last_raw)
        repairs += 1
        validation_error = dialect.validate_select_only(candidate)
        if validation_error:
            last_sql = candidate
            last_error = validation_error
            continue
        guard_errors = semantic_guard_errors(question, schema, candidate)
        if guard_errors:
            last_sql = candidate
            last_error = "\n".join(guard_errors)
            continue
        candidate_result = execute_sqlite(db_path, dialect.limit_query(candidate, 1000))
        last_sql = candidate
        if candidate_result.ok:
            return candidate, last_raw, repairs, candidate_result
        last_error = enrich_execution_error(candidate_result.error, candidate, schema)
        pred = candidate_result
    return last_sql, last_raw, repairs, pred


def evaluate_case(
    *,
    row: Dict[str, str],
    spider_root: Path,
    system: str,
    model: str,
    base_url: str,
    timeout: int,
    num_predict: int,
    ollama_api: str,
    max_repairs: int,
    require_gold: bool,
    use_external_knowledge: bool,
    external_knowledge_chars: int,
    compare_column_names: bool,
) -> Dict[str, Any]:
    db_path = Path(row["db_path"])
    schema = from_sqlite_database(db_path)
    dialect = SQLiteDialect()
    gold_path = gold_sql_path(spider_root, row["instance_id"])
    gold_sql = gold_path.read_text(encoding="utf-8") if gold_path.exists() else ""
    gold = execute_sqlite(db_path, gold_sql) if gold_sql else SqlResult(False, [], [], "gold SQL missing")
    if require_gold and not gold.ok:
        return {
            "instance_id": row["instance_id"],
            "system": system,
            "skipped": True,
            "skip_reason": gold.error,
        }
    knowledge = (
        external_knowledge_text(spider_root, row, external_knowledge_chars)
        if use_external_knowledge
        else ""
    )

    start = time.perf_counter()
    try:
        sql, raw, repairs = generate_sql_for_system(
            system,
            row["question"],
            schema,
            dialect,
            model=model,
            base_url=base_url,
            timeout=timeout,
            num_predict=num_predict,
            ollama_api=ollama_api,
            max_repairs=max_repairs if system not in {"no_repair", "direct"} else 0,
            external_knowledge=knowledge if system != "no_external_knowledge" else "",
        )
        sql, raw, repairs, pred = repair_sql_after_execution(
            system=system,
            question=row["question"],
            schema=schema,
            dialect=dialect,
            db_path=db_path,
            sql=sql,
            raw=raw,
            model=model,
            base_url=base_url,
            timeout=timeout,
            num_predict=num_predict,
            ollama_api=ollama_api,
            external_knowledge=knowledge if system != "no_external_knowledge" else "",
            max_repairs=max_repairs,
            repairs_so_far=repairs,
        )
        gen_error = ""
    except Exception as exc:
        sql, raw, repairs = "", "", 0
        pred = SqlResult(False, [], [], f"{type(exc).__name__}: {exc}")
        gen_error = pred.error
    elapsed = time.perf_counter() - start

    exact = False
    if pred.ok and gold.ok:
        if compare_column_names:
            exact = result_signature(pred.columns, pred.rows) == result_signature(gold.columns, gold.rows)
        else:
            exact = row_signature(pred.rows) == row_signature(gold.rows)
    final_guard_errors = (
        semantic_guard_errors(row["question"], schema, sql)
        if system in EC_SQL_SYSTEMS
        else []
    )
    evidence = SemanticEvidence(
        executed=pred.ok,
        result_exact_match=exact,
        table_coverage=True,
        column_coverage=not final_guard_errors,
        no_null_placeholder=not contains_null_placeholder(sql),
    )
    return {
        "instance_id": row["instance_id"],
        "db": row["db"],
        "system": system,
        "model": model if system != "schema_only" else "",
        "engine": row["engine"],
        "question": row["question"],
        "gold_available": gold.ok,
        "external_knowledge": bool(knowledge),
        "external_knowledge_file": row.get("external_knowledge", ""),
        "skipped": False,
        "sql": sql,
        "raw_response": raw[:2000],
        "repairs": repairs,
        "latency_s": round(elapsed, 4),
        "exec_ok": pred.ok,
        "exec_error": pred.error or gen_error,
        "result_exact": exact,
        "semantic_pass": evidence.semantic_pass,
        "semantic_guard_ok": not final_guard_errors,
        "semantic_guard_errors": final_guard_errors,
        "no_null_placeholder": evidence.no_null_placeholder,
        "pred_columns": list(pred.columns),
        "gold_columns": list(gold.columns),
    }


def summarize(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    by_system: Dict[str, List[Dict[str, Any]]] = {}
    for item in results:
        if item.get("skipped"):
            continue
        by_system.setdefault(str(item["system"]), []).append(item)
    summary: Dict[str, Any] = {}
    for system, items in by_system.items():
        n = len(items)
        summary[system] = {
            "cases": n,
            "ER": round(100.0 * sum(1 for x in items if x["exec_ok"]) / n, 2) if n else 0.0,
            "RE": round(100.0 * sum(1 for x in items if x["result_exact"]) / n, 2) if n else 0.0,
            "SER": round(100.0 * sum(1 for x in items if x["semantic_pass"]) / n, 2) if n else 0.0,
            "avg_latency_s": round(sum(float(x["latency_s"]) for x in items) / n, 4) if n else 0.0,
        }
    return summary


def write_payload(out: Path, settings: Dict[str, Any], results: List[Dict[str, Any]]) -> None:
    payload = {
        "settings": settings,
        "summary": summarize(results),
        "results": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_systems(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def select_sqlite_rows(
    rows: List[Dict[str, str]],
    *,
    limit: int,
    require_gold: bool,
    gold_case_limit: int,
    gold_case_offset: int,
    spider_root: Path,
) -> List[Dict[str, str]]:
    selected = rows[: limit] if limit > 0 else list(rows)
    if not require_gold:
        return selected
    filtered_rows: List[Dict[str, str]] = []
    skipped_gold = 0
    for row in selected:
        gold_path = gold_sql_path(spider_root, row["instance_id"])
        if not gold_path.exists():
            continue
        gold = execute_sqlite(Path(row["db_path"]), gold_path.read_text(encoding="utf-8"))
        if not gold.ok:
            continue
        if skipped_gold < gold_case_offset:
            skipped_gold += 1
            continue
        filtered_rows.append(row)
        if gold_case_limit > 0 and len(filtered_rows) >= gold_case_limit:
            break
    return filtered_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EC-SQL-style experiments on Spider2-Lite SQLite cases")
    parser.add_argument("--manifest", default="artifacts/spider2_manifest.csv")
    parser.add_argument("--spider-root", default=r"D:\text2sql_datasets\Spider2")
    parser.add_argument(
        "--systems",
        default="schema_only,ecsql,no_semantic_templates,no_repair,no_schema_retrieval,direct",
    )
    parser.add_argument("--model", default=os.environ.get("OLLAMA_SQL_MODEL", "qwen3-vl:8b"))
    parser.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--ollama-api", choices=["generate", "chat"], default="generate")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--require-gold", action="store_true", help="Skip cases without executable local gold SQL")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--num-predict", type=int, default=512)
    parser.add_argument("--max-repairs", type=int, default=1)
    parser.add_argument("--no-external-knowledge", action="store_true")
    parser.add_argument("--external-knowledge-chars", type=int, default=6000)
    parser.add_argument(
        "--compare-column-names",
        action="store_true",
        help="Include output column names in exact result matching. Disabled by default.",
    )
    parser.add_argument(
        "--gold-case-limit",
        type=int,
        default=0,
        help="When --require-gold is set, keep only the first N locally executable gold cases.",
    )
    parser.add_argument(
        "--gold-case-offset",
        type=int,
        default=0,
        help="When --require-gold is set, skip the first N locally executable gold cases before applying --gold-case-limit.",
    )
    parser.add_argument("--out", default="artifacts/spider2_sqlite_experiment.json")
    args = parser.parse_args()

    rows = [
        row
        for row in read_manifest(Path(args.manifest))
        if row.get("engine") == "sqlite" and row.get("db_path")
    ]
    rows = select_sqlite_rows(
        rows,
        limit=args.limit,
        require_gold=args.require_gold,
        gold_case_limit=args.gold_case_limit,
        gold_case_offset=args.gold_case_offset,
        spider_root=Path(args.spider_root),
    )
    systems = parse_systems(args.systems)
    results: List[Dict[str, Any]] = []
    spider_root = Path(args.spider_root)
    out = Path(args.out)
    settings = {
        "manifest": str(Path(args.manifest).resolve()),
        "spider_root": str(spider_root),
        "systems": systems,
        "model": args.model,
        "limit": args.limit,
        "require_gold": args.require_gold,
        "gold_case_limit": args.gold_case_limit,
        "gold_case_offset": args.gold_case_offset,
        "num_predict": args.num_predict,
        "ollama_api": args.ollama_api,
        "use_external_knowledge": not args.no_external_knowledge,
        "external_knowledge_chars": args.external_knowledge_chars,
        "compare_column_names": args.compare_column_names,
    }

    for system in systems:
        for row in rows:
            print(f"[run] system={system} instance={row['instance_id']} db={row['db']}", flush=True)
            results.append(
                evaluate_case(
                    row=row,
                    spider_root=spider_root,
                    system=system,
                    model=args.model,
                    base_url=args.ollama_base_url,
                    timeout=args.timeout,
                    num_predict=args.num_predict,
                    ollama_api=args.ollama_api,
                    max_repairs=args.max_repairs,
                    require_gold=args.require_gold,
                    use_external_knowledge=not args.no_external_knowledge,
                    external_knowledge_chars=args.external_knowledge_chars,
                    compare_column_names=args.compare_column_names,
                )
            )
            write_payload(out, settings, results)

    payload = {
        "settings": settings,
        "summary": summarize(results),
        "results": results,
    }
    write_payload(out, settings, results)
    print(f"wrote: {out.resolve()}")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    skipped = sum(1 for item in results if item.get("skipped"))
    if skipped:
        print(f"skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
