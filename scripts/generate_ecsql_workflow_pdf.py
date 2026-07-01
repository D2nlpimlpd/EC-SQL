from __future__ import annotations

from math import atan2, cos, pi, sin
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "figures" / "ecsql_workflow.pdf"

W = 7.35 * inch
H = 5.35 * inch

PALETTE = {
    "ink": colors.HexColor("#142033"),
    "line": colors.HexColor("#536D88"),
    "blue": colors.HexColor("#2D75B8"),
    "blue_fill": colors.HexColor("#F2F8FF"),
    "green": colors.HexColor("#2F8A4C"),
    "green_fill": colors.HexColor("#F1FAF3"),
    "purple": colors.HexColor("#7055B8"),
    "purple_fill": colors.HexColor("#F6F2FF"),
    "orange": colors.HexColor("#C56D1F"),
    "orange_fill": colors.HexColor("#FFF6EA"),
    "red": colors.HexColor("#BA4D5E"),
    "red_fill": colors.HexColor("#FFF2F4"),
    "slate": colors.HexColor("#5C6D7E"),
    "slate_fill": colors.HexColor("#F4F7FA"),
    "white": colors.white,
}


def set_font(c: canvas.Canvas, size: float, bold: bool = False, color=PALETTE["ink"]) -> None:
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)


def draw_box(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    lines: list[str],
    stroke,
    fill,
    *,
    title_size: float = 7.0,
    body_size: float = 5.55,
) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(1.15)
    c.roundRect(x, y, w, h, 7, fill=1, stroke=1)

    c.setFillColor(PALETTE["white"])
    c.roundRect(x + 2, y + h - 18, w - 4, 15, 5, fill=1, stroke=0)
    set_font(c, title_size, True, stroke)
    c.drawCentredString(x + w / 2, y + h - 13.6, title)

    set_font(c, body_size, False, PALETTE["ink"])
    step = body_size + 1.35
    total = step * len(lines)
    start = y + (h - 20 - total) / 2 + total - 1.0
    for i, line in enumerate(lines):
        c.drawCentredString(x + w / 2, start - i * step, line)


def arrow_head(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float, color) -> None:
    angle = atan2(y2 - y1, x2 - x1)
    head = 5.9
    spread = pi / 7
    p1 = (x2 - head * cos(angle - spread), y2 - head * sin(angle - spread))
    p2 = (x2 - head * cos(angle + spread), y2 - head * sin(angle + spread))
    c.setStrokeColor(color)
    c.line(x2, y2, p1[0], p1[1])
    c.line(x2, y2, p2[0], p2[1])


def arrow(c: canvas.Canvas, points: list[tuple[float, float]], color=PALETTE["line"], width: float = 1.05) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(width)
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        c.line(x1, y1, x2, y2)
    arrow_head(c, points[-2][0], points[-2][1], points[-1][0], points[-1][1], color)


def dot(c: canvas.Canvas, x: float, y: float, color=PALETTE["line"]) -> None:
    c.setFillColor(color)
    c.circle(x, y, 2.1, fill=1, stroke=0)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=(W, H))
    c.setFillColor(PALETTE["white"])
    c.rect(0, 0, W, H, fill=1, stroke=0)

    left = 24
    right = 278
    top_y = 314
    top_w = 228
    top_h = 52

    draw_box(
        c,
        left,
        top_y,
        top_w,
        top_h,
        "Dataset and Setup",
        [
            "RUN_PACKET_ON_SERVER.sh / one_click_linux.sh setup",
            "setup_linux.sh creates .venv and installs pinned deps",
            "download_spider2.py plus Ollama/HF model preparation",
        ],
        PALETTE["blue"],
        PALETTE["blue_fill"],
        title_size=7.1,
        body_size=5.35,
    )
    draw_box(
        c,
        right,
        top_y,
        top_w,
        top_h,
        "Manifest and Launch",
        [
            "spider2_manifest.py writes artifacts/spider2_manifest.csv",
            "launch_server_benchmark.sh writes launch.env and marker",
            "server_job.log and server_job.pid track the run",
        ],
        PALETTE["orange"],
        PALETTE["orange_fill"],
        title_size=7.1,
        body_size=5.35,
    )
    arrow(c, [(left + top_w, top_y + top_h / 2), (right, top_y + top_h / 2)])

    matrix_x = 88
    matrix_y = 244
    matrix_w = 352
    matrix_h = 46
    draw_box(
        c,
        matrix_x,
        matrix_y,
        matrix_w,
        matrix_h,
        "Experiment Matrix Dispatch",
        [
            "run_full_server_benchmark.sh sets model lists, limits, systems, and ablations",
            "run_server_experiments.sh checks models, runs smoke tests, and dispatches jobs",
        ],
        PALETTE["orange"],
        PALETTE["orange_fill"],
        title_size=7.0,
        body_size=5.35,
    )
    arrow(c, [(right + top_w / 2, top_y), (right + top_w / 2, matrix_y + matrix_h)])

    branch_y = 128
    branch_h = 84
    branch_w = 232
    sqlite_x = 20
    dbt_x = 276
    draw_box(
        c,
        sqlite_x,
        branch_y,
        branch_w,
        branch_h,
        "SQLite Text-to-SQL Runner",
        [
            "run_spider2_sqlite_experiment.py",
            "from_sqlite_database -> retrieve_tables/schema_prompt",
            "semantic templates or Ollama generation",
            "semantic_guard_errors + execute_sqlite",
            "repair_sql_after_execution -> SemanticEvidence",
            "writes spider2_sqlite_*.json",
        ],
        PALETTE["green"],
        PALETTE["green_fill"],
        title_size=7.0,
        body_size=5.05,
    )
    draw_box(
        c,
        dbt_x,
        branch_y,
        branch_w,
        branch_h,
        "DBT Project Runner",
        [
            "run_spider2_dbt_experiment.py / run_spider2_dbt_llm_edit_experiment.py",
            "copy_case, setup repairs, DuckDB schema, dbt graph",
            "deterministic fallbacks or optional LLM edits",
            "dbt deps/run -> predicted DuckDB -> eval_tables",
            "failure_history drives repair rounds",
            "writes spider2_dbt_*.json",
        ],
        PALETTE["purple"],
        PALETTE["purple_fill"],
        title_size=7.0,
        body_size=4.85,
    )

    split_x = matrix_x + matrix_w / 2
    split_y = 226
    arrow(c, [(split_x, matrix_y), (split_x, split_y), (sqlite_x + branch_w / 2, split_y), (sqlite_x + branch_w / 2, branch_y + branch_h)])
    arrow(c, [(split_x, split_y), (dbt_x + branch_w / 2, split_y), (dbt_x + branch_w / 2, branch_y + branch_h)])
    dot(c, split_x, split_y)

    aggregate_x = 88
    aggregate_y = 64
    aggregate_w = 352
    aggregate_h = 42
    draw_box(
        c,
        aggregate_x,
        aggregate_y,
        aggregate_w,
        aggregate_h,
        "Aggregation and Diagnostics",
        [
            "aggregate_experiment_results.py reads spider2*.json and *_registered.json",
            "analyze_experiment_failures.py writes summary, cases, and failure reports",
        ],
        PALETTE["red"],
        PALETTE["red_fill"],
        title_size=7.0,
        body_size=5.2,
    )
    merge_x = aggregate_x + aggregate_w / 2
    merge_y = 116
    arrow(c, [(sqlite_x + branch_w / 2, branch_y), (sqlite_x + branch_w / 2, merge_y), (merge_x, merge_y), (merge_x, aggregate_y + aggregate_h)])
    arrow(c, [(dbt_x + branch_w / 2, branch_y), (dbt_x + branch_w / 2, merge_y), (merge_x, merge_y), (merge_x, aggregate_y + aggregate_h)])
    dot(c, merge_x, merge_y, PALETTE["red"])

    output_x = 88
    output_y = 12
    output_w = 352
    output_h = 36
    draw_box(
        c,
        output_x,
        output_y,
        output_w,
        output_h,
        "Paper-ready Outputs",
        [
            "validate_server_matrix.py, build_server_evidence_report.py, build_server_abstract.py",
            "LaTeX snippets, evidence tables, abstract, checksumed result bundle",
        ],
        PALETTE["slate"],
        PALETTE["slate_fill"],
        title_size=7.0,
        body_size=5.05,
    )
    arrow(c, [(merge_x, aggregate_y), (merge_x, output_y + output_h)], PALETTE["slate"])

    c.showPage()
    c.save()
    print(OUT)


if __name__ == "__main__":
    main()
