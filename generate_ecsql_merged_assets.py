from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import textwrap

OUT = Path(__file__).resolve().parent


def font(size, bold=False):
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


INK = "#1F2933"
MUTED = "#5E6B76"
LINE = "#25313B"
BLUE = ("#DCEBFA", "#275C86")
GREEN = ("#E3F2E8", "#2B7A4B")
AMBER = ("#FFF0C9", "#A66A00")
RED = ("#FBE2E2", "#A43B3B")
PURPLE = ("#EEE8F8", "#604A96")
GRAY = ("#EEF2F5", "#657482")
WHITE = "#FFFFFF"


def rounded(draw, xy, fill, outline, width=4, radius=22):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def text_center(draw, xy, text, size=32, bold=False, fill=INK, spacing=5):
    x1, y1, x2, y2 = xy
    f = font(size, bold)
    lines = text.split("\n")
    heights = []
    widths = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=f)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    total_h = sum(heights) + spacing * (len(lines) - 1)
    y = y1 + (y2 - y1 - total_h) / 2
    for line, w, h in zip(lines, widths, heights):
        draw.text((x1 + (x2 - x1 - w) / 2, y), line, font=f, fill=fill)
        y += h + spacing


def label(draw, xy, text, size=28, bold=True, fill=INK):
    draw.text(xy, text, font=font(size, bold), fill=fill)


def box(draw, x, y, w, h, title, body="", color=BLUE):
    fill, edge = color
    rounded(draw, (x, y, x + w, y + h), fill, edge)
    text_center(draw, (x + 12, y + 10, x + w - 12, y + 55), title, size=25, bold=True)
    if body:
        text_center(draw, (x + 16, y + 60, x + w - 16, y + h - 12), body, size=21, fill=INK, spacing=4)


def arrow(draw, p1, p2, fill=LINE, width=5):
    draw.line((p1, p2), fill=fill, width=width)
    x1, y1 = p1
    x2, y2 = p2
    import math

    ang = math.atan2(y2 - y1, x2 - x1)
    l = 22
    a = 0.55
    pts = [
        (x2, y2),
        (x2 - l * math.cos(ang - a), y2 - l * math.sin(ang - a)),
        (x2 - l * math.cos(ang + a), y2 - l * math.sin(ang + a)),
    ]
    draw.polygon(pts, fill=fill)


def poly_arrow(draw, points, fill=LINE, width=5):
    for a, b in zip(points[:-1], points[1:]):
        draw.line((a, b), fill=fill, width=width)
    arrow(draw, points[-2], points[-1], fill=fill, width=width)


def small_tag(draw, x, y, text, color=GRAY):
    fill, edge = color
    rounded(draw, (x, y, x + 250, y + 48), fill, edge, width=3, radius=18)
    text_center(draw, (x, y + 2, x + 250, y + 46), text, size=18, bold=True)


def save(img, stem):
    png = OUT / f"{stem}.png"
    pdf = OUT / f"{stem}.pdf"
    img.save(png)
    img.convert("RGB").save(pdf, "PDF", resolution=300.0)
    print(f"saved {png}")
    print(f"saved {pdf}")


def schema_kg():
    W, H = 3000, 1500
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    label(d, (70, 45), "Dictionary-to-Schema-KG Construction and Retrieval", 42)
    d.text((W - 700, 55), "EC-SQL / RagAnything schema-KG", font=font(26), fill=MUTED)

    # Lanes
    d.rounded_rectangle((55, 130, W - 55, 715), radius=30, fill="#F7FBF8", outline="#C8DEC9", width=4)
    d.rounded_rectangle((55, 785, W - 55, H - 65), radius=30, fill="#F7FAFF", outline="#C6D6EA", width=4)
    label(d, (95, 160), "Build lane", 30, fill=GREEN[1])
    label(d, (95, 815), "Retrieval lane", 30, fill=BLUE[1])

    # Build lane boxes
    xs = [140, 500, 900]
    y = 245
    w, h = 300, 165
    box(d, xs[0], y, w, h, "Database\nDictionary", "tables, columns\nChinese labels\ntypes + usage", GREEN)
    box(d, xs[1], y, w, h, "Normalization", "uppercase IDs\nremove empty rows\ncanonical JSON", GREEN)
    box(d, xs[2], y, w, h, "Custom KG\nAdapter", "build_custom_kg\nquery_keywords\nparse markers", GREEN)
    out_y = 470
    out_xs = [1280, 1680, 2080]
    box(d, out_xs[0], out_y, w, h, "Table\nEntities", "TABLE::t\n|V| = 292", AMBER)
    box(d, out_xs[1], out_y, w, h, "Marker-Rich\nChunks", "[[TABLE:t]]\n[[COLUMN:t.c]]\n293 chunks", AMBER)
    box(d, out_xs[2], out_y, w, h, "Relation\nCatalog", "join-like keys\ncode mappings\n|E| = 287", AMBER)
    for i in range(2):
        arrow(d, (xs[i] + w, y + h // 2), (xs[i + 1], y + h // 2), GREEN[1])
    poly_arrow(d, [(xs[2] + w, y + 52), (1195, y + 52), (1195, out_y + 82), (out_xs[0], out_y + 82)], GREEN[1])
    poly_arrow(d, [(xs[2] + w, y + 82), (1585, y + 82), (1585, out_y + 82), (out_xs[1], out_y + 82)], GREEN[1])
    poly_arrow(d, [(xs[2] + w, y + 112), (1985, y + 112), (1985, out_y + 82), (out_xs[2], out_y + 82)], GREEN[1])
    box(d, 2540, 360, 300, 165, "Persisted\nLightRAG Store", "schema signature\nreuse unless\ndictionary changes", GREEN)
    arrow(d, (2380, out_y + 82), (2540, 442), GREEN[1])
    small_tag(d, 2500, 560, "parseable markers", GREEN)
    small_tag(d, 2220, 650, "dictionary-derived", GREEN)

    # Retrieval lane
    xs2 = [140, 505, 870, 1250, 1640, 2040, 2440]
    y2 = 930
    box(d, xs2[0], y2, 285, 165, "Question q", "terms\ntable names\ncolumn labels", BLUE)
    box(d, xs2[1], y2, 285, 165, "Query\nKeywords", "high/low terms\nCJK n-grams\nidentifiers", BLUE)
    box(d, xs2[2], y2, 300, 165, "LightRAG\nQuery", "mode = mix\nentity/relation\nchunk top-k", BLUE)
    box(d, xs2[3], y2, 310, 165, "hits_from_\ncontext", "parse markers\nkeyword ranking\nrelation evidence", BLUE)
    box(d, xs2[4], y2, 310, 165, "Candidate\nSchema", "live-valid tables\ncolumns + relations\nKG context", BLUE)
    box(d, xs2[5], y2, 320, 165, "Budget +\nClosure", "candidate 14\nfinal 16\nadd linked tables", PURPLE)
    box(d, xs2[6], y2, 340, 165, "Bounded\nPrompt Schema", "schema text\njoin hints\ncode-table hints", BLUE)
    for i in range(len(xs2) - 1):
        arrow(d, (xs2[i] + (320 if i >= 5 else 310 if i in [3, 4] else 300 if i == 2 else 285), y2 + 82), (xs2[i + 1], y2 + 82), BLUE[1])
    d.text((995, 850), "LightRAG reuses the persisted schema store unless the dictionary signature changes", font=font(23), fill=MUTED)
    small_tag(d, 1200, 1185, "1024-d lexical hash", GRAY)
    small_tag(d, 1500, 1185, "live catalog binding", GRAY)
    small_tag(d, 1800, 1185, "bounded prompt", GRAY)

    save(img, "schema_kg")


def repair_semantic():
    W, H = 3300, 1500
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    label(d, (70, 45), "Catalog-Verified Repair and Semantic Evidence Protocol", 42)
    d.text((W - 900, 55), "Merged Fig.3 + Fig.4: repair loop on the left, semantic decision on the right", font=font(25), fill=MUTED)

    # Background panels
    d.rounded_rectangle((55, 130, 1660, H - 65), radius=30, fill="#FFF9F9", outline="#E7C8C8", width=4)
    d.rounded_rectangle((1710, 130, W - 55, H - 65), radius=30, fill="#F8FBFF", outline="#C6D6EA", width=4)
    label(d, (95, 160), "A. Repair loop", 32, fill=RED[1])
    label(d, (1750, 160), "B. Semantic evidence check", 32, fill=BLUE[1])

    # Repair loop boxes
    box(d, 130, 285, 315, 145, "Candidate SQL", "explicit synthesis\nor qwen3-vl generation", AMBER)
    box(d, 560, 285, 330, 145, "Guard + Probe", "SELECT-only\nallowed identifiers\nOracle 1-row probe", BLUE)
    box(d, 1020, 285, 280, 145, "Pass?", "no static error\nprobe executes", PURPLE)
    box(d, 1320, 285, 250, 145, "Predicted SQL", "execution-safe\nnot yet semantic proof", GREEN)
    arrow(d, (445, 357), (560, 357))
    arrow(d, (890, 357), (1020, 357))
    arrow(d, (1300, 357), (1320, 357), GREEN[1])

    box(d, 560, 575, 330, 150, "Error\nClassification", "MISSING_TABLE\nINVALID_COL / SYNTAX\nGROUPBY / ALIAS", RED)
    box(d, 150, 805, 330, 150, "Update\nRepair State", "history H\nforbidden SQL F\nbanned tables B", RED)
    box(d, 560, 805, 330, 150, "Temperature\nSchedule", "0.0, 0.5, 0.7\n0.85, 0.9\n+ duplicate penalty", RED)
    box(d, 970, 805, 330, 150, "Regenerate", "qwen3-vl:8b\nKG context\nrepair history", BLUE)
    arrow(d, (1160, 430), (725, 575), RED[1])
    arrow(d, (560, 650), (480, 880), RED[1])
    arrow(d, (480, 880), (560, 880), RED[1])
    arrow(d, (890, 880), (970, 880), RED[1])
    arrow(d, (1135, 805), (300, 430), RED[1])
    d.text((1035, 1020), "next round, i <= 5", font=font(22), fill=RED[1])

    box(d, 150, 1125, 330, 145, "After 5\nFailed Rounds", "standard repair exhausted", RED)
    box(d, 560, 1125, 330, 145, "Final Repair", "_final_repair()\none repair\ntemperature = 0.3", RED)
    box(d, 970, 1125, 330, 145, "Validation\nGate", "dictionary valid?\nlive-catalog valid?\nOracle probe?", BLUE)
    box(d, 1350, 1125, 250, 145, "Schema-Only\nFallback", "live-valid joins\nmay emit NULL AS\nexecution-safe only", RED)
    arrow(d, (480, 1197), (560, 1197), RED[1])
    arrow(d, (890, 1197), (970, 1197), RED[1])
    arrow(d, (1300, 1197), (1350, 1197), RED[1])
    arrow(d, (1090, 1125), (1400, 430), GREEN[1])
    d.text((1120, 1085), "valid repair returns", font=font(21), fill=GREEN[1])
    d.text((1315, 1085), "invalid", font=font(21), fill=RED[1])

    # Semantic panel
    box(d, 1785, 285, 310, 150, "Benchmark\nCase", "question\nselected tables\nrequired columns\ngold SQL", AMBER)
    box(d, 2210, 285, 310, 150, "Generated\nSQL", "EC-SQL or baseline\nmay be executable\nor fallback", AMBER)
    box(d, 2635, 285, 310, 150, "Live Oracle\nExecution", "run predicted SQL\nrun gold SQL\nnormalize results", GREEN)
    arrow(d, (2095, 360), (2210, 360), BLUE[1])
    arrow(d, (2520, 360), (2635, 360), BLUE[1])

    box(d, 1940, 620, 370, 165, "Evidence\nRecord", "exec_ok\nresult_exact\ntable/column coverage\nNULL placeholder", BLUE)
    box(d, 2480, 620, 520, 165, "Semantic Pass SER(m)", "Exec(pred) AND Result(pred)=Result(gold)\nAND TableCov=1 AND ColumnCov=1\nAND NOT NullPH", BLUE)
    arrow(d, (2790, 435), (2310, 650), BLUE[1])
    arrow(d, (2310, 700), (2480, 700), BLUE[1])

    box(d, 1800, 960, 310, 145, "Execution-only\ntrap", "SQL runs but answers\nthe wrong question", RED)
    box(d, 2180, 960, 310, 145, "Coverage\ntrap", "result may be close\nbut misses required\nidentifiers", RED)
    box(d, 2560, 960, 310, 145, "Placeholder\ntrap", "NULL AS labels execute\nbut fail SER", RED)
    box(d, 2880, 960, 270, 145, "Strict\npass", "all evidence\nmust agree", GREEN)
    arrow(d, (2670, 785), (1955, 960), RED[1])
    arrow(d, (2730, 785), (2335, 960), RED[1])
    arrow(d, (2790, 785), (2715, 960), RED[1])
    arrow(d, (2950, 785), (3015, 960), GREEN[1])

    # Bridge
    arrow(d, (1570, 357), (1785, 360), GREEN[1])
    d.text((1585, 310), "successful candidate\nenters evidence check", font=font(22), fill=GREEN[1])
    d.text((1355, 1300), "fallback is evaluated by the\nsame semantic evidence gate", font=font(21), fill=RED[1])
    d.line((1600, 1255, 1710, 1255), fill=RED[1], width=5)
    arrow(d, (1710, 1255), (1810, 1105), RED[1])

    save(img, "repair_semantic_combined")


if __name__ == "__main__":
    schema_kg()
    repair_semantic()
