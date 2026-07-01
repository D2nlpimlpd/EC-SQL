# visualization_engine.py
from collections import Counter
from datetime import date, datetime


def analyze_for_visualization(columns, rows, max_categories=20):
    """
    根据 SQL 查询结果，自动生成可视化建议
    返回格式直接供前端 ECharts 使用
    """
    charts = []

    if not rows or not columns:
        return {"charts": []}

    # 列转置
    col_data = {
        col: [row[i] for i in range(len(rows))]
        for col in columns
    }

    # ========= 1️⃣ 字符串字段 → 柱状图 / 饼图 =========
    for col, values in col_data.items():
        non_null = [v for v in values if isinstance(v, str)]
        if not non_null:
            continue

        counter = Counter(non_null)
        if len(counter) > max_categories:
            continue

        charts.append({
            "type": "bar",
            "title": f"{col} 分布",
            "x": list(counter.keys()),
            "y": list(counter.values())
        })

        charts.append({
            "type": "pie",
            "title": f"{col} 占比",
            "data": [{"name": k, "value": v} for k, v in counter.items()]
        })

    # ========= 2️⃣ 日期字段 → 折线图 =========
    for col, values in col_data.items():
        dates = [v for v in values if isinstance(v, (date, datetime))]
        if not dates:
            continue

        year_counter = Counter(v.year for v in dates)
        years = sorted(year_counter.keys())

        charts.append({
            "type": "line",
            "title": f"{col} 年度趋势",
            "x": years,
            "y": [year_counter[y] for y in years]
        })

    # ========= 3️⃣ 数值字段 → 直方图 =========
    for col, values in col_data.items():
        nums = [v for v in values if isinstance(v, (int, float))]
        if len(nums) < 10:
            continue

        charts.append({
            "type": "histogram",
            "title": f"{col} 数值分布",
            "data": nums
        })

    return {"charts": charts}