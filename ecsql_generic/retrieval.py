"""Lightweight schema retrieval utilities for portable Text2SQL experiments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .dictionary import SchemaDictionary, TableDef


_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def lexical_terms(text: str) -> set[str]:
    terms = {tok.lower() for tok in _WORD_RE.findall(text or "") if tok}
    for raw in _WORD_RE.findall(text or ""):
        lowered = raw.lower()
        if "_" in lowered:
            terms.update(part for part in lowered.split("_") if part)
        if len(lowered) >= 3:
            terms.update(lowered[i : i + 3] for i in range(len(lowered) - 2))
    return terms


@dataclass(frozen=True)
class RetrievedTable:
    table: TableDef
    score: float
    matched_terms: Sequence[str]


def score_table(question_terms: set[str], table: TableDef) -> RetrievedTable:
    table_terms = lexical_terms(
        " ".join([table.name, table.label, table.description])
    )
    column_terms: set[str] = set()
    for column in table.columns:
        column_terms.update(
            lexical_terms(
                " ".join(
                    [
                        column.name,
                        column.label,
                        column.description,
                        column.data_type,
                    ]
                )
            )
        )
    table_hits = question_terms & table_terms
    column_hits = question_terms & column_terms
    score = 2.0 * len(table_hits) + 1.2 * len(column_hits)
    if table.normalized_name.lower() in question_terms:
        score += 100.0
    return RetrievedTable(
        table=table,
        score=score,
        matched_terms=tuple(sorted(table_hits | column_hits)),
    )


def retrieve_tables(
    schema: SchemaDictionary,
    question: str,
    *,
    limit: int = 16,
    relation_closure: int = 2,
) -> List[RetrievedTable]:
    question_terms = lexical_terms(question)
    scored = [score_table(question_terms, table) for table in schema.tables.values()]
    scored.sort(key=lambda item: (item.score, item.table.normalized_name), reverse=True)
    selected = [item for item in scored if item.score > 0][:limit]
    if not selected:
        selected = scored[: min(limit, len(scored))]

    selected_names = {item.table.normalized_name for item in selected}
    if relation_closure > 0:
        extra: list[RetrievedTable] = []
        scored_by_name = {item.table.normalized_name: item for item in scored}
        for rel in schema.relationships:
            left = str(rel.get("left") or rel.get("source") or "").upper()
            right = str(rel.get("right") or rel.get("target") or "").upper()
            if left in selected_names and right not in selected_names and right in scored_by_name:
                extra.append(scored_by_name[right])
                selected_names.add(right)
            elif right in selected_names and left not in selected_names and left in scored_by_name:
                extra.append(scored_by_name[left])
                selected_names.add(left)
            if len(extra) >= relation_closure:
                break
        selected.extend(extra)
    return selected[:limit]


def schema_prompt(
    tables: Iterable[TableDef],
    *,
    max_columns_per_table: int = 80,
) -> str:
    chunks: list[str] = []
    for table in tables:
        column_parts = []
        for column in table.columns[:max_columns_per_table]:
            desc = f" -- {column.description}" if column.description else ""
            dtype = f" {column.data_type}" if column.data_type else ""
            column_parts.append(f"  - {column.name}{dtype}{desc}")
        chunks.append(f"TABLE {table.name}\n" + "\n".join(column_parts))
    return "\n\n".join(chunks)

