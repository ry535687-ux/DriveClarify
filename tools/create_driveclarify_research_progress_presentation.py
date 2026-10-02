#!/usr/bin/env python3
"""Generate the evidence-grounded Chinese DriveClarify research progress deck.

The deck intentionally separates:
1. implemented repository components,
2. bounded/offline prototypes,
3. planned experiments that have not started, and
4. future extensions that are not implemented.
"""

from __future__ import annotations

import csv
import html
import shutil
import textwrap
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables" / "driveclarify_research_progress_v1"
FIG = OUT / "figures"
SRC = OUT / "source_papers"
PAGES = SRC / "rendered_pages"

HERO_SOURCE = Path(
    "/home/buaa/.codex/generated_images/019fe6d2-477c-7163-b104-0afac1b307f6/"
    "exec-50a4e8bd-b349-4e30-b664-6dcad3de1d7c.png"
)
HERO = FIG / "imagegen_ambiguous_white_vans_v1.png"
LIVE_ASK = ROOT / "reports/closed_loop_v1_queue/03_end_to_end_demo/live/ask_sent_wait_active.png"
LIVE_ACT = ROOT / "reports/closed_loop_v1_queue/03_end_to_end_demo/live/act_control_tick_active.png"
LIVE_RETURN = ROOT / "reports/closed_loop_v1_queue/03_end_to_end_demo/live/control_returned_to_baseline.png"


SLIDE_W = 13.333
SLIDE_H = 7.5
FONT = "Noto Sans CJK SC"
MONO = "Noto Sans Mono CJK SC"

COLORS = {
    "navy": "0B132B",
    "ink": "17223B",
    "muted": "5C667A",
    "paper": "F6F8FB",
    "white": "FFFFFF",
    "line": "D7DDE8",
    "teal": "00A6A6",
    "cyan": "35B9C7",
    "amber": "F3A712",
    "orange": "E97B30",
    "green": "2CA58D",
    "red": "D94B4B",
    "purple": "7857D5",
    "magenta": "C447FF",
    "pale_teal": "E6F7F7",
    "pale_cyan": "E8F7FA",
    "pale_amber": "FFF4D8",
    "pale_green": "E9F6F1",
    "pale_red": "FCEBEC",
    "pale_purple": "F0ECFB",
    "dark_panel": "121A2F",
}


def rgb(value: str) -> RGBColor:
    value = value.lstrip("#")
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def set_fill(shape, color: str, transparency: int = 0) -> None:
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(color)
    shape.fill.transparency = transparency


def set_line(shape, color: str, width: float = 1.0, transparency: int = 0) -> None:
    shape.line.color.rgb = rgb(color)
    shape.line.width = Pt(width)
    shape.line.transparency = transparency


def add_rect(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    fill: str,
    line: str | None = None,
    radius: bool = True,
    transparency: int = 0,
):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    set_fill(shape, fill, transparency)
    if line:
        set_line(shape, line, 1.0)
    else:
        shape.line.fill.background()
    return shape


def add_text(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    size: float = 16,
    color: str = COLORS["ink"],
    bold: bool = False,
    font: str = FONT,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    valign: MSO_ANCHOR = MSO_ANCHOR.TOP,
    margin: float = 0.04,
    fit: bool = True,
    italic: bool = False,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.margin_left = Inches(margin)
    tf.margin_right = Inches(margin)
    tf.margin_top = Inches(margin)
    tf.margin_bottom = Inches(margin)
    tf.word_wrap = True
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_after = Pt(0)
    p.space_before = Pt(0)
    p.line_spacing = 1.0
    r = p.add_run()
    r.text = text
    r.font.name = font
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = rgb(color)
    # python-pptx's fit_text() cannot discover fonts on this Linux runtime.
    # The deck uses explicitly sized text boxes, so keep the authored font size.
    return box


def add_runs(
    slide,
    runs: list[tuple[str, str, bool]],
    x: float,
    y: float,
    w: float,
    h: float,
    size: float = 16,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    valign: MSO_ANCHOR = MSO_ANCHOR.TOP,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.03)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = 1.0
    for text, color, bold in runs:
        r = p.add_run()
        r.text = text
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = rgb(color)
    return box


def add_bullets(
    slide,
    items: list[str],
    x: float,
    y: float,
    w: float,
    h: float,
    size: float = 15,
    color: str = COLORS["ink"],
    bullet_color: str | None = None,
    spacing: float = 5,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = Inches(0.05)
    tf.margin_right = Inches(0.02)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = item
        p.level = 0
        p.font.name = FONT
        p.font.size = Pt(size)
        p.font.color.rgb = rgb(color)
        p.space_after = Pt(spacing)
        p.line_spacing = 1.08
        p.text = "•  " + item
        if bullet_color:
            p.runs[0].font.color.rgb = rgb(bullet_color)
    return box


def add_card(
    slide,
    title: str,
    body: str,
    x: float,
    y: float,
    w: float,
    h: float,
    accent: str = COLORS["teal"],
    fill: str = COLORS["white"],
    title_size: float = 17,
    body_size: float = 13,
):
    add_rect(slide, x, y, w, h, fill, COLORS["line"], True)
    add_rect(slide, x, y, 0.08, h, accent, None, False)
    add_text(slide, title, x + 0.18, y + 0.12, w - 0.28, 0.38, title_size, COLORS["ink"], True)
    add_text(slide, body, x + 0.18, y + 0.56, w - 0.28, h - 0.66, body_size, COLORS["muted"], False)


def add_chevron(slide, x: float, y: float, w: float = 0.28, h: float = 0.34, color: str = COLORS["teal"]):
    sh = slide.shapes.add_shape(MSO_SHAPE.CHEVRON, Inches(x), Inches(y), Inches(w), Inches(h))
    set_fill(sh, color)
    sh.line.fill.background()
    return sh


def add_connector(slide, x1: float, y1: float, x2: float, y2: float, color: str = COLORS["muted"], width: float = 1.5):
    line = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    line.line.color.rgb = rgb(color)
    line.line.width = Pt(width)
    return line


def add_picture_contain(slide, path: Path, x: float, y: float, w: float, h: float):
    with Image.open(path) as im:
        iw, ih = im.size
    ir = iw / ih
    fr = w / h
    if ir > fr:
        pw = w
        ph = w / ir
        px = x
        py = y + (h - ph) / 2
    else:
        ph = h
        pw = h * ir
        px = x + (w - pw) / 2
        py = y
    return slide.shapes.add_picture(str(path), Inches(px), Inches(py), Inches(pw), Inches(ph))


def add_picture_cover(slide, path: Path, x: float, y: float, w: float, h: float):
    with Image.open(path) as im:
        iw, ih = im.size
    ir = iw / ih
    fr = w / h
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
    if ir > fr:
        visible = fr / ir
        crop = (1.0 - visible) / 2.0
        pic.crop_left = crop
        pic.crop_right = crop
    else:
        visible = ir / fr
        crop = (1.0 - visible) / 2.0
        pic.crop_top = crop
        pic.crop_bottom = crop
    return pic


def add_badge(slide, text: str, color: str, x: float = 10.45, y: float = 0.27, w: float = 2.48):
    add_rect(slide, x, y, w, 0.35, color, None, True)
    add_text(slide, text, x + 0.07, y + 0.03, w - 0.14, 0.26, 10, COLORS["white"], True, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)


def base_slide(
    prs: Presentation,
    title: str,
    section: str,
    status: str,
    status_color: str,
    claim: str,
    evidence: str,
):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(COLORS["paper"])
    add_rect(slide, 0, 0, SLIDE_W, 0.09, COLORS["teal"], None, False)
    add_text(slide, section.upper(), 0.42, 0.2, 2.2, 0.25, 9.5, COLORS["teal"], True)
    # Keep long bilingual research titles clear of the status badge and body.
    # A two-line title is allowed, but its box must end before y=1.25.
    title_len = len(title)
    title_size = 26 if title_len <= 28 else 24 if title_len <= 32 else 23 if title_len <= 40 else 19.5
    add_text(slide, title, 0.42, 0.52, 9.72, 0.70, title_size, COLORS["navy"], True)
    add_badge(slide, status, status_color)
    # Standard claim/evidence/status strip required by the brief.
    add_rect(slide, 0, 6.72, SLIDE_W, 0.78, COLORS["navy"], None, False)
    add_text(slide, "结论", 0.38, 6.86, 0.44, 0.22, 9, COLORS["cyan"], True)
    add_text(slide, claim, 0.86, 6.81, 5.7, 0.36, 9.6, COLORS["white"], True)
    add_text(slide, "证据", 6.68, 6.86, 0.44, 0.22, 9, COLORS["amber"], True)
    add_text(slide, evidence, 7.15, 6.81, 5.76, 0.42, 8.5, "DDE4F2", False)
    return slide


def add_slide_number(slide, n: int):
    add_text(slide, f"{n:02d}", 12.08, 6.42, 0.82, 0.22, 8.5, COLORS["muted"], True, align=PP_ALIGN.CENTER)


def add_table(
    slide,
    data: list[list[str]],
    x: float,
    y: float,
    w: float,
    h: float,
    col_widths: list[float] | None = None,
    font_size: float = 11,
    header_fill: str = COLORS["navy"],
):
    rows, cols = len(data), len(data[0])
    shape = slide.shapes.add_table(rows, cols, Inches(x), Inches(y), Inches(w), Inches(h))
    table = shape.table
    if col_widths:
        total = sum(col_widths)
        for i, cw in enumerate(col_widths):
            table.columns[i].width = Inches(w * cw / total)
    for r_idx, row in enumerate(data):
        for c_idx, value in enumerate(row):
            cell = table.cell(r_idx, c_idx)
            cell.margin_left = cell.margin_right = Inches(0.05)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.text = str(value)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if r_idx == 0:
                cell.fill.fore_color.rgb = rgb(header_fill)
            else:
                cell.fill.fore_color.rgb = rgb(COLORS["white"] if r_idx % 2 else "EEF2F7")
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.CENTER if c_idx > 0 else PP_ALIGN.LEFT
                p.font.name = FONT
                p.font.size = Pt(font_size if r_idx else font_size - 0.3)
                p.font.bold = r_idx == 0 or c_idx == 0
                p.font.color.rgb = rgb(COLORS["white"] if r_idx == 0 else COLORS["ink"])
    return table


def prepare_assets() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    if not HERO_SOURCE.exists():
        raise FileNotFoundError(HERO_SOURCE)
    if not HERO.exists():
        shutil.copy2(HERO_SOURCE, HERO)

    crops = {
        "paper_simlingo_fig2.png": (PAGES / "simlingo_p3.png", (135, 140, 1225, 640)),
        "paper_lmdrive_fig4.png": (PAGES / "lmdrive_p5.png", (140, 0, 1220, 740)),
        "paper_drivegpt4_fig2.png": (PAGES / "drivegpt4_p3.png", (100, 115, 1265, 690)),
        "paper_talk2car_fig1.png": (PAGES / "talk2car_p2.png", (150, 140, 1175, 690)),
        "paper_knowno_fig1.png": (PAGES / "knowno_p2.png", (225, 145, 1145, 655)),
    }
    for name, (source, box) in crops.items():
        if not source.exists():
            raise FileNotFoundError(source)
        target = FIG / name
        with Image.open(source) as im:
            cropped = im.crop(box)
            cropped.save(target, optimize=True)

    for source, name in [
        (LIVE_ASK, "repo_closed_loop_ask_wait.png"),
        (LIVE_ACT, "repo_closed_loop_act_tick.png"),
        (LIVE_RETURN, "repo_closed_loop_baseline_return.png"),
    ]:
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, FIG / name)


def svg_box(x: int, y: int, w: int, h: int, title: str, subtitle: str, fill: str, stroke: str = "#D7DDE8", dashed: bool = False) -> str:
    dash = ' stroke-dasharray="12 8"' if dashed else ""
    title_e = html.escape(title)
    subtitle_e = html.escape(subtitle)
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{fill}" stroke="{stroke}" stroke-width="3"{dash}/>'
        f'<text x="{x + w/2}" y="{y + 46}" text-anchor="middle" class="title">{title_e}</text>'
        f'<text x="{x + w/2}" y="{y + 78}" text-anchor="middle" class="sub">{subtitle_e}</text>'
    )


def svg_arrow(x1: int, y1: int, x2: int, y2: int, color: str = "#00A6A6") -> str:
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="5" marker-end="url(#arrow)"/>'


def write_svg(name: str, body: str, title: str) -> None:
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#00A6A6"/></marker></defs>
<style>.title{{font:700 27px "Noto Sans CJK SC",sans-serif;fill:#17223B}}.sub{{font:400 17px "Noto Sans CJK SC",sans-serif;fill:#5C667A}}.heading{{font:700 40px "Noto Sans CJK SC",sans-serif;fill:#0B132B}}.label{{font:700 20px "Noto Sans CJK SC",sans-serif;fill:#00A6A6}}</style>
<rect width="1600" height="900" fill="#F6F8FB"/><rect width="1600" height="14" fill="#00A6A6"/>
<text x="65" y="78" class="heading">{html.escape(title)}</text>
{body}
</svg>'''
    (FIG / name).write_text(svg, encoding="utf-8")


def emit_architecture_svgs() -> None:
    body = []
    xs = [70, 365, 660, 955, 1250]
    labels = [
        ("原始指令", "+ 符号场景表", "#E8F7FA"),
        ("结构化解析", "unresolved slot", "#E6F7F7"),
        ("固定 K=2 候选", "validity / distinctness", "#FFF4D8"),
        ("区分性问题", "二选一 / 单查询", "#F0ECFB"),
        ("答案与重规划", "旧候选失效", "#E9F6F1"),
    ]
    for i, (title, sub, fill) in enumerate(labels):
        body.append(svg_box(xs[i], 330, 240, 120, title, sub, fill))
        if i < len(labels) - 1:
            body.append(svg_arrow(xs[i] + 240, 390, xs[i + 1] - 18, 390))
    body.append('<text x="800" y="560" text-anchor="middle" class="label">当前：离线结构化交互 + 受限闭环答案链路；非自由文本 NLU</text>')
    write_svg("fig_01_language_interaction_layer.svg", "".join(body), "语言交互层（当前仓库边界）")

    body = []
    rows = [
        ("输入", "指令 / 相机 / 车辆状态", "#E8F7FA"),
        ("语言与候选", "structured_interaction.py / candidate_wrapper.py", "#E6F7F7"),
        ("候选计划", "冻结 SimLingo，多候选顺序 forward", "#FFF4D8"),
        ("反事实后果", "counterfactual_evidence.py / m2b_binding.py", "#F0ECFB"),
        ("ACT / ASK / WAIT", "query_value_policy.py", "#E9F6F1"),
        ("M3 + 执行权", "reducer.py / resolver.py / existing PID", "#FCEBEC"),
    ]
    for i, (title, sub, fill) in enumerate(rows):
        y = 145 + i * 112
        body.append(svg_box(390, y, 820, 86, title, sub, fill))
        if i < len(rows) - 1:
            body.append(svg_arrow(800, y + 86, 800, y + 108))
    write_svg("fig_02_current_repository_architecture.svg", "".join(body), "当前仓库架构：从语言交互到 CARLA 执行权")

    body = []
    body.append(svg_box(610, 145, 380, 95, "合同与证据门", "候选 / 新鲜度 / query / safety", "#E8F7FA"))
    body.append(svg_arrow(800, 240, 800, 300))
    decisions = [
        (210, "ACT", "后果等价或唯一优势\n且物理安全可授权", "#E9F6F1"),
        (610, "ASK", "答案可改变动作\nquery value > 0", "#FFF4D8"),
        (1010, "WAIT", "未来信息 + holding\nwait value > 0", "#F0ECFB"),
    ]
    for x, title, sub, fill in decisions:
        body.append(svg_box(x, 360, 360, 145, title, sub.replace("\\n", " / "), fill))
        body.append(svg_arrow(800, 300, x + 180, 360))
    body.append(svg_box(610, 620, 380, 95, "否则 FALLBACK", "不等于 emergency stop", "#FCEBEC", "#D94B4B"))
    body.append(svg_arrow(800, 505, 800, 620, "#D94B4B"))
    write_svg("fig_03_act_ask_wait_decision.svg", "".join(body), "ACT / ASK / WAIT 决策逻辑")

    body = []
    body.append(svg_box(80, 325, 220, 120, "CARLA", "24 scenarios × 4 seeds", "#E8F7FA"))
    body.append(svg_arrow(300, 385, 390, 385))
    body.append(svg_box(390, 325, 220, 120, "SimLingo", "同一环境 / 同一配置", "#E6F7F7"))
    body.append(svg_arrow(610, 385, 700, 385))
    body.append(svg_box(700, 325, 220, 120, "8 种方法", "96 × 8 = 768 episodes", "#FFF4D8"))
    body.append(svg_arrow(920, 385, 1010, 385))
    body.append(svg_box(1010, 325, 220, 120, "ACT/ASK/WAIT", "private label join", "#F0ECFB"))
    body.append(svg_arrow(1230, 385, 1320, 385))
    body.append(svg_box(1320, 325, 220, 120, "指标", "decision / driving / safety", "#E9F6F1"))
    body.append('<text x="800" y="590" text-anchor="middle" class="label">当前状态：0 episodes；正式执行被四项预执行 blocker 阻断</text>')
    write_svg("fig_04_experiment_design.svg", "".join(body), "计划中的 Paper MVP 实验设计")

    body = []
    labels = [
        ("自然语言", "current input", False, "#E8F7FA"),
        ("歧义意识", "FUTURE integrated", True, "#FCEBEC"),
        ("视觉/语言 grounding", "FUTURE", True, "#FCEBEC"),
        ("候选后果推理", "current bounded", False, "#E6F7F7"),
        ("时间澄清", "FUTURE dynamic", True, "#F0ECFB"),
        ("ACT / ASK / WAIT", "current contract", False, "#FFF4D8"),
        ("闭环驾驶", "current one bounded demo", False, "#E9F6F1"),
    ]
    for i, (title, sub, dashed, fill) in enumerate(labels):
        y = 115 + i * 98
        body.append(svg_box(440, y, 720, 78, title, sub, fill, "#D94B4B" if dashed else "#D7DDE8", dashed))
        if i < len(labels) - 1:
            body.append(svg_arrow(800, y + 78, 800, y + 96))
    write_svg("fig_05_final_expected_architecture.svg", "".join(body), "最终期望架构：当前模块与未来扩展分层")


def generate_deck() -> tuple[Path, list[dict[str, str]], list[dict[str, str]]]:
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    prs.core_properties.title = "DriveClarify 研究进展汇报 V1"
    prs.core_properties.subject = "Consequence-Aware Active Clarification for Ambiguous Instructions"
    prs.core_properties.author = "DriveClarify research project"
    records: list[dict[str, str]] = []
    notes: list[dict[str, str]] = []

    def record(title: str, claim: str, evidence: str, status: str, note: str):
        records.append({"slide": str(len(prs.slides)), "title": title, "claim": claim, "evidence": evidence, "status": status})
        notes.append({"slide": str(len(prs.slides)), "title": title, "note": note})
        add_slide_number(prs.slides[-1], len(prs.slides))

    # 01 — title
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_picture_cover(slide, HERO, 0, 0, SLIDE_W, SLIDE_H)
    add_rect(slide, 0, 0, SLIDE_W, SLIDE_H, COLORS["navy"], None, False, 28)
    add_rect(slide, 0, 0, 0.14, SLIDE_H, COLORS["teal"], None, False)
    add_text(slide, "DriveClarify", 0.72, 0.74, 6.7, 0.7, 38, COLORS["white"], True)
    add_text(
        slide,
        "Consequence-Aware Active Clarification for Ambiguous Instructions\nin Closed-Loop Autonomous Driving",
        0.74,
        1.45,
        7.65,
        1.45,
        23,
        COLORS["white"],
        True,
    )
    add_text(slide, "面向闭环自动驾驶歧义指令的后果感知主动澄清", 0.76, 3.02, 7.2, 0.52, 19, "DDE4F2", False)
    add_rect(slide, 0.74, 4.13, 4.25, 1.22, COLORS["dark_panel"], "43506B", True, 12)
    add_text(slide, "研究方向", 0.96, 4.34, 0.95, 0.22, 10, COLORS["cyan"], True)
    add_text(slide, "Vision–Language–Action Models", 1.92, 4.26, 2.72, 0.38, 14, COLORS["white"], True)
    add_text(slide, "当前状态", 0.96, 4.74, 0.95, 0.22, 10, COLORS["amber"], True)
    add_text(slide, "Paper MVP 准备中｜正式 E2E 评测预执行阻断", 1.92, 4.67, 2.8, 0.46, 12.5, COLORS["white"], True)
    add_badge(slide, "PAPER MVP PREPARATION", COLORS["amber"], 10.22, 0.42, 2.7)
    add_rect(slide, 0, 6.72, SLIDE_W, 0.78, COLORS["navy"], None, False)
    add_text(slide, "结论", 0.38, 6.86, 0.44, 0.22, 9, COLORS["cyan"], True)
    add_text(slide, "闭环原型已打通，但论文性能实验尚未开始。", 0.86, 6.81, 5.7, 0.36, 9.6, COLORS["white"], True)
    add_text(slide, "证据", 6.68, 6.86, 0.44, 0.22, 9, COLORS["amber"], True)
    add_text(slide, "runtime_decision_authority_activation_v1；paper_mvp_end_to_end_evaluation_v0", 7.15, 6.81, 5.76, 0.42, 8.3, "DDE4F2")
    record(
        "DriveClarify",
        "闭环原型已打通，但论文性能实验尚未开始。",
        "reports/runtime_decision_authority_activation_v1/FINAL_REPORT.md; reports/paper_mvp_end_to_end_evaluation_v0/FINAL_REPORT.md",
        "PAPER MVP PREPARATION",
        "这一页先把边界说清楚：我们已经有语言交互、受限闭环和决策权激活，但还没有正式 Paper MVP 性能结果。后面所有数字会区分设计计数、合同验证和真实闭环观测。",
    )

    # 02 — core question
    title = "核心科学问题：VLA 何时不应立即行动？"
    claim = "DriveClarify 研究的是“何时需要先获取信息”，而不是单纯提高语言理解分数。"
    evidence = "reports/driveclarify_architecture_v0/DRIVECLARIFY_ARCHITECTURE_V0.md §1"
    slide = base_slide(prs, title, "Motivation", "问题定义｜非实现声明", COLORS["teal"], claim, evidence)
    add_text(slide, "Can an autonomous-driving VLA agent know when it should not act immediately?", 0.68, 1.42, 8.35, 0.88, 25, COLORS["navy"], True)
    add_text(slide, "能否在动作不可逆之前，判断：执行、提问，还是等待？", 0.7, 2.28, 7.6, 0.58, 19, COLORS["teal"], True)
    add_card(slide, "传统管线", "语言指令 → 轨迹 → 控制\n默认“一条指令 = 一个解释”", 0.72, 3.22, 3.65, 1.72, COLORS["muted"])
    add_card(slide, "真实世界", "指称 / 空间 / 时间 / 约束均可能欠定\n不确定本身不等于必须提问", 4.62, 3.22, 3.65, 1.72, COLORS["amber"])
    add_card(slide, "DriveClarify", "先比较候选解释造成的后果，再决定 ACT / ASK / WAIT", 8.52, 3.22, 3.9, 1.72, COLORS["teal"])
    add_text(slide, "关键转变", 0.74, 5.42, 1.02, 0.24, 10, COLORS["muted"], True)
    add_runs(slide, [("语言不确定性", COLORS["muted"], False), ("  →  ", COLORS["line"], False), ("决策相关的后果差异", COLORS["teal"], True)], 1.78, 5.29, 5.6, 0.5, 16)
    record(title, claim, evidence, "问题定义｜非实现声明", "动机不是让车在所有不确定时都停下或提问，而是判断不确定是否会改变最优动作；这也是后果感知相对语言置信度阈值的科学差异。")

    # 03 — ambiguity taxonomy
    title = "驾驶指令中的四类歧义"
    claim = "同一语言表述可产生不同的参考对象、空间关系、时间点或约束集合。"
    evidence = "reports/paper_mvp_scenario_freeze_v0/SCENARIO_CATALOG.json（每类 6 场景）"
    slide = base_slide(prs, title, "Motivation", "BENCHMARK TAXONOMY", COLORS["teal"], claim, evidence)
    cards = [
        ("指称歧义", "“跟在那辆白色车后面”\n多辆对象均满足描述", COLORS["cyan"]),
        ("空间歧义", "“停在入口旁边”\n旁边 / 入口存在多个可行区域", COLORS["green"]),
        ("时间歧义", "“经过巴士后转弯”\n等待信息与承诺点持续变化", COLORS["amber"]),
        ("约束欠定", "“找个安全地方靠边”\n安全、合法、可恢复条件未给全", COLORS["purple"]),
    ]
    for i, (ct, body, c) in enumerate(cards):
        x = 0.68 + (i % 2) * 6.15
        y = 1.45 + (i // 2) * 2.18
        add_card(slide, ct, body, x, y, 5.75, 1.74, c, title_size=20, body_size=15)
    add_text(slide, "场景冻结：referential / spatial / temporal / underspecified_constraint 各 6 个。", 0.75, 5.92, 11.7, 0.36, 12.5, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "BENCHMARK TAXONOMY", "这里用冻结场景集的四类标签作为研究分类，但这些标签属于场景注释，不是运行时模型已经自动发现的输出。")

    # 04 — white van example
    title = "例子：“Turn after the white van”并不唯一"
    claim = "只有当候选解释导致不同且不可忽略的驾驶后果时，澄清才有价值。"
    evidence = "ImageGen illustrative asset；概念依据：DriveClarify architecture causal chain"
    slide = base_slide(prs, title, "Motivation", "示意图｜非实验结果", COLORS["purple"], claim, evidence)
    add_picture_cover(slide, HERO, 0.55, 1.25, 7.45, 4.95)
    add_rect(slide, 0.55, 5.69, 7.45, 0.51, COLORS["navy"], None, False, 18)
    add_text(slide, "AI 生成概念图：两辆相似白色厢式车 → 两个可行转向承诺点", 0.78, 5.8, 7.0, 0.24, 10, COLORS["white"], False)
    add_card(slide, "解释 A", "“在近处白色厢式车之后转弯”\n更早承诺、较短可恢复窗口", 8.25, 1.58, 4.25, 1.35, COLORS["cyan"], COLORS["pale_cyan"], 18, 14)
    add_card(slide, "解释 B", "“在远处白色厢式车之后转弯”\n继续直行、稍后才承诺", 8.25, 3.12, 4.25, 1.35, COLORS["amber"], COLORS["pale_amber"], 18, 14)
    add_card(slide, "决定是否 ASK", "不是看句子“模不模糊”，而是看回答能否改变当前最优动作，并且答案能否在 deadline 前到达。", 8.25, 4.66, 4.25, 1.48, COLORS["teal"], COLORS["white"], 17, 13.2)
    record(title, claim, evidence, "示意图｜非实验结果", "这张图由 ImageGen 生成，只承担概念说明，绝不作为仓库实验图。真正的运行时证据在后面的 CARLA 闭环样例页。")

    # 05 — driving VLA literature
    title = "研究缺口 I：Driving VLA 能执行语言，但未显式比较多解释后果"
    claim = "现有 Driving VLA 将语言直接用于控制或动作预测，论文未报告主动歧义澄清的 ACT/ASK/WAIT 机制。"
    evidence = "SimLingo Fig.2 (CVPR 2025); LMDrive Fig.4 (CVPR 2024); DriveGPT4 Fig.2 (2023)"
    slide = base_slide(prs, title, "Related Work", "文献证据｜非仓库实现", COLORS["muted"], claim, evidence)
    papers = [
        ("SimLingo · CVPR 2025 · Fig.2", FIG / "paper_simlingo_fig2.png", "图像/导航/语言 → LLM → path & speed；强调语言—动作对齐。"),
        ("LMDrive · CVPR 2024 · Fig.4", FIG / "paper_lmdrive_fig4.png", "多模态传感器 + 导航/提示指令 → 控制与完成判断。"),
        ("DriveGPT4 · 2023 · Fig.2", FIG / "paper_drivegpt4_fig2.png", "视频 + 问答 → 解释文本与控制信号；主要为离线数据评测。"),
    ]
    for i, (ptitle, img, desc) in enumerate(papers):
        x = 0.45 + i * 4.28
        add_rect(slide, x, 1.28, 4.05, 4.98, COLORS["white"], COLORS["line"], True)
        add_text(slide, ptitle, x + 0.12, 1.39, 3.8, 0.34, 13.5, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_picture_contain(slide, img, x + 0.18, 1.83, 3.7, 2.78)
        add_text(slide, desc, x + 0.2, 4.74, 3.64, 0.75, 11.4, COLORS["muted"], False)
        add_text(slide, "未报告：多解释后果比较 / ASK / WAIT", x + 0.2, 5.58, 3.64, 0.36, 10.5, COLORS["red"], True, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "文献证据｜非仓库实现", "这页不否定这些系统的语言与驾驶能力；缺口是论文没有把一条可能歧义的指令拆成多解释，并在车辆动力学与闭环后果上决定是否提问。")

    # 06 — grounding
    title = "研究缺口 II：语言 grounding 找到“所指对象”，但不回答“是否值得问”"
    claim = "对象指称消解解决目标定位；DriveClarify 进一步关心不同 grounding 的驾驶后果是否决策关键。"
    evidence = "Talk2Car: Taking Control of Your Self-Driving Car, EMNLP-IJCNLP 2019, Fig.1"
    slide = base_slide(prs, title, "Related Work", "文献证据｜非仓库实现", COLORS["muted"], claim, evidence)
    add_rect(slide, 0.55, 1.28, 6.35, 4.96, COLORS["white"], COLORS["line"], True)
    add_picture_contain(slide, FIG / "paper_talk2car_fig1.png", 0.75, 1.46, 5.95, 3.48)
    add_text(slide, "Talk2Car 原论文 Fig.1：自然语言命令 → 场景中被指称对象", 0.78, 5.11, 5.9, 0.42, 11, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    add_card(slide, "Grounding 典型目标", "从图像候选区域中识别唯一 referent\n输出 object / region identity", 7.25, 1.48, 5.22, 1.44, COLORS["cyan"], COLORS["pale_cyan"], 18, 14)
    add_chevron(slide, 9.68, 3.15, 0.4, 0.5, COLORS["muted"])
    add_card(slide, "DriveClarify 追加问题", "若两个 referent 都合理：\n执行各自解释会不会导致不同路线、任务失败、时间或可恢复性？", 7.25, 3.05, 5.22, 1.74, COLORS["teal"], COLORS["pale_teal"], 18, 14)
    add_card(slide, "缺口", "“识别到对象” ≠ “知道何时必须 ASK”", 7.25, 5.02, 5.22, 1.05, COLORS["red"], COLORS["pale_red"], 17, 14)
    record(title, claim, evidence, "文献证据｜非仓库实现", "Talk2Car 很适合说明上游 grounding 的价值，但它不把两个可能 referent 的候选轨迹与闭环后果拿来比较。")

    # 07 — clarification and uncertainty
    title = "研究缺口 III：会提问的机器人仍缺少驾驶闭环、动力学与 WAIT"
    claim = "不确定性校准可以触发求助，但不等价于基于驾驶后果选择 ACT/ASK/WAIT。"
    evidence = "KnowNo, CoRL 2023, Fig.1; CLARA, arXiv:2306.10376"
    slide = base_slide(prs, title, "Related Work", "文献证据｜非仓库实现", COLORS["muted"], claim, evidence)
    add_rect(slide, 0.55, 1.3, 6.25, 4.9, COLORS["white"], COLORS["line"], True)
    add_picture_contain(slide, FIG / "paper_knowno_fig1.png", 0.77, 1.5, 5.8, 3.25)
    add_text(slide, "KnowNo 原论文 Fig.1：conformal prediction set > 1 → ask for help", 0.78, 4.95, 5.8, 0.38, 10.8, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    gap_table = [
        ["维度", "KnowNo / CLARA", "DriveClarify 研究目标"],
        ["触发量", "语言/计划不确定性", "候选驾驶后果差异与信息价值"],
        ["动作空间", "执行或求助", "ACT / ASK / WAIT / FALLBACK"],
        ["环境", "操控/桌面机器人", "持续演化的 CARLA 闭环"],
        ["控制", "非车辆动力学", "holding、deadline、执行权、PID 所有权"],
    ]
    add_table(slide, gap_table, 7.05, 1.48, 5.55, 3.85, [1.0, 1.5, 1.85], 10.5)
    add_card(slide, "核心区别", "uncertainty estimation ≠ action selection\n必须判断不确定是否足以改变当前最优驾驶动作。", 7.05, 5.48, 5.55, 0.68, COLORS["teal"], COLORS["pale_teal"], 16, 12)
    record(title, claim, evidence, "文献证据｜非仓库实现", "KnowNo 和 CLARA 已经证明主动提问是成熟方向；DriveClarify 的位置是把提问决策绑定到驾驶后果、时间窗和闭环控制权。")

    # 08 — gap summary table
    title = "研究缺口总结：DriveClarify 补的是“后果—信息—控制”闭环"
    claim = "当前仓库在后果比较与受限闭环上已有实现，但自动歧义发现和开放语言 grounding 仍未完成。"
    evidence = "外部论文 + repository evidence matrix（详见 literature_comparison.csv）"
    slide = base_slide(prs, title, "Related Work", "CURRENT + LITERATURE", COLORS["purple"], claim, evidence)
    data = [
        ["方法", "语言", "歧义检测", "后果比较", "决定 ASK", "WAIT", "闭环驾驶"],
        ["SimLingo", "●", "○", "○", "○", "○", "●"],
        ["LMDrive", "●", "○", "○", "○", "○", "●"],
        ["DriveGPT4", "●", "○", "○", "○", "○", "○"],
        ["Talk2Car", "●", "○", "○", "○", "○", "○"],
        ["KnowNo / CLARA", "●", "●", "◐", "●", "○", "○"],
        ["DriveClarify（当前）", "◐", "◐", "●", "●", "●", "◐"],
    ]
    add_table(slide, data, 0.55, 1.35, 12.2, 4.75, [2.5, 1, 1.25, 1.25, 1.15, 0.9, 1.25], 12)
    add_runs(slide, [("● 已覆盖   ", COLORS["green"], True), ("◐ 受限/离线原型   ", COLORS["amber"], True), ("○ 未报告或未实现", COLORS["muted"], True)], 2.95, 6.17, 7.4, 0.31, 11, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "CURRENT + LITERATURE", "DriveClarify 当前行不能读成完整系统：语言理解和歧义检测只到结构化离线/固定 fixture，闭环也只有一个受限 CARLA 样例。")

    # 09 — contributions
    title = "DriveClarify 贡献：新增语言交互层后，研究链条闭合"
    claim = "语言交互层把“检测到歧义”连接到可执行的单查询、答案解析与最新观测重规划。"
    evidence = "driveclarify_language/*; query_value_policy.py; m3_minimal_core/reducer.py; closed_loop_v1_queue/03_end_to_end_demo"
    slide = base_slide(prs, title, "Method", "受限已实现", COLORS["green"], claim, evidence)
    add_card(slide, "Enabling layer · 语言交互", "结构化解析 → 固定 K=2 候选 → 区分性问题 → 答案解析 → 旧候选失效", 0.58, 1.32, 12.15, 0.84, COLORS["purple"], COLORS["pale_purple"], 18, 13.5)
    contribs = [
        ("C1", "后果感知澄清决策", "不是按语言置信度提问；比较候选的 counterfactual task consequence。", COLORS["teal"]),
        ("C2", "统一 ACT / ASK / WAIT", "query value 与 wait value 同一 reducer 中竞争，硬门保持 fail-closed。", COLORS["amber"]),
        ("C3", "闭环澄清执行", "ASK → physical WAIT → delayed answer → latest-observation replan → bounded ACT。", COLORS["cyan"]),
        ("C4", "安全保持的执行权", "M3 不输出低层控制；authority resolver 维护 holding / candidate / baseline 所有权。", COLORS["green"]),
    ]
    for i, (tag, ct, body, c) in enumerate(contribs):
        x = 0.58 + (i % 2) * 6.2
        y = 2.45 + (i // 2) * 1.65
        add_card(slide, f"{tag} · {ct}", body, x, y, 5.92, 1.35, c, COLORS["white"], 17, 12.5)
    add_text(slide, "注意：上述贡献均需用后续正式 24 场景闭环实验验证其性能价值。", 0.7, 6.0, 11.95, 0.35, 12, COLORS["red"], True, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "受限已实现", "老师指出的缺口主要落在 enabling layer：没有问题与答案的生命周期，后果推理无法真正闭环。现在这条链已在结构化离线层和一个受限 CARLA 样例中出现。")

    # 10 — boundary
    title = "当前贡献与未来扩展边界"
    claim = "已实现能力必须带限定词；自动发现、真实 grounding、学习式候选和动态时间推理仍是未来工作。"
    evidence = "language/M2B/M3/runtime activation/closed-loop reports; ambiguity discovery prototype limitations"
    slide = base_slide(prs, title, "Method", "CLAIM BOUNDARY", COLORS["red"], claim, evidence)
    add_rect(slide, 0.55, 1.27, 6.05, 5.05, COLORS["pale_green"], "B8DDCF", True)
    add_text(slide, "已实现（严格限定）", 0.85, 1.52, 5.45, 0.4, 21, COLORS["green"], True)
    add_bullets(slide, [
        "候选后果推理：symbolic / diagnostic；受限闭环样例",
        "query value + wait value + ACT/ASK/WAIT policy",
        "结构化语言交互：固定 K=2、单次二选一",
        "M3 生命周期：离线完整 + 受限 live 链路",
        "CARLA 执行权：simulation-only、单个 0.1 s / 1 tick receipt",
        "24 场景 / 96 配置冻结：仅静态与合同校准",
    ], 0.88, 2.05, 5.35, 3.85, 13.2, COLORS["ink"], COLORS["green"], 8)
    add_rect(slide, 6.82, 1.27, 5.95, 5.05, COLORS["pale_red"], "E9B9BD", True)
    add_text(slide, "FUTURE EXTENSION · NOT IMPLEMENTED", 7.08, 1.52, 5.42, 0.4, 17.5, COLORS["red"], True)
    add_bullets(slide, [
        "自动歧义发现的 live 集成（现只有离线规则原型）",
        "真实视觉 grounding 与开放词汇 reference resolution",
        "学习式 / 动态候选生成与 clustering",
        "多轮自由语言对话与语言级推理",
        "基于距离、速度、通信延迟的动态时序澄清",
        "正式 24 场景 × 8 方法性能结果与安全收益声明",
    ], 7.08, 2.05, 5.25, 3.85, 13.2, COLORS["ink"], COLORS["red"], 8)
    record(title, claim, evidence, "CLAIM BOUNDARY", "这一页建议在汇报里停下来强调：语言层已经加上，但不能表述为自由文本理解；歧义发现有原型，但不能表述为 Paper MVP 的自动能力。")

    # 11 — language interaction layer
    title = "新增进展：结构化语言交互层 v0"
    claim = "当前语言层已实现单一 unresolved slot 的固定 K=2 澄清生命周期，并强制答案后使用最新观测重规划。"
    evidence = "reports/structured_language_interaction_v0/IMPLEMENTATION_REPORT.md; driveclarify_language/structured_interaction.py"
    slide = base_slide(prs, title, "Current Implementation", "IMPLEMENTED OFFLINE", COLORS["green"], claim, evidence)
    labels = [
        ("Raw instruction", "+ symbolic scene"),
        ("Structured parse", "unresolved slot"),
        ("K=2 candidates", "valid + distinct"),
        ("Binary question", "one query"),
        ("Answer resolver", "no default A"),
        ("Latest replan", "old cache invalid"),
    ]
    for i, (a, b) in enumerate(labels):
        x = 0.5 + i * 2.08
        fill = COLORS["pale_purple"] if i in {2, 3, 4} else COLORS["white"]
        add_rect(slide, x, 1.45, 1.75, 1.02, fill, COLORS["line"], True)
        add_text(slide, a, x + 0.08, 1.65, 1.59, 0.26, 13, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_text(slide, b, x + 0.08, 2.0, 1.59, 0.24, 10.5, COLORS["muted"], False, align=PP_ALIGN.CENTER)
        if i < len(labels) - 1:
            add_chevron(slide, x + 1.82, 1.8, 0.18, 0.32, COLORS["purple"])
    metrics = [
        ("32", "手工模板 fixtures"),
        ("49", "语言层定向测试 PASS"),
        ("531 / 3", "相关隔离回归 pass / skip"),
        ("0", "gold leakage violation"),
    ]
    for i, (n, lab) in enumerate(metrics):
        x = 0.65 + i * 3.08
        add_rect(slide, x, 2.93, 2.75, 1.03, COLORS["white"], COLORS["line"], True)
        add_text(slide, n, x + 0.12, 3.07, 2.5, 0.38, 22, COLORS["teal"], True, align=PP_ALIGN.CENTER)
        add_text(slide, lab, x + 0.12, 3.5, 2.5, 0.25, 10.5, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    add_card(slide, "已验证的语言行为", "first / second / exact option；STILL_AMBIGUOUS；CONTRADICTORY；NO_ANSWER；EXPIRED；OUT_OF_DOMAIN；answer 后旧 plan 不可复用。", 0.65, 4.38, 6.05, 1.55, COLORS["green"], COLORS["pale_green"], 17, 12.5)
    add_card(slide, "仍不能声称", "free-form NLU、真实视觉 grounding、自然语言泛化、多轮 HRI、clarification benefit 或 paper result。", 6.95, 4.38, 5.75, 1.55, COLORS["red"], COLORS["pale_red"], 17, 12.5)
    record(title, claim, evidence, "IMPLEMENTED OFFLINE", "这是相对前几次汇报的主要增量。离线满分只说明合同和模板覆盖正确，不代表真实自然语言泛化。")

    # 12 — current architecture overview
    title = "当前仓库架构：语言、后果、生命周期与执行权已形成一条链"
    claim = "仓库已有从结构化语言交互到 M3 与 CARLA 执行权的受限实现，但不存在通用 benchmark runtime 绑定。"
    evidence = "driveclarify_language; driveclarify_m3_runtime_shadow; driveclarify_m3_minimal_core; driveclarify_m3_live_authority"
    slide = base_slide(prs, title, "Current Architecture", "CURRENT · BOUNDED", COLORS["amber"], claim, evidence)
    modules = [
        ("输入", "指令 / camera / state", COLORS["pale_cyan"]),
        ("语言交互", "parse / candidates / Q&A", COLORS["pale_purple"]),
        ("候选计划", "shared obs → SimLingo", COLORS["pale_amber"]),
        ("反事实后果", "K×K task matrix", COLORS["pale_teal"]),
        ("决策权", "ACT / ASK / WAIT", COLORS["pale_green"]),
        ("M3 生命周期", "query / lease / freshness", COLORS["white"]),
        ("执行权", "holding / candidate / baseline", COLORS["pale_red"]),
        ("CARLA", "existing PID / control", COLORS["white"]),
    ]
    for i, (mt, ms, fill) in enumerate(modules):
        x = 0.28 + i * 1.61
        add_rect(slide, x, 1.5, 1.38, 1.17, fill, COLORS["line"], True)
        add_text(slide, mt, x + 0.06, 1.69, 1.26, 0.31, 13.2, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_text(slide, ms, x + 0.06, 2.09, 1.26, 0.38, 9.6, COLORS["muted"], False, align=PP_ALIGN.CENTER)
        if i < len(modules) - 1:
            add_chevron(slide, x + 1.41, 1.9, 0.17, 0.35, COLORS["teal"])
    paths = [
        ("语言层", "driveclarify_language/structured_interaction.py", COLORS["purple"]),
        ("候选 wrapper", "driveclarify_m3_runtime_shadow/candidate_wrapper.py", COLORS["amber"]),
        ("后果 + M2B", "counterfactual_evidence.py / m2b_binding.py", COLORS["teal"]),
        ("M3 reducer", "driveclarify_m3_minimal_core/reducer.py", COLORS["cyan"]),
        ("执行 authority", "driveclarify_m3_live_authority/resolver.py", COLORS["red"]),
        ("现有 PID", "simlingo/team_code/agent_simlingo.py::control_pid", COLORS["green"]),
    ]
    for i, (pt, pp, c) in enumerate(paths):
        x = 0.55 + (i % 2) * 6.18
        y = 3.15 + (i // 2) * 0.9
        add_rect(slide, x, y, 5.92, 0.68, COLORS["white"], COLORS["line"], True)
        add_text(slide, pt, x + 0.14, y + 0.13, 1.25, 0.25, 11.5, c, True)
        add_text(slide, pp, x + 1.42, y + 0.1, 4.34, 0.35, 9.8, COLORS["muted"], False, font=MONO)
    record(title, claim, evidence, "CURRENT · BOUNDED", "这张总图展示的是仓库里已经存在的模块，不等于它们都已被绑定到 24 场景正式 runtime；后面的阻断页会指出缺口。")

    # 13 — candidate consequence detail
    title = "当前架构细节：候选计划 → 反事实后果矩阵 → M2B"
    claim = "同一 observation 上的两个候选被顺序执行，输出 route/speed 后形成 2×2 任务后果矩阵。"
    evidence = "candidate_wrapper.py; counterfactual_evidence.py; m2b_binding.py; query_value_policy.py"
    slide = base_slide(prs, title, "Current Architecture", "IMPLEMENTED · DIAGNOSTIC", COLORS["green"], claim, evidence)
    add_card(slide, "同一观测快照", "source_observation_id\nsource_frame_id\nmodel input digest", 0.55, 1.35, 2.25, 1.65, COLORS["cyan"], COLORS["pale_cyan"], 17, 12)
    add_chevron(slide, 2.92, 1.9, 0.3, 0.45, COLORS["teal"])
    add_card(slide, "Candidate A / B", "顺序 forward\nroute [20×2]\nspeed waypoints [10×2]\ncandidate PID = 0", 3.28, 1.35, 2.65, 1.65, COLORS["amber"], COLORS["pale_amber"], 17, 11.5)
    add_chevron(slide, 6.04, 1.9, 0.3, 0.45, COLORS["teal"])
    add_card(slide, "语义映射", "route / stop task evidence\nUNKNOWN 保留\n不推断 collision/TTC", 6.4, 1.35, 2.55, 1.65, COLORS["purple"], COLORS["pale_purple"], 17, 11.5)
    add_chevron(slide, 9.07, 1.9, 0.3, 0.45, COLORS["teal"])
    add_card(slide, "M2B", "expected task loss\nquery value\nwait value\nhard gates", 9.43, 1.35, 3.08, 1.65, COLORS["green"], COLORS["pale_green"], 17, 11.5)
    add_text(slide, "Counterfactual matrix  C[a, z]", 0.7, 3.43, 4.1, 0.38, 18, COLORS["navy"], True)
    matrix = [
        ["action / hypothesis", "z = A", "z = B"],
        ["a = A", "PASS / 0", "FAIL / 1"],
        ["a = B", "FAIL / 1", "PASS / 0"],
    ]
    add_table(slide, matrix, 0.7, 3.9, 5.15, 1.72, [1.8, 1, 1], 12)
    add_card(slide, "严格边界", "这类矩阵可表征“走错目标”的任务代价；它不是物理碰撞、安全或乘客收益证据。", 6.3, 3.65, 6.2, 1.15, COLORS["red"], COLORS["pale_red"], 17, 13)
    add_card(slide, "M2B 已有离线证据", "冻结 balanced primary core macro-F1 = 0.969；去掉完整 counterfactual matrix 后降至 0.100。仅限 sealed offline action-level design。", 6.3, 5.0, 6.2, 1.05, COLORS["teal"], COLORS["pale_teal"], 16, 12)
    record(title, claim, evidence, "IMPLEMENTED · DIAGNOSTIC", "这里的贡献是把每个可能解释当作 hypothesis，评估每个候选 action 在这些 hypothesis 下的任务后果；但当前证据不支持把它扩展为物理安全矩阵。")

    # 14 — lifecycle and authority
    title = "当前架构细节：M3 生命周期与单一执行权"
    claim = "M3 只维护生命周期和授权语义；低层控制始终由已有 SimLingo PID 执行。"
    evidence = "driveclarify_m3_minimal_core/reducer.py; driveclarify_m3_live_authority/resolver.py; physical_wait_v0.py"
    slide = base_slide(prs, title, "Current Architecture", "IMPLEMENTED · BOUNDED LIVE", COLORS["amber"], claim, evidence)
    states = [
        ("DECISION_READY", COLORS["pale_cyan"]),
        ("QUERY_ACTIVE", COLORS["pale_amber"]),
        ("ANSWER_RECEIVED", COLORS["pale_purple"]),
        ("REPLAN_REQUIRED", COLORS["pale_red"]),
        ("RESUME_READY", COLORS["pale_green"]),
    ]
    for i, (st, fill) in enumerate(states):
        x = 0.6 + i * 2.45
        add_rect(slide, x, 1.45, 2.08, 0.76, fill, COLORS["line"], True)
        add_text(slide, st, x + 0.07, 1.68, 1.94, 0.25, 12.2, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        if i < len(states) - 1:
            add_chevron(slide, x + 2.13, 1.65, 0.22, 0.34, COLORS["teal"])
    add_text(slide, "执行权优先级", 0.72, 2.77, 2.1, 0.35, 18, COLORS["navy"], True)
    auth = [
        ("Independent safety", COLORS["red"], "最高优先级"),
        ("M3 holding", COLORS["purple"], "WAIT 期间维持当前有效闭环行为"),
        ("Candidate receipt", COLORS["amber"], "simulation-only / 0.1 s / 最多 1 tick"),
        ("Baseline control", COLORS["green"], "existing SimLingo PID"),
        ("No authority", COLORS["muted"], "fail closed"),
    ]
    for i, (a, c, desc) in enumerate(auth):
        y = 3.18 + i * 0.55
        add_rect(slide, 0.75, y, 3.45, 0.42, c, None, True)
        add_text(slide, a, 0.83, y + 0.08, 1.35, 0.2, 10.5, COLORS["white"], True, align=PP_ALIGN.CENTER)
        add_text(slide, desc, 2.3, y + 0.05, 1.72, 0.25, 9.7, COLORS["white"], False, align=PP_ALIGN.CENTER)
    add_card(slide, "WAIT ≠ 急停", "WAIT 不生成 steer / throttle / brake；维持既有 baseline PID，world 和 observation 继续推进。", 4.72, 2.85, 3.72, 1.45, COLORS["purple"], COLORS["pale_purple"], 17, 12.5)
    add_card(slide, "ASK ≠ 控制动作", "ASK 只改变 query lifecycle；physical safety UNKNOWN 仍可允许非控制 ASK，但不能授权 ACT。", 8.7, 2.85, 3.72, 1.45, COLORS["amber"], COLORS["pale_amber"], 17, 12.5)
    add_card(slide, "ACT 需要 receipt", "候选必须 fresh、绑定当前 frame/observation、通过 M3 contract，并由 resolver 选为唯一 owner。", 4.72, 4.62, 3.72, 1.32, COLORS["green"], COLORS["pale_green"], 17, 12.3)
    add_card(slide, "单 PID 不变", "受限闭环样例的 candidate ACT tick 只调用一次既有 control_pid；下一 tick 返回 baseline。", 8.7, 4.62, 3.72, 1.32, COLORS["cyan"], COLORS["pale_cyan"], 17, 12.3)
    record(title, claim, evidence, "IMPLEMENTED · BOUNDED LIVE", "老师可能会追问 DriveClarify 是否新增控制器。答案是没有：M3 和 resolver 管理授权，低层仍复用已有 SimLingo PID。")

    # 15 — ACT/ASK/WAIT mechanism
    title = "ACT / ASK / WAIT：后果、信息价值与时间窗共同决定"
    claim = "ASK/WAIT 是信息动作；ACT 才需要物理控制资格，三者共享合同门但具有不同授权条件。"
    evidence = "driveclarify_decision/query_value_policy.py; runtime_decision_authority_activation_v1/FINAL_REPORT.md"
    slide = base_slide(prs, title, "Decision Mechanism", "ACTIVATED ON CONTRACT CASES", COLORS["green"], claim, evidence)
    add_rect(slide, 4.4, 1.25, 4.5, 0.62, COLORS["navy"], None, True)
    add_text(slide, "共享门：候选有效 / matrix known / freshness / query-state / deadline", 4.58, 1.43, 4.14, 0.25, 12.2, COLORS["white"], True, align=PP_ALIGN.CENTER)
    branches = [
        (0.55, "ACT", COLORS["green"], COLORS["pale_green"], ["后果等价类，或唯一预期损失最优", "physical safety = PASS", "fresh candidate + eligible receipt"]),
        (4.63, "ASK", COLORS["amber"], COLORS["pale_amber"], ["两个有效解释且后果不同", "回答可改变最优动作", "query value > 0；无 active query"]),
        (8.71, "WAIT", COLORS["purple"], COLORS["pale_purple"], ["未来信息明确会到达", "holding capability + lease 可验证", "wait value > 0；不是 emergency stop"]),
    ]
    for x, bt, c, fill, bullets in branches:
        add_connector(slide, 6.65, 1.87, x + 1.8, 2.35, c, 2)
        add_rect(slide, x, 2.34, 3.55, 2.45, fill, c, True)
        add_text(slide, bt, x + 0.18, 2.55, 3.18, 0.5, 24, c, True, align=PP_ALIGN.CENTER)
        add_bullets(slide, bullets, x + 0.28, 3.1, 3.02, 1.38, 12.4, COLORS["ink"], c, 4)
    add_rect(slide, 3.2, 5.22, 6.95, 0.82, COLORS["pale_red"], "E8B3B7", True)
    add_text(slide, "任何硬证据缺失 / stale / inconsistent → reason-coded FALLBACK（不自动等于 full brake）", 3.38, 5.48, 6.6, 0.3, 13.2, COLORS["red"], True, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "ACTIVATED ON CONTRACT CASES", "runtime activation v1 证明 ACT、ASK、WAIT 在无手工 override 的合同 case 中自然可达；但这不等于 24 场景正式 runtime 已绑定。")

    # 16 — timeline
    title = "当前实验进展：从离线证据走到受限闭环，再到场景冻结"
    claim = "方法机制和一个闭环样例已完成；Paper MVP 正式性能评测仍停在预执行准备度。"
    evidence = "M2B/M3 reports; structured_language_interaction_v0; closed_loop_v1_queue; runtime authority; scenario freeze; E2E evaluation"
    slide = base_slide(prs, title, "Progress", "MIXED PASS / BLOCKED", COLORS["amber"], claim, evidence)
    milestones = [
        ("SimLingo 审计", "理解现有 forward / PID 边界", "PASS", COLORS["green"]),
        ("M2B sealed offline", "action-level generalization\nmacro-F1 0.969 primary core", "PASS*", COLORS["green"]),
        ("语言交互层", "structured K=2 Q&A\n49 tests", "PASS", COLORS["green"]),
        ("M3 生命周期", "offline core + replay + shadow", "PASS", COLORS["green"]),
        ("闭环原型 V1", "1 bounded CARLA episode", "PASS*", COLORS["green"]),
        ("决策权激活 V1", "ACT / ASK / WAIT contract cases", "PASS*", COLORS["green"]),
        ("场景冻结 V0", "24 scenarios / 96 configs", "PASS†", COLORS["green"]),
        ("正式 E2E V0", "0 episodes / 4 blockers", "BLOCKED", COLORS["red"]),
    ]
    for i, (mt, md, st, c) in enumerate(milestones):
        x = 0.42 + i * 1.58
        add_connector(slide, x + 0.48, 1.9, x + 0.48, 2.48, c, 3)
        add_rect(slide, x + 0.28, 1.7, 0.4, 0.4, c, None, True)
        add_text(slide, str(i + 1), x + 0.31, 1.78, 0.34, 0.2, 10, COLORS["white"], True, align=PP_ALIGN.CENTER)
        add_text(slide, mt, x, 2.62, 1.36, 0.62, 11.2, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_text(slide, md, x - 0.02, 3.27, 1.4, 0.78, 9.2, COLORS["muted"], False, align=PP_ALIGN.CENTER)
        add_rect(slide, x + 0.05, 4.18, 1.26, 0.34, c, None, True)
        add_text(slide, st, x + 0.1, 4.25, 1.16, 0.18, 9.5, COLORS["white"], True, align=PP_ALIGN.CENTER)
    add_card(slide, "* 受限 claim", "M2B 仅 sealed offline；closed-loop 仅 1 个受限样例；activation 仅合同 case。", 0.62, 5.02, 5.9, 0.94, COLORS["amber"], COLORS["pale_amber"], 15, 11.5)
    add_card(slide, "† 场景冻结不等于执行", "Stage 5 为 CPU-only static / contract calibration；CARLA launch = 0，SimLingo forward = 0。", 6.78, 5.02, 5.9, 0.94, COLORS["red"], COLORS["pale_red"], 15, 11.5)
    record(title, claim, evidence, "MIXED PASS / BLOCKED", "这条时间线要帮助老师快速判断哪些里程碑是代码合同、哪些是闭环观测、哪些还没有进入实验。")

    # 17 — closed loop evidence
    title = "受限闭环证据：ASK → WAIT → 答案 → 最新观测重规划 → 1-tick ACT"
    claim = "一个原生显示 CARLA 回合完成了完整交互与执行权链路，但不支持性能或安全提升结论。"
    evidence = "reports/closed_loop_v1_queue/03_end_to_end_demo/FINAL_REPORT.md; LIVE_INVARIANT_AUDIT.json"
    slide = base_slide(prs, title, "Progress", "ONE BOUNDED CARLA EPISODE", COLORS["amber"], claim, evidence)
    add_picture_cover(slide, FIG / "repo_closed_loop_act_tick.png", 0.5, 1.25, 7.7, 4.98)
    add_rect(slide, 0.5, 5.69, 7.7, 0.54, COLORS["navy"], None, False, 10)
    add_text(slide, "仓库真实截图：ACT_CONTROL_TICK_ACTIVE（simulation only）", 0.72, 5.82, 7.25, 0.24, 10, COLORS["white"], True)
    facts = [
        ("1", "CARLA launch / query / answer"),
        ("1.359 s", "physical WAIT；frames 2507 → 2509"),
        ("2", "post-answer fresh SimLingo forwards"),
        ("1", "candidate receipt issued / consumed"),
        ("1", "candidate control tick + existing PID invocation"),
        ("0", "old / stale candidate control writes"),
    ]
    for i, (n, lab) in enumerate(facts):
        x = 8.48 + (i % 2) * 2.08
        y = 1.37 + (i // 2) * 1.17
        add_rect(slide, x, y, 1.9, 0.95, COLORS["white"], COLORS["line"], True)
        add_text(slide, n, x + 0.08, y + 0.13, 1.74, 0.32, 18, COLORS["teal"], True, align=PP_ALIGN.CENTER)
        add_text(slide, lab, x + 0.1, y + 0.5, 1.7, 0.31, 9.3, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    add_card(slide, "必须披露的边界", "Natural M2B output = FALLBACK_RECOMMENDED；演示中的 ASK 是 bounded lifecycle entry，不得表述为“该回合自然 M2B ASK”。", 8.45, 4.95, 4.25, 1.2, COLORS["red"], COLORS["pale_red"], 15.5, 11.5)
    record(title, claim, evidence, "ONE BOUNDED CARLA EPISODE", "这是目前最强的闭环工程证据：世界继续推进、回答后旧候选失效、用最新观测重规划、单 tick candidate authority 后返回 baseline。它仍然只是一个样例。")

    # 18 — benchmark freeze
    title = "冻结场景基准：24 场景、96 seed 配置，标签与运行时严格隔离"
    claim = "场景集和评价协议已冻结，但当前仅完成静态验证与合同级校准，未生成 CARLA episode。"
    evidence = "paper_mvp_scenario_freeze_v0/SCENARIO_CATALOG.json; VALIDATION_REPORT.json; CALIBRATION_REPORT.json"
    slide = base_slide(prs, title, "Experimental Design", "FROZEN · NOT EXECUTED", COLORS["amber"], claim, evidence)
    stats = [("24", "scenarios"), ("4", "seeds / scenario"), ("96", "runtime configs"), ("8 / 8 / 8", "ACT / ASK / WAIT")]
    for i, (n, lab) in enumerate(stats):
        x = 0.52 + i * 3.1
        add_rect(slide, x, 1.3, 2.8, 1.15, COLORS["white"], COLORS["line"], True)
        add_text(slide, n, x + 0.12, 1.49, 2.56, 0.42, 23, COLORS["teal"], True, align=PP_ALIGN.CENTER)
        add_text(slide, lab, x + 0.12, 1.98, 2.56, 0.25, 10.8, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    add_text(slide, "歧义类型（每类 6）", 0.75, 2.9, 3.1, 0.35, 18, COLORS["navy"], True)
    amb = [("referential", COLORS["cyan"]), ("spatial", COLORS["green"]), ("temporal", COLORS["amber"]), ("underspecified constraint", COLORS["purple"])]
    for i, (a, c) in enumerate(amb):
        y = 3.38 + i * 0.58
        add_rect(slide, 0.78, y, 4.2, 0.42, c, None, True)
        add_text(slide, f"{a} · 6", 0.96, y + 0.08, 3.82, 0.2, 11.2, COLORS["white"], True, align=PP_ALIGN.CENTER)
    split = [
        ["split", "scenario", "episode config", "towns"],
        ["TRAIN", "8", "32", "Town03/04/05/06"],
        ["DEV", "8", "32", "Town02/07"],
        ["TEST", "8", "32", "Town01/10HD"],
    ]
    add_table(slide, split, 5.35, 2.98, 7.2, 2.55, [1, 1, 1.3, 2.4], 11.5)
    add_card(slide, "annotation ≠ runtime output", "scenario annotation 是人工审计记录；policy 只能看到 instruction + 正常 sensor / route context，expected label 在 episode 完成后私有 join。", 5.35, 5.65, 7.2, 0.58, COLORS["red"], COLORS["pale_red"], 14.5, 10.5)
    record(title, claim, evidence, "FROZEN · NOT EXECUTED", "96 是计划配置数，不是完成回合数。冻结报告明确 CARLA launch、SimLingo forward、benchmark run 都为零。")

    # 19 — experiment design
    title = "正式实验设计：8 种方法在 96 个相同配置上配对比较"
    claim = "计划形成 768 个 matched method episodes；当前 baseline 合同尚未冻结为可执行状态。"
    evidence = "paper_mvp_end_to_end_evaluation_v0/RESULTS.json; BASELINE_RESULTS.json"
    slide = base_slide(prs, title, "Experimental Design", "SCHEDULED · NOT STARTED", COLORS["amber"], claim, evidence)
    flow = [("CARLA", "24 × 4"), ("SimLingo", "shared environment"), ("Method", "8 policies"), ("Decision", "ACT/ASK/WAIT"), ("Control", "same evaluator")]
    for i, (ft, fs) in enumerate(flow):
        x = 0.65 + i * 2.47
        add_rect(slide, x, 1.32, 2.03, 0.92, COLORS["white"], COLORS["line"], True)
        add_text(slide, ft, x + 0.08, 1.5, 1.87, 0.3, 15.5, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_text(slide, fs, x + 0.08, 1.87, 1.87, 0.22, 10, COLORS["muted"], False, align=PP_ALIGN.CENTER)
        if i < len(flow) - 1:
            add_chevron(slide, x + 2.07, 1.6, 0.24, 0.36, COLORS["teal"])
    baselines = [
        ["方法", "当前冻结状态", "计划配置"],
        ["Original SimLingo", "MISSING", "96"],
        ["Never Ask", "metadata only / disabled", "96"],
        ["Always Ask", "metadata only / disabled", "96"],
        ["Always Wait", "metadata only / disabled", "96"],
        ["Always Stop", "metadata only / disabled", "96"],
        ["Language-only uncertainty", "threshold 未冻结", "96"],
        ["Risk-only", "threshold 未冻结", "96"],
        ["DriveClarify", "MISSING", "96"],
    ]
    add_table(slide, baselines, 0.7, 2.72, 7.6, 3.42, [2.6, 3.4, 1], 9.8)
    add_card(slide, "设计总量", "96 runtime configurations × 8 methods = 768 planned episodes", 8.65, 2.86, 3.95, 1.17, COLORS["teal"], COLORS["pale_teal"], 17, 14)
    add_card(slide, "当前观测", "started = 0\ncompleted = 0\nTEST consumed = 0", 8.65, 4.25, 3.95, 1.25, COLORS["red"], COLORS["pale_red"], 18, 14)
    record(title, claim, evidence, "SCHEDULED · NOT STARTED", "这页明确区分 schedule 和 observations。baseline 表里出现了方法名字，但大多只是 metadata-only；Original SimLingo 和 DriveClarify 甚至缺 manifest 记录。")

    # 20 — metrics
    title = "评价指标：定义已就绪，数值均尚未测得"
    claim = "由于 0 completed episodes，所有率、召回、时延和安全指标应显示 unavailable，而不是 0。"
    evidence = "paper_mvp_end_to_end_evaluation_v0/METRICS.json; FINAL_REPORT.md"
    slide = base_slide(prs, title, "Experimental Design", "METRICS = NULL", COLORS["red"], claim, evidence)
    groups = [
        ("Decision", ["ACT accuracy", "ASK recall", "WAIT recall", "macro-F1"], COLORS["cyan"]),
        ("Driving", ["success rate", "collision", "route completion", "route failure"], COLORS["green"]),
        ("Interaction", ["query count", "unnecessary query", "answer delay", "completion time"], COLORS["amber"]),
        ("Safety", ["rule violation", "unsafe/unresolved ACT", "control ownership", "stale write"], COLORS["red"]),
    ]
    for i, (gt, items, c) in enumerate(groups):
        x = 0.55 + i * 3.12
        add_rect(slide, x, 1.35, 2.85, 3.4, COLORS["white"], COLORS["line"], True)
        add_rect(slide, x, 1.35, 2.85, 0.55, c, None, True)
        add_text(slide, gt, x + 0.12, 1.5, 2.61, 0.24, 17, COLORS["white"], True, align=PP_ALIGN.CENTER)
        add_bullets(slide, items, x + 0.18, 2.12, 2.5, 1.86, 12.2, COLORS["ink"], c, 8)
        add_rect(slide, x + 0.42, 4.18, 2.0, 0.38, COLORS["pale_red"], COLORS["red"], True)
        add_text(slide, "UNAVAILABLE", x + 0.52, 4.27, 1.8, 0.18, 10.5, COLORS["red"], True, align=PP_ALIGN.CENTER)
    add_card(slide, "为什么不能画零柱状图？", "零会暗示“测量值为 0”；当前真实语义是 denominator = 0，computed = false，metric = null。", 1.1, 5.18, 5.45, 0.95, COLORS["red"], COLORS["pale_red"], 16, 12.3)
    add_card(slide, "何时可计算？", "完成 annotation-free runtime、episode terminal record 与 post-episode private label join 后。", 6.82, 5.18, 5.45, 0.95, COLORS["teal"], COLORS["pale_teal"], 16, 12.3)
    record(title, claim, evidence, "METRICS = NULL", "这页能防止在汇报中误用空 confusion matrix。当前 METRICS.json 的全零 count table 明确标了 computed=false。")

    # 21 — blockers
    title = "当前真实瓶颈：Paper MVP 正式评测在启动前被四项准备度阻断"
    claim = "阻断属于环境、方法、集成与评价设计准备度，不是负面模型结果。"
    evidence = "paper_mvp_end_to_end_evaluation_v0/FINAL_REPORT.md; FAILURE_ANALYSIS.md"
    slide = base_slide(prs, title, "Current Status", "BLOCKED PRE-EXECUTION", COLORS["red"], claim, evidence)
    blockers = [
        ("B1 · CARLA fixtures", "24 条 catalog 记录只是 metadata/contract fixture；12 个源 XML 无 scenario element。", "授权并编写 24 个物理 CARLA 场景", COLORS["red"]),
        ("B2 · runtime candidates", "live path 仍是固定的一次性 instruction/A/B demo；无 annotation-free 候选路径覆盖 24 场景。", "实现并冻结 live grounding/candidate path", COLORS["orange"]),
        ("B3 · authority binding", "Activation V1 在合同 fixture 上 PASS，但尚未被 live catalog runtime / postprocessor 调用。", "绑定 activation 到实际运行路径", COLORS["amber"]),
        ("B4 · baselines", "7 个 baseline metadata disabled；缺 Original SimLingo / DriveClarify，language/risk threshold 未冻结。", "实现并冻结完整 8-method reducers", COLORS["purple"]),
    ]
    for i, (bt, body, next_step, c) in enumerate(blockers):
        x = 0.55 + (i % 2) * 6.15
        y = 1.3 + (i // 2) * 2.32
        add_rect(slide, x, y, 5.85, 1.95, COLORS["white"], c, True)
        add_text(slide, bt, x + 0.2, y + 0.17, 5.45, 0.35, 18, c, True)
        add_text(slide, body, x + 0.2, y + 0.62, 5.45, 0.62, 12.5, COLORS["ink"], False)
        add_rect(slide, x + 0.2, y + 1.37, 5.45, 0.38, c, None, True)
        add_text(slide, "NEXT · " + next_step, x + 0.31, y + 1.46, 5.22, 0.18, 10.3, COLORS["white"], True, align=PP_ALIGN.CENTER)
    add_text(slide, "结论：继续画性能曲线没有科学意义；下一步应先完成 implementation-and-freeze stage。", 0.72, 6.05, 11.9, 0.36, 12.8, COLORS["red"], True, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "BLOCKED PRE-EXECUTION", "这页是最新状态相对原始提示的重要纠正：不是“Paper MVP ready”，而是场景冻结完成、正式 E2E 预执行阻断。")

    # 22 — future ambiguity/language
    title = "FUTURE EXTENSION 1：自动歧义发现与语言级推理"
    claim = "仓库有离线规则原型，但未实现真实视觉 grounding、开放语言或 live Paper MVP 集成。"
    evidence = "ambiguity_discovery_prototype_v0/FINAL_REPORT.md; LIMITATIONS.md; driveclarify_ambiguity_discovery_v0"
    slide = base_slide(prs, title, "Future Extension", "NOT IMPLEMENTED IN LIVE PIPELINE", COLORS["red"], claim, evidence)
    add_text(slide, "当前 precursor（受限）", 0.62, 1.28, 5.5, 0.4, 20, COLORS["green"], True)
    current = [("Instruction", "英文关键词/有限 noun phrase"), ("Symbolic entities", "人工结构化场景表"), ("Rule retrieval", "兼容实体 ≥ 2"), ("Candidates", "symbolic binding")]
    for i, (a, b) in enumerate(current):
        x = 0.55 + i * 1.55
        add_rect(slide, x, 1.88, 1.32, 1.04, COLORS["pale_green"], "B9DCCE", True)
        add_text(slide, a, x + 0.05, 2.08, 1.22, 0.25, 11, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_text(slide, b, x + 0.05, 2.42, 1.22, 0.3, 8.7, COLORS["muted"], False, align=PP_ALIGN.CENTER)
        if i < 3:
            add_chevron(slide, x + 1.35, 2.2, 0.16, 0.3, COLORS["green"])
    add_card(slide, "原型诊断结果", "30 手工样例；accuracy / candidate recall = 1.0。仅限同一规则覆盖集；所有 consequence handoff 因无 plan evidence 而 fail-closed UNKNOWN。", 0.55, 3.2, 6.0, 1.48, COLORS["green"], COLORS["pale_green"], 17, 12.5)
    add_text(slide, "未来目标（未实现）", 6.9, 1.28, 3.1, 0.4, 20, COLORS["red"], True)
    future = [
        ("开放指令", "free-form / multilingual"),
        ("视觉 grounding", "detector + tracking + map"),
        ("ambiguity detector", "calibrated abstention"),
        ("dynamic candidates", "N candidates + clustering"),
        ("DriveClarify", "consequence-aware selection"),
    ]
    for i, (a, b) in enumerate(future):
        y = 1.85 + i * 0.78
        add_rect(slide, 7.0, y, 5.5, 0.59, COLORS["pale_red"], COLORS["red"], True)
        add_text(slide, a, 7.15, y + 0.1, 1.7, 0.24, 11.8, COLORS["red"], True)
        add_text(slide, b, 8.95, y + 0.08, 3.3, 0.28, 10.5, COLORS["muted"], False)
    add_card(slide, "研究问题", "能否在不泄漏 oracle intent 的前提下，保持 rare but safety-critical interpretation recall，并控制多候选 forward 成本？", 0.55, 4.95, 6.0, 1.12, COLORS["purple"], COLORS["pale_purple"], 16, 12.5)
    record(title, claim, evidence, "NOT IMPLEMENTED IN LIVE PIPELINE", "自动歧义发现不能简单写成未来完全没有：有一个离线规则 precursor，但它不看真实图像、不跑 SimLingo、不做控制，也没有自然语言泛化。")

    # 23 — temporal clarification
    title = "FUTURE EXTENSION 2：时间感知澄清智能"
    claim = "当前有 query value、deadline 与 holding 合同；基于车辆动态的实时到达时序推理仍未实现。"
    evidence = "query_value_policy.py; physical_wait_v0.py; driveclarify_learning_architecture_v1/DYNAMIC_CANDIDATE_ROADMAP.md"
    slide = base_slide(prs, title, "Future Extension", "FUTURE · NOT IMPLEMENTED", COLORS["red"], claim, evidence)
    add_rect(slide, 0.65, 1.34, 4.05, 4.72, COLORS["pale_green"], "B9DCCE", True)
    add_text(slide, "当前已有合同", 0.95, 1.63, 3.45, 0.42, 21, COLORS["green"], True, align=PP_ALIGN.CENTER)
    add_bullets(slide, [
        "query value 与 no-answer / delay channel",
        "decision_deadline_monotonic",
        "expected_information_arrival_time",
        "holding lease / max duration / reevaluation interval",
        "WAIT 维持当前有效 closed-loop behavior",
        "late answer → old candidate invalidation",
    ], 1.0, 2.22, 3.35, 2.85, 13.2, COLORS["ink"], COLORS["green"], 10)
    add_rect(slide, 4.95, 1.34, 7.72, 4.72, COLORS["pale_red"], "E9B9BD", True)
    add_text(slide, "未来动态模型（未实现）", 5.28, 1.63, 7.05, 0.42, 21, COLORS["red"], True, align=PP_ALIGN.CENTER)
    dims = [("remaining distance", "到不可逆承诺点距离"), ("vehicle speed", "速度与制动/转向可达域"), ("communication delay", "乘客响应分布"), ("decision time", "推理与候选 forward 延迟")]
    for i, (a, b) in enumerate(dims):
        x = 5.25 + (i % 2) * 3.52
        y = 2.35 + (i // 2) * 1.22
        add_card(slide, a, b, x, y, 3.25, 0.95, COLORS["red"], COLORS["white"], 14.2, 10.8)
    add_rect(slide, 5.45, 4.93, 6.72, 0.72, COLORS["navy"], None, True)
    add_text(slide, "ASK only if  P(answer arrives before commitment)  is high enough", 5.72, 5.15, 6.18, 0.27, 13.5, COLORS["white"], True, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "FUTURE · NOT IMPLEMENTED", "当前 WAIT 合同已经显式写出 deadline 和信息到达时间，但这些大多来自受控/合同 fixture；未来需要从车辆速度、距离和通信延迟实时估计。")

    # 24 — final architecture
    title = "最终期望架构：当前模块用实线，未来扩展用虚线"
    claim = "最终系统将自动歧义意识、语言 grounding、反事实后果与时间澄清统一到闭环 ACT/ASK/WAIT。"
    evidence = "architecture_v0 design + current repository implementation + explicitly marked future extensions"
    slide = base_slide(prs, title, "Final Vision", "TARGET ARCHITECTURE", COLORS["purple"], claim, evidence)
    modules = [
        ("自然语言", "current input", False, COLORS["pale_cyan"]),
        ("自动歧义意识", "FUTURE", True, COLORS["pale_red"]),
        ("视觉/语言 grounding", "FUTURE", True, COLORS["pale_red"]),
        ("候选生成", "current fixed / FUTURE dynamic", True, COLORS["pale_amber"]),
        ("反事实后果", "current bounded", False, COLORS["pale_teal"]),
        ("时间澄清", "FUTURE dynamic", True, COLORS["pale_purple"]),
        ("ACT / ASK / WAIT", "current contract", False, COLORS["pale_green"]),
        ("闭环驾驶", "current one bounded demo", False, COLORS["white"]),
    ]
    for i, (a, b, future, fill) in enumerate(modules):
        x = 0.38 + i * 1.61
        sh = add_rect(slide, x, 2.0, 1.38, 1.38, fill, COLORS["red"] if future else COLORS["line"], True)
        if future:
            # XML-level dash is unnecessary in the deck; a clear FUTURE badge carries the status.
            add_rect(slide, x + 0.17, 1.83, 1.04, 0.28, COLORS["red"], None, True)
            add_text(slide, "FUTURE", x + 0.22, 1.88, 0.94, 0.15, 8.5, COLORS["white"], True, align=PP_ALIGN.CENTER)
        add_text(slide, a, x + 0.07, 2.25, 1.24, 0.5, 12.4, COLORS["navy"], True, align=PP_ALIGN.CENTER)
        add_text(slide, b, x + 0.07, 2.89, 1.24, 0.25, 8.7, COLORS["muted"], False, align=PP_ALIGN.CENTER)
        if i < len(modules) - 1:
            add_chevron(slide, x + 1.41, 2.5, 0.16, 0.35, COLORS["teal"])
    add_card(slide, "当前可主张", "结构化语言交互、候选后果、query/wait value、M3 生命周期、受限执行权与一个 CARLA 闭环样例。", 0.7, 4.3, 5.8, 1.28, COLORS["green"], COLORS["pale_green"], 17, 12.8)
    add_card(slide, "最终论文目标", "证明 consequence-aware clarification 在必要 ASK、避免不必要 ASK、WAIT 选择和闭环驾驶代价之间形成更优 trade-off。", 6.82, 4.3, 5.8, 1.28, COLORS["purple"], COLORS["pale_purple"], 17, 12.8)
    add_text(slide, "虚线/红色 FUTURE 模块均为 NOT IMPLEMENTED。", 3.9, 5.9, 5.5, 0.28, 11.8, COLORS["red"], True, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "TARGET ARCHITECTURE", "最终愿景不是另起炉灶，而是在已有后果—决策—执行权链条前补齐自动歧义发现与真实 grounding，在决策前补齐动态时间模型。")

    # 25 — roadmap
    title = "研究路线图：先解除四项阻断，再做一次可信的 TEST"
    claim = "下一步优先级是 implementation-and-freeze，而不是继续训练、画空指标或消费 TEST。"
    evidence = "paper_mvp_end_to_end_evaluation_v0/FINAL_REPORT.md; scenario freeze split/firewall contracts"
    slide = base_slide(prs, title, "Roadmap", "NEXT ACTIONS", COLORS["teal"], claim, evidence)
    phases = [
        ("NOW", "实现 24 个 CARLA fixtures\n绑定 annotation-free candidates\n绑定 activation 到 live runtime", COLORS["red"]),
        ("FREEZE", "冻结 8 个可执行方法\n固定 language/risk thresholds\npin checkpoint + probe hook", COLORS["amber"]),
        ("TRAIN / DEV", "预执行与 smoke\n仅 TRAIN/DEV 调试\n完整 evidence index", COLORS["cyan"]),
        ("TEST ONCE", "冻结后一次性运行 TEST\nprivate label join\n不回看调参", COLORS["green"]),
        ("PAPER", "统计分析 / ablation\nfailure atlas / limits\n写作与图表", COLORS["purple"]),
        ("EXTENSIONS", "自动歧义发现\n开放语言 grounding\n动态时间澄清", COLORS["muted"]),
    ]
    for i, (ph, body, c) in enumerate(phases):
        x = 0.42 + i * 2.12
        add_rect(slide, x, 1.55, 1.86, 3.55, COLORS["white"], c, True)
        add_rect(slide, x, 1.55, 1.86, 0.58, c, None, True)
        add_text(slide, ph, x + 0.08, 1.72, 1.7, 0.22, 13.2, COLORS["white"], True, align=PP_ALIGN.CENTER)
        add_text(slide, body, x + 0.14, 2.42, 1.58, 1.72, 11.4, COLORS["ink"], False, align=PP_ALIGN.CENTER)
        if i < len(phases) - 1:
            add_chevron(slide, x + 1.89, 3.0, 0.18, 0.38, COLORS["teal"])
    add_rect(slide, 0.7, 5.42, 11.95, 0.68, COLORS["navy"], None, True)
    add_text(slide, "Supervisor takeaway：语言交互层已补上；当前最关键科学工作是把它绑定到真实场景，并完成可比较的闭环证据。", 0.98, 5.62, 11.4, 0.25, 13, COLORS["white"], True, align=PP_ALIGN.CENTER)
    add_text(slide, "完整引用、图源与 claim-evidence 映射见随附文件。", 3.9, 6.22, 5.5, 0.25, 10.5, COLORS["muted"], False, align=PP_ALIGN.CENTER)
    record(title, claim, evidence, "NEXT ACTIONS", "建议汇报最后落到一个明确请求：授权 implementation-and-freeze stage。只有解除四项阻断后，正式指标才有科学意义。")

    output = OUT / "DriveClarify_研究进展汇报_V1_中文.pptx"
    prs.save(output)
    return output, records, notes


def write_literature_comparison() -> None:
    rows = [
        ["Method", "Year", "Category", "Language", "Ambiguity detection", "Consequence comparison", "ASK", "WAIT", "Closed-loop driving", "Evidence-based gap", "Primary source"],
        ["SimLingo", "2025", "Driving VLA", "Yes", "Not reported", "Not reported", "No", "No", "Yes", "Language-action alignment but no explicit multi-interpretation clarification policy", "https://openaccess.thecvf.com/content/CVPR2025/html/Renz_SimLingo_Vision-Only_Closed-Loop_Autonomous_Driving_with_Language-Action_Alignment_CVPR_2025_paper.html"],
        ["LMDrive", "2024", "Driving LLM", "Yes", "Not reported", "Not reported", "No", "No", "Yes", "Language-guided closed loop; no active clarification mechanism reported", "https://openaccess.thecvf.com/content/CVPR2024/html/Shao_LMDrive_Closed-Loop_End-to-End_Driving_with_Large_Language_Models_CVPR_2024_paper.html"],
        ["DriveGPT4", "2023", "Interpretable driving MLLM", "Yes", "Not reported", "Not reported", "No", "No", "No formal closed-loop benchmark in cited paper", "Video QA/control prediction; human questions are explanatory rather than a clarification policy", "https://arxiv.org/abs/2310.01412"],
        ["Talk2Car", "2019", "Referring-expression grounding", "Yes", "No runtime ASK", "No", "No", "No", "No", "Grounds the referred object but does not evaluate whether alternative groundings change driving consequences", "https://aclanthology.org/D19-1215/"],
        ["KnowNo", "2023", "Uncertainty-aware robot planning", "Yes", "Yes", "Task-plan level", "Yes", "No driving WAIT", "No", "Calibrates when to ask for help; no closed-loop vehicle dynamics/authority", "https://arxiv.org/abs/2307.01928"],
        ["CLARA", "2023", "Interactive robot clarification", "Yes", "Yes", "No vehicle consequences", "Yes", "No", "No", "Classifies clear/ambiguous/infeasible and asks questions outside closed-loop driving", "https://arxiv.org/abs/2306.10376"],
        ["DriveClarify (current repo)", "2026", "Consequence-aware driving clarification", "Bounded structured", "Offline prototype only", "Yes, symbolic/diagnostic and bounded demo", "Yes, contract-level", "Yes, contract/bounded live", "One bounded CARLA episode", "Formal 24-scenario evaluation blocked pre-execution; free-form grounding not implemented", "Repository evidence; see claim_evidence_matrix.csv"],
    ]
    with (OUT / "literature_comparison.csv").open("w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)


def write_citations() -> None:
    text = """# DriveClarify 研究进展汇报：引用清单

## 外部文献

1. Katrin Renz, Long Chen, Elahe Arani, and Oleg Sinavski. **SimLingo: Vision-Only Closed-Loop Autonomous Driving with Language-Action Alignment.** CVPR 2025, pp. 11993–12003. https://openaccess.thecvf.com/content/CVPR2025/html/Renz_SimLingo_Vision-Only_Closed-Loop_Autonomous_Driving_with_Language-Action_Alignment_CVPR_2025_paper.html
2. Hao Shao, Yuxuan Hu, Letian Wang, Guanglu Song, Steven L. Waslander, Yu Liu, and Hongsheng Li. **LMDrive: Closed-Loop End-to-End Driving with Large Language Models.** CVPR 2024, pp. 15120–15130. https://openaccess.thecvf.com/content/CVPR2024/html/Shao_LMDrive_Closed-Loop_End-to-End_Driving_with_Large_Language_Models_CVPR_2024_paper.html
3. Zhenhua Xu et al. **DriveGPT4: Interpretable End-to-end Autonomous Driving via Large Language Model.** arXiv:2310.01412, 2023. https://arxiv.org/abs/2310.01412
4. Thierry Deruyttere, Simon Vandenhende, Dusan Grujicic, Luc Van Gool, and Marie-Francine Moens. **Talk2Car: Taking Control of Your Self-Driving Car.** EMNLP-IJCNLP 2019, pp. 2088–2098. https://aclanthology.org/D19-1215/
5. Allen Z. Ren et al. **Robots That Ask For Help: Uncertainty Alignment for Large Language Model Planners.** CoRL 2023. https://arxiv.org/abs/2307.01928
6. Jeongeun Park et al. **CLARA: Classifying and Disambiguating User Commands for Reliable Interactive Robotic Agents.** arXiv:2306.10376, 2023. https://arxiv.org/abs/2306.10376

## 主要仓库证据

- `reports/structured_language_interaction_v0/IMPLEMENTATION_REPORT.md`
- `reports/runtime_decision_authority_activation_v1/FINAL_REPORT.md`
- `reports/closed_loop_v1_queue/03_end_to_end_demo/FINAL_REPORT.md`
- `reports/closed_loop_v1_queue/03_end_to_end_demo/LIVE_INVARIANT_AUDIT.json`
- `reports/paper_mvp_scenario_freeze_v0/FINAL_REPORT.md`
- `reports/paper_mvp_scenario_freeze_v0/VALIDATION_REPORT.json`
- `reports/paper_mvp_end_to_end_evaluation_v0/FINAL_REPORT.md`
- `reports/paper_mvp_end_to_end_evaluation_v0/FAILURE_ANALYSIS.md`
- `reports/m2b_r3_post_blind_readonly_analysis/DC-M2B-R3-POSTBLIND-20260804T165508Z/CLAIM_BOUNDARY.md`
- `reports/m3_lifecycle_aware_nonblind_capture_replay_shadow/DC-M3-LIFECYCLE-MILESTONE-20260805T144406Z/CLAIM_BOUNDARY.md`
- `reports/ambiguity_discovery_prototype_v0/FINAL_REPORT.md`
- `reports/ambiguity_discovery_prototype_v0/LIMITATIONS.md`
"""
    (OUT / "citation_list.md").write_text(text, encoding="utf-8")


def write_figure_sources() -> None:
    text = f"""# Figure source list

| Slide | Local asset | Source | Original figure | Use/status |
|---:|---|---|---|---|
| 1, 4 | `figures/{HERO.name}` | Built-in ImageGen / gpt-image-2 workflow | N/A | Illustrative only; not experimental evidence |
| 5 | `figures/paper_simlingo_fig2.png` | SimLingo, CVPR 2025 official paper | Figure 2 | Original paper crop; not redrawn |
| 5 | `figures/paper_lmdrive_fig4.png` | LMDrive, CVPR 2024 official paper | Figure 4 | Original paper crop; not redrawn |
| 5 | `figures/paper_drivegpt4_fig2.png` | DriveGPT4, arXiv:2310.01412 | Figure 2 | Original paper crop; not redrawn |
| 6 | `figures/paper_talk2car_fig1.png` | Talk2Car, EMNLP-IJCNLP 2019 | Figure 1 | Original paper crop; not redrawn |
| 7 | `figures/paper_knowno_fig1.png` | KnowNo, CoRL 2023 / arXiv:2307.01928 | Figure 1 | Original paper crop; not redrawn |
| 17 | `figures/repo_closed_loop_act_tick.png` | DriveClarify repository | ACT_CONTROL_TICK_ACTIVE screenshot | Primary repository evidence |

## ImageGen prompt

Use case: scientific-educational. Asset: 16:9 academic research presentation hero. Create a restrained photorealistic autonomous-driving ambiguity scene with two visually similar white delivery vans and two subtle cyan/amber candidate trajectory ribbons. No text, logos, watermark, crash, or impossible road geometry. The asset is illustrative and is never used as experimental evidence.

## Separate architecture figures

- `figures/fig_01_language_interaction_layer.svg`
- `figures/fig_02_current_repository_architecture.svg`
- `figures/fig_03_act_ask_wait_decision.svg`
- `figures/fig_04_experiment_design.svg`
- `figures/fig_05_final_expected_architecture.svg`

PNG renders with matching stems are generated from the SVG files for convenient reuse.
"""
    (OUT / "figure_sources.md").write_text(text, encoding="utf-8")


def write_records(records: list[dict[str, str]], notes: list[dict[str, str]]) -> None:
    with (OUT / "claim_evidence_matrix.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["slide", "title", "claim", "evidence", "status"])
        writer.writeheader()
        writer.writerows(records)
    lines = ["# DriveClarify 研究进展汇报 V1：逐页讲稿", ""]
    for row in notes:
        lines.append(f"## {int(row['slide']):02d}. {row['title']}")
        lines.append("")
        lines.append(row["note"])
        lines.append("")
    (OUT / "DriveClarify_研究进展汇报_V1_逐页讲稿.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    prepare_assets()
    emit_architecture_svgs()
    pptx_path, records, notes = generate_deck()
    write_literature_comparison()
    write_citations()
    write_figure_sources()
    write_records(records, notes)
    print(pptx_path)


if __name__ == "__main__":
    main()
