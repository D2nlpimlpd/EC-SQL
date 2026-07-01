#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate Figure 1 for the EC-SQL paper.

The diagram mirrors the current implementation:
RagAnythingSchemaRetriever -> retrieve_schema -> explicit_structured_sql
or qwen3-vl generation -> Guard/Oracle execution -> Repair/FinalRepair/
schema-only fallback, with the offline semantic evidence checker used in
semantic_50_eval.py.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon


OUT = Path(__file__).resolve().parent

COLORS = {
    "ink": "#1F2933",
    "muted": "#62717C",
    "line": "#25313B",
    "blue": "#DCEBFA",
    "blue_edge": "#275C86",
    "green": "#E3F2E8",
    "green_edge": "#2B7A4B",
    "amber": "#FFF0C9",
    "amber_edge": "#A66A00",
    "red": "#FBE2E2",
    "red_edge": "#A43B3B",
    "gray": "#EEF2F5",
    "gray_edge": "#657482",
    "purple": "#EEE8F8",
    "purple_edge": "#604A96",
    "white": "#FFFFFF",
}


def box(ax, x, y, w, h, title, body="", color="blue", ls="-", lw=1.0):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.035,rounding_size=0.045",
        facecolor=COLORS[color],
        edgecolor=COLORS[f"{color}_edge"],
        linewidth=lw,
        linestyle=ls,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2,
        y + h - 0.13,
        title,
        ha="center",
        va="top",
        fontsize=8.2,
        fontweight="bold",
        color=COLORS["ink"],
    )
    if body:
        ax.text(
            x + w / 2,
            y + h / 2 - 0.12,
            body,
            ha="center",
            va="center",
            fontsize=6.35,
            color=COLORS["ink"],
            linespacing=1.08,
        )
    return patch


def diamond(ax, cx, cy, w, h, title, body=""):
    pts = [(cx, cy + h / 2), (cx + w / 2, cy), (cx, cy - h / 2), (cx - w / 2, cy)]
    patch = Polygon(
        pts,
        closed=True,
        facecolor=COLORS["purple"],
        edgecolor=COLORS["purple_edge"],
        linewidth=1.0,
    )
    ax.add_patch(patch)
    ax.text(cx, cy + 0.08, title, ha="center", va="center", fontsize=7.4, fontweight="bold", color=COLORS["ink"])
    if body:
        ax.text(cx, cy - 0.16, body, ha="center", va="center", fontsize=5.6, color=COLORS["muted"], linespacing=1.04)
    return patch


def arr(ax, start, end, text="", color=None, ls="-", lw=1.05, rad=0.0, toff=(0, 0), size=6.5):
    color = color or COLORS["line"]
    patch = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=8.0,
        linewidth=lw,
        linestyle=ls,
        color=color,
        connectionstyle=f"arc3,rad={rad}",
        shrinkA=2,
        shrinkB=2,
    )
    ax.add_patch(patch)
    if text:
        mx = (start[0] + end[0]) / 2 + toff[0]
        my = (start[1] + end[1]) / 2 + toff[1]
        ax.text(
            mx,
            my,
            text,
            ha="center",
            va="center",
            fontsize=size,
            color=color,
            bbox=dict(facecolor="white", edgecolor="none", pad=0.55, alpha=0.92),
        )
    return patch


def label(ax, x, y, text, color="muted", size=7.0, ha="left"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=size, color=COLORS[color])


def main() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.6,
        }
    )

    fig, ax = plt.subplots(figsize=(13.8, 7.2))
    ax.set_xlim(0, 13.8)
    ax.set_ylim(0, 7.2)
    ax.axis("off")

    ax.text(
        0.30,
        6.92,
        "EC-SQL: implementation-aligned enterprise NL2SQL pipeline",
        ha="left",
        va="center",
        fontsize=12.6,
        fontweight="bold",
        color=COLORS["ink"],
    )
    ax.text(
        13.45,
        6.92,
        "Local generation model: qwen3-vl:8b",
        ha="right",
        va="center",
        fontsize=8.3,
        color=COLORS["muted"],
    )

    label(ax, 0.36, 6.28, "Offline schema-KG construction", "green_edge", size=7.5)
    dict_box = box(
        ax,
        0.35,
        5.45,
        2.45,
        0.70,
        "Database Dictionary",
        "DB dictionary .xlsx / JSON\n292 tables; all columns kept",
        color="green",
    )
    kg_box = box(
        ax,
        3.12,
        5.45,
        3.05,
        0.70,
        "Customized RagAnything / LightRAG KG",
        "292 table entities; 287 relation edges\n293 retrievable schema chunks",
        color="green",
    )
    arr(ax, (2.80, 5.80), (3.12, 5.80), text="build_or_load()", color=COLORS["green_edge"], size=6.2)

    label(ax, 0.36, 4.86, "Online NL2SQL serving path", "blue_edge", size=7.5)
    request = box(ax, 0.35, 3.95, 1.45, 0.76, "User Request", "NL question\n+ date range", color="gray")
    retriever = box(
        ax,
        2.18,
        3.95,
        2.05,
        0.76,
        "Retriever",
        "retrieve_schema()\nKG + lexical evidence",
        color="blue",
    )
    schema = box(
        ax,
        4.62,
        3.95,
        2.05,
        0.76,
        "Catalog-Bound Schema",
        "live table/column check\ncandidate tables <= 16",
        color="blue",
    )
    decision = diamond(ax, 7.72, 4.33, 1.65, 0.95, "Explicit SQL?", "tables / columns /\njoin / operation")
    explicit = box(
        ax,
        8.82,
        4.56,
        2.15,
        0.72,
        "Explicit SQL Builder",
        "explicit_structured_sql()\nOracle template + probe",
        color="amber",
    )
    guard = box(
        ax,
        11.35,
        3.95,
        2.05,
        0.76,
        "Guard + Execute",
        "validate_sql_against_dictionary()\nOracle run_sql()",
        color="blue",
    )
    output = box(ax, 11.63, 2.72, 1.55, 0.66, "Output", "SQL + result\nwhen valid", color="green")

    planner = box(
        ax,
        4.87,
        2.64,
        1.85,
        0.70,
        "Optional Planner",
        "empty plan by default\nfor qwen3-vl mode",
        color="gray",
    )
    generator = box(
        ax,
        7.10,
        2.64,
        2.05,
        0.70,
        "Generator",
        "generate_multi_sql()\nqwen3-vl:8b",
        color="blue",
    )
    repairer = box(
        ax,
        7.08,
        1.52,
        2.08,
        0.72,
        "Repairer",
        "error class + history\nforbidden SQL + banned tables",
        color="red",
    )
    final_repair = box(
        ax,
        9.62,
        1.52,
        1.82,
        0.72,
        "Final Repair",
        "_final_repair()\nfull history + schema",
        color="red",
        ls="--",
    )
    fallback = box(
        ax,
        11.80,
        1.52,
        1.72,
        0.72,
        "Executable Fallback",
        "_schema_only_fallback_sql()\nexecution safety only",
        color="red",
        ls=":",
    )

    oracle = box(
        ax,
        4.78,
        0.35,
        2.15,
        0.70,
        "Live Catalog",
        "object existence; execution probes\ndialect adapter",
        color="green",
    )
    evaluator = box(
        ax,
        7.52,
        0.35,
        3.15,
        0.70,
        "Semantic Evidence Evaluator",
        "gold SQL result exact match\n+ table/column coverage + no NULL AS",
        color="amber",
    )
    gold = box(ax, 11.15, 0.35, 2.10, 0.70, "Benchmark Gold SQL", "50 questions\n25 single + 25 multi", color="amber")

    # Main path.
    arr(ax, (1.80, 4.33), (2.18, 4.33))
    arr(ax, (4.23, 4.33), (4.62, 4.33))
    arr(ax, (6.67, 4.33), (6.90, 4.33))
    arr(ax, (8.54, 4.33), (8.82, 4.92), text="yes", toff=(0.03, 0.10), size=6.2)
    arr(ax, (10.97, 4.92), (11.35, 4.43), rad=-0.10)
    arr(ax, (12.38, 3.95), (12.40, 3.38), text="ok", toff=(0.18, 0.00), size=6.2)

    # LLM path.
    arr(ax, (7.48, 3.88), (5.78, 3.34), text="no", toff=(-0.05, 0.12), size=6.2)
    arr(ax, (6.72, 2.99), (7.10, 2.99))
    arr(ax, (9.15, 2.99), (11.35, 4.05), rad=0.12, text="candidate SQL", toff=(0.08, 0.12), size=6.0)

    # Error handling.
    arr(
        ax,
        (11.55, 3.95),
        (8.95, 2.24),
        text="runtime/static error",
        color=COLORS["red_edge"],
        rad=-0.15,
        toff=(0.05, -0.02),
        size=6.0,
    )
    arr(
        ax,
        (7.82, 2.24),
        (7.80, 2.64),
        text="repair hint\nR <= 3",
        color=COLORS["red_edge"],
        rad=0.0,
        toff=(-0.42, 0.02),
        size=5.9,
    )
    arr(ax, (9.16, 1.88), (9.62, 1.88), text="if exhausted", color=COLORS["red_edge"], ls="--", size=5.8, toff=(0, 0.16))
    arr(ax, (11.44, 1.88), (11.80, 1.88), text="if invalid", color=COLORS["red_edge"], ls=":", size=5.8, toff=(0, 0.16))
    arr(ax, (10.52, 2.24), (11.70, 3.95), text="probe again", color=COLORS["red_edge"], ls="--", rad=-0.10, size=5.8, toff=(0.12, 0.02))
    arr(ax, (12.66, 2.24), (12.62, 2.72), text="not counted as SER\nif semantically wrong", color=COLORS["red_edge"], ls=":", size=5.6, toff=(0.40, 0.00))

    # Evidence links.
    dotted = dict(color=COLORS["muted"], ls=(0, (1.2, 2.0)), lw=0.85, size=5.7)
    arr(ax, (4.65, 5.45), (3.20, 4.71), text="KG retrieval", rad=0.08, **dotted)
    arr(ax, (1.65, 5.45), (3.18, 4.71), text="dictionary text", rad=-0.08, **dotted)
    arr(ax, (6.88, 1.05), (7.18, 1.52), text="catalog\nevidence", rad=-0.06, **dotted)
    arr(ax, (12.20, 1.05), (10.67, 0.70), rad=0.05, **dotted)
    label(ax, 9.10, 1.18, "offline benchmark evidence", "amber_edge", size=6.1, ha="center")

    ax.text(
        0.35,
        0.15,
        "Solid arrows are online serving; dashed/dotted arrows are conditional repair, fallback, or offline evaluation paths.",
        ha="left",
        va="center",
        fontsize=7.0,
        color=COLORS["muted"],
    )

    fig.tight_layout(pad=0.25)
    for stem in ("xiezuo", "figure1_ecsql"):
        for suffix in ("pdf", "svg", "png"):
            path = OUT / f"{stem}.{suffix}"
            if suffix == "png":
                fig.savefig(path, dpi=600, bbox_inches="tight", facecolor="white")
            else:
                fig.savefig(path, bbox_inches="tight", facecolor="white")
            print(f"saved {path}")


if __name__ == "__main__":
    main()
