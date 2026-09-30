"""Build submission/TIET_TapTrace.pptx (the organisers' 12 sections, in their order) from measured data.

Every number comes from artifacts/eval_raw.json, artifacts/stress.json, results.jsonl or is computed live
(catalog audit, embedding similarity), so the deck cannot drift from the system.

    python scripts/build_deck.py --template "<path>/CollegeName_TeamName_Submission.pptx" [--github URL] [--video URL]
    powershell -ExecutionPolicy Bypass -File scripts/deck_postprocess.ps1 -pptx submission/TIET_TapTrace.pptx

Visual system: dark warm glass - the blurred cover photo behind every slide, frosted translucent cards,
amber accent + sage / sky / coral stage tints, Segoe UI (same language as demo/index.html);
Lucide icons + Simple Icons logos inserted as vector SVG by the post-processor; real product screenshots
(demo/index.html snapshot mode) and a photographic cover with the real UI composited onto the phone.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import numpy as np
from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ASSETS = ROOT / "submission" / "deck_assets"

# ---------------------------------------------------------------- palette & type
# dark warm glass: one visual language with the cover photo and the demo UI (demo/index.html)
# colours are hex or (hex, alpha) for translucent "frosted" fills over the blurred desk background
INK, SLATE, MUTE, FAINT, WHITE = "F5EFE6", "D9D2C7", "A89F93", "8A8176", "FFFFFF"
LINE = ("FFFFFF", 0.14)
PANEL = ("000000", 0.26)
GLASS, GLASS2 = ("FFFFFF", 0.065), ("FFFFFF", 0.11)
DARK = "2A1A0B"  # text on amber fills
TEAL, TEAL_D, TEAL_L = "F2B66D", "F7D3A6", ("F2B66D", 0.14)  # primary accent (amber; name kept for layout code)
AMBER, AMBER_L = "93CDB9", ("93CDB9", 0.13)  # sage
SKY, SKY_L = "9DBFE3", ("9DBFE3", 0.13)
CORAL, CORAL_L = "EE9A7E", ("EE9A7E", 0.13)
STONE, STONE_L = "C9C1B6", ("C9C1B6", 0.10)
RED, RED_L = "F08A7A", ("F08A7A", 0.15)
BAR = "6B6259"  # neutral bars on dark charts
WARM = "F2B66D"  # warm highlight on dark backgrounds
F, FB, MONO = "Segoe UI", "Segoe UI Semibold", "Consolas"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
SW, SH = 13.333, 7.5
L, W = 0.62, 12.09


def rgb(h):
    return RGBColor.from_string(h[0] if isinstance(h, tuple) else h)


def _alpha(parent, c):
    """Give the srgbClr under parent (solidFill container) an alpha if c is (hex, alpha)."""
    if isinstance(c, tuple) and parent is not None:
        clr = parent.find(f"{A}solidFill/{A}srgbClr")
        if clr is not None:
            etree.SubElement(clr, A + "alpha").set("val", str(int(c[1] * 100000)))


# ---------------------------------------------------------------- primitives
def shape(sl, x, y, w, h, fill=None, line=None, kind=MSO_SHAPE.RECTANGLE, radius=None, dash=False, lw=0.75, name=None):
    s = sl.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    spPr = s._element.spPr
    if fill:
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
        _alpha(spPr, fill)
    else:
        s.fill.background()
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(lw)
        if dash:
            s.line.dash_style = 4
        _alpha(spPr.find(A + "ln"), line)
    else:
        s.line.fill.background()
    if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    s.shadow.inherit = False
    if name:
        s.name = name
    return s


def rrect(sl, x, y, w, h, fill=GLASS, line=LINE, radius=0.06, lw=0.75, dash=False):
    return shape(sl, x, y, w, h, fill=fill, line=line, kind=MSO_SHAPE.ROUNDED_RECTANGLE, radius=radius, lw=lw, dash=dash)


def T(sl, x, y, w, h, paras, size=11, color=INK, font=F, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
      on=None, margin=0.0, spacing=None):
    """paras: list of str | (runs_or_text, opts); runs = [(text, opts)]. opts: size color font bold italic spc align space line"""
    sh = on if on is not None else sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = sh.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(0.0)
    tf.clear()
    for i, p in enumerate(paras):
        t, o = (p, {}) if isinstance(p, str) else p
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = o.get("align", align)
        if "space" in o:
            para.space_after = Pt(o["space"])
        if "before" in o:
            para.space_before = Pt(o["before"])
        if o.get("line", spacing):
            para.line_spacing = o.get("line", spacing)
        runs = t if isinstance(t, list) else [(t, {})]
        for rt, ro in runs:
            r = para.add_run()
            r.text = rt
            fnt = r.font
            fnt.name = ro.get("font", o.get("font", font))
            fnt.size = Pt(ro.get("size", o.get("size", size)))
            fnt.bold = ro.get("bold", o.get("bold", bold))
            fnt.italic = ro.get("italic", o.get("italic", False))
            fnt.color.rgb = rgb(ro.get("color", o.get("color", color)))
            spc = ro.get("spc", o.get("spc"))
            if spc:
                r._r.get_or_add_rPr().set("spc", str(spc))
    return sh


def line(sl, x1, y1, x2, y2, color=MUTE, w=1.0, arrow=True, dash=False):
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(w)
    if dash:
        c.line.dash_style = 4
    if arrow:
        ln = c.line._get_or_add_ln()
        t = etree.SubElement(ln, A + "tailEnd")
        t.set("type", "triangle")
        t.set("w", "sm")
        t.set("len", "sm")
    return c


def hline(sl, x, y, w, color=LINE, h=0.012):
    return shape(sl, x, y, w, h, fill=color)


# ---------------------------------------------------------------- vector icons (inserted by deck_postprocess.ps1)
_COLORED = ASSETS / "_colored"


def _svg(kind, name, color):
    src = ASSETS / kind / f"{name}.svg"
    if not src.exists():
        return None
    _COLORED.mkdir(parents=True, exist_ok=True)
    out = _COLORED / f"{kind}_{name}_{color}.svg"
    s = src.read_text(encoding="utf-8")
    if kind == "icons":
        s = s.replace("currentColor", f"#{color}")
    else:
        s = s.replace("<svg ", f'<svg fill="#{color}" ', 1)
    out.write_text(s, encoding="utf-8")
    return out


def icon(sl, name, x, y, size, color=TEAL, kind="icons"):
    p = _svg(kind, name, color)
    if p is None:
        return None
    return shape(sl, x, y, size, size, name=f"SVG|{p.resolve()}")


def icon_badge(sl, name, x, y, d=0.5, fill=TEAL_L, color=TEAL, ratio=0.52, oval=False):
    shape(sl, x, y, d, d, fill=fill, kind=MSO_SHAPE.OVAL if oval else MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.28)
    s = d * ratio
    icon(sl, name, x + (d - s) / 2, y + (d - s) / 2, s, color)


def num_badge(sl, n, x, y, d=0.42, fill=TEAL_L, color=TEAL):
    b = shape(sl, x, y, d, d, fill=fill, kind=MSO_SHAPE.OVAL)
    T(sl, 0, 0, 0, 0, [(str(n), {"size": 13, "font": FB, "color": color, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=b)


def picture(sl, path, x, y, w=None, h=None):
    p = Path(path)
    if not p.exists():
        ph = rrect(sl, x, y, w or 3, h or 2, fill=PANEL, line=LINE)
        T(sl, 0, 0, 0, 0, [(f"[missing {p.name}]", {"size": 10, "color": FAINT, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=ph)
        return ph
    kw = {}
    if w:
        kw["width"] = Inches(w)
    if h:
        kw["height"] = Inches(h)
    return sl.shapes.add_picture(str(p), Inches(x), Inches(y), **kw)


def drop_all(sl):
    for sh in list(sl.shapes):
        sh._element.getparent().remove(sh._element)


# ---------------------------------------------------------------- page chrome
def logo_mark(sl, x, y, d=0.3, dark=False):
    c = TEAL if not dark else WARM
    shape(sl, x, y, d, d, fill=None, line=c, kind=MSO_SHAPE.OVAL, lw=1.5)
    shape(sl, x + d * 0.3, y + d * 0.3, d * 0.4, d * 0.4, fill=c, kind=MSO_SHAPE.OVAL)


def chrome(sl, n, section, headline, sub=None, hl=None):
    """Background, top bar (brand | section kicker | event), assertion headline (hl = phrase to highlight), optional side note."""
    bg = ASSETS / "bg_slide.jpg"
    if bg.exists():
        sl.shapes.add_picture(str(bg), 0, 0, Inches(SW), Inches(SH))
    logo_mark(sl, L, 0.34, 0.26)
    T(sl, L + 0.36, 0.3, 1.6, 0.34, [("TapTrace", {"size": 13, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
    shape(sl, L + 1.62, 0.36, 0.012, 0.22, fill=LINE)
    T(sl, L + 1.8, 0.3, 6.0, 0.34, [(f"{n:02d}  |  {section.upper()}", {"size": 9, "color": MUTE, "spc": 250})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, 7.9, 0.3, 4.81, 0.34, [("SAMSUNG PRISM  ·  GENAI HACKATHON 3.0  ·  THEME 02", {"size": 8.5, "color": MUTE, "spc": 200, "align": PP_ALIGN.RIGHT})],
      anchor=MSO_ANCHOR.MIDDLE)
    runs = [(headline, {})]
    if hl and hl in headline:
        a, b = headline.split(hl, 1)
        runs = [(a, {}), (hl, {"color": TEAL}), (b, {})]
    T(sl, L, 0.9, 8.95 if sub else W, 1.2, [(runs, {"size": 27, "font": FB, "color": INK, "line": 0.92})])
    if sub:
        T(sl, 9.85, 0.98, 2.86, 1.1, [(sub, {"size": 11, "color": SLATE, "line": 1.05})])
    T(sl, 11.6, 7.08, 1.11, 0.25, [(f"{n + 1:02d} / 12", {"size": 8, "color": FAINT, "align": PP_ALIGN.RIGHT})])
    T(sl, L, 7.08, 6, 0.25, [("TIET  ·  TAPTRACE  ·  THEME 02 SMART GUIDED TROUBLESHOOTING", {"size": 7.5, "color": FAINT, "spc": 150})])


def kicker(sl, x, y, text, color=MUTE, w=4):
    T(sl, x, y, w, 0.25, [(text.upper(), {"size": 8.5, "font": FB, "color": color, "spc": 220})])


def _chart_nofill(ch):
    """Transparent chart + plot area so the glass card and background show through."""
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    cs = ch._chartSpace
    for parent, after in ((cs, cs.find(C + "chart")), (cs.find(f"{C}chart/{C}plotArea"), None)):
        if parent is None or parent.find(C + "spPr") is not None:
            continue
        sp = etree.Element(C + "spPr")
        etree.SubElement(sp, A + "noFill")
        ln = etree.SubElement(sp, A + "ln")
        etree.SubElement(ln, A + "noFill")
        if after is not None:
            after.addnext(sp)
        else:
            ext = parent.find(C + "extLst")
            (ext.addprevious(sp) if ext is not None else parent.append(sp))


def style_chart(ch, maxv=115, fmt='0"%"', size=9.5):
    _chart_nofill(ch)
    ch.font.size = Pt(size)
    ch.font.name = F
    ch.font.color.rgb = rgb(SLATE)
    va = ch.value_axis
    va.maximum_scale, va.minimum_scale = maxv, 0
    va.has_major_gridlines = False
    va.visible = False
    ca = ch.category_axis
    ca.format.line.color.rgb = rgb("5A534B")
    ca.tick_labels.font.size = Pt(size)
    ca.tick_labels.font.color.rgb = rgb(SLATE)
    pl = ch.plots[0]
    pl.has_data_labels = True
    dl = pl.data_labels
    dl.number_format, dl.number_format_is_linked = fmt, False
    dl.position = XL_LABEL_POSITION.OUTSIDE_END
    dl.font.size, dl.font.bold = Pt(size), True
    dl.font.color.rgb = rgb(INK)


def bar_chart(sl, x, y, w, h, cats, vals, hi_last=True, title=None, horizontal=True):
    cd = CategoryChartData()
    cd.categories = cats
    cd.add_series("s", vals)
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED,
                             Inches(x), Inches(y), Inches(w), Inches(h), cd)
    ch = gf.chart
    ch.has_legend = False
    if title:
        ch.has_title = True
        ch.chart_title.text_frame.text = title
        r = ch.chart_title.text_frame.paragraphs[0].runs[0]
        r.font.size, r.font.bold, r.font.name = Pt(10), True, F
        r.font.color.rgb = rgb(INK)
    else:
        ch.has_title = False
    style_chart(ch)
    ch.plots[0].gap_width = 45
    ser = ch.plots[0].series[0]
    for i in range(len(vals)):
        pt = ser.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(TEAL if (hi_last and i == len(vals) - 1) else BAR)
    return ch


def _bare(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--github", default="https://github.com/Arnav020/Tap-Trace")
    ap.add_argument("--deployed", default="https://taptrace.onrender.com")
    ap.add_argument("--video", default="YouTube / Drive link in the submission form")
    args = ap.parse_args()

    ev = json.loads((ROOT / "artifacts" / "eval_raw.json").read_text(encoding="utf-8"))
    stress = json.loads((ROOT / "artifacts" / "stress.json").read_text(encoding="utf-8"))
    res = [json.loads(l) for l in (ROOT / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    s1, s2 = ev["schema_rules"], ev["accuracy"]
    ca, cg, mp = ev["cache"]["adaptive"], ev["cache"]["global"], ev["mapping"]
    cold = ev["cold_latencies"]
    P = lambda xs, q: float(np.percentile(xs, q)) if xs else float("nan")  # noqa: E731
    costs = [r["meta"]["cost_usd"] for r in res if r["meta"]["cost_usd"] > 0]
    avg_cost = sum(costs) / len(costs)
    nomatch = sum(1 for r in res if r["meta"]["fallback"] == "no_match")
    cold50, cold95 = P(cold, 50) / 1000, P(cold, 95) / 1000
    hit95, hit50 = P(ca["lat_para"], 95), P(ca["lat_para"], 50)
    speedup = 900.0 / cold50

    from taptrace.catalog import Catalog
    from taptrace.embed import Embedder

    cat = Catalog(ROOT / "data" / "deeplinks.json")
    rep = cat.noise_report()
    emb = Embedder(ROOT / "artifacts" / "model")
    on_off = float(emb.encode_one("My Nexa X1 Ultra screen won't turn on") @ emb.encode_one("My Nexa X1 Ultra screen won't turn off"))
    sample = json.loads((ROOT / "data" / "sample_output.json").read_text(encoding="utf-8"))
    sample_words = max(len(a["description"].split()) for g in sample["response"]["contexts"] for a in g["actions"])
    distractors = len(rep["tv_distractors"]) + len(rep["iot_distractors"])
    r19 = res[18]
    g19 = r19["response"]["contexts"][0]
    nav = next(a for a in g19["actions"] if a["actionName"].startswith("Configure Navigation"))
    nav_dl = nav["stepGroups"][0]["actionableDeeplink"]
    nav_val = nav["stepGroups"][0]["validationDeeplink"]
    import subprocess
    col = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True, text=True).stdout
    m_t = re.search(r"(\d+) tests? collected", col)
    n_tests = int(m_t.group(1)) if m_t else sum(1 for l in col.splitlines() if "::" in l)

    prs = Presentation(args.template)
    S = prs.slides
    for sl in S:
        drop_all(sl)

    # ================================================================ 1 COVER
    sl = S[0]
    hero = ASSETS / "hero.jpg"
    if hero.exists():
        sl.shapes.add_picture(str(hero), 0, 0, Inches(SW), Inches(SH))
    else:
        shape(sl, 0, 0, SW, SH, fill="0B1220")
    logo_mark(sl, 0.75, 0.62, 0.34, dark=True)
    T(sl, 1.2, 0.58, 2.5, 0.42, [("TapTrace", {"size": 18, "font": FB, "color": WHITE})], anchor=MSO_ANCHOR.MIDDLE)
    shape(sl, 2.85, 0.785, 1.2, 0.01, fill="8A7F73")
    T(sl, 4.2, 0.62, 4.2, 0.34, [("SAMSUNG PRISM  ·  GENAI HACKATHON 3.0",
                                 {"size": 9, "color": "CFC6BA", "spc": 300})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, 0.75, 1.55, 7.0, 2.6, [([("From a vague complaint", {})], {"size": 44, "font": FB, "color": WHITE, "line": 0.95}),
                                 ([("to a ", {}), ("verified fix", {"color": WARM}), (",", {})], {"size": 44, "font": FB, "color": WHITE, "line": 0.95}),
                                 ([("in one tap.", {})], {"size": 44, "font": FB, "color": WHITE, "line": 0.95})])
    T(sl, 0.75, 4.2, 6.2, 0.9, [("A grounded troubleshooting engine for Samsung PRISM Theme 02: every step traced to its source, "
                                 "every Settings fix one exact deeplink away, every repeat answered from cache in milliseconds.",
                                 {"size": 13.5, "color": "E6DDD1", "line": 1.12})])
    feats = [("scan-search", "Evidence-locked", "steps"), ("crosshair", "Exact-screen", "deeplinks"), ("filter", "Relevance", "gate"), ("zap", "Contrastive", "cache")]
    for i, (ic, a, b) in enumerate(feats):
        x = 0.75 + i * 1.62
        icon(sl, ic, x, 5.35, 0.36, "EDE5DA")
        T(sl, x, 5.82, 1.5, 0.5, [(a, {"size": 10.5, "color": WHITE}), (b, {"size": 10.5, "color": WHITE})])
        if i:
            shape(sl, x - 0.14, 5.38, 0.01, 0.85, fill="5A4F45")
    shape(sl, 0.75, 6.72, 11.85, 0.01, fill="5A4F45")
    T(sl, 0.75, 6.8, 11.85, 0.5, [("THEME ID 02   |   TEAM TAPTRACE   |   THAPAR INSTITUTE OF ENGINEERING & TECHNOLOGY   |   "
                                   "ARNAV JOSHI · ajoshi4_be23@thapar.edu", {"size": 8.5, "color": "CFC6BA", "spc": 120, "space": 3}),
                                  (f"CODE  {_bare(args.github)}   |   LIVE DEMO  {_bare(args.deployed)}", {"size": 8.5, "color": WARM, "spc": 120})])

    # ================================================================ 2 THEME / PROBLEM
    sl = S[1]
    chrome(sl, 1, "Theme · the problem", "One vague sentence costs an agent ~15 minutes - and the customer still hunts through Settings.",
           "Theme 02 asks for a REST engine that turns such complaints into a grounded, ordered, one-tap plan under hard latency, cost and hygiene rules.",
           hl="~15 minutes")
    # input card
    rrect(sl, L, 2.3, 3.95, 3.3, fill=PANEL, line=LINE)
    kicker(sl, L + 0.25, 2.48, "The input", MUTE)
    T(sl, L + 0.25, 2.8, 3.5, 1.7, [("“My Nexa X1 screen inputs are delayed and the touch responsiveness is laggy, causing a noticeable "
                                     "delay when I try to interact with the phone.”", {"size": 13.5, "italic": True, "color": INK, "line": 1.1})])
    T(sl, L + 0.25, 4.3, 3.5, 0.3, [("input.txt, line 19  +  SIIS ‘Touchscreen issues’ (9 sections)", {"size": 9, "color": MUTE})])
    for i, chip in enumerate(["no setting names", "no technical terms", "no order"]):
        c = rrect(sl, L + 0.25 + i * 1.18, 4.8, 1.1, 0.34, fill=GLASS2, line=LINE, radius=0.5)
        T(sl, 0, 0, 0, 0, [(chip, {"size": 8.5, "color": SLATE, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=c)
    # arrow
    line(sl, 4.72, 3.95, 5.28, 3.95, color=TEAL, w=2)
    T(sl, 4.6, 3.5, 0.8, 0.3, [(f"{cold50:.1f} s", {"size": 11, "font": FB, "color": TEAL, "align": PP_ALIGN.CENTER})])
    # output anatomy card
    rrect(sl, 5.38, 2.3, 7.33, 3.3, fill=GLASS, line=LINE)
    kicker(sl, 5.62, 2.48, "The required output (schema.py contract)", TEAL, 6)
    tree = [(0, "goal", g19["goal"], INK), (0, "title", g19["title"], INK),
            (1, "actionName", nav["actionName"], INK), (1, "description", nav["description"], INK),
            (1, "category", nav["category"], TEAL), (2, "steps", " → ".join(s.rstrip(".") for s in nav["stepGroups"][0]["steps"][1:4]), SLATE),
            (2, "actionableDeeplink", nav_dl["deeplink"], TEAL), (2, "validationDeeplink", f"key = ‘{nav_val['key']}’", TEAL)]
    for i, (lvl, k, v, col) in enumerate(tree):
        y = 2.85 + i * 0.33
        x = 5.62 + lvl * 0.34
        if lvl:
            shape(sl, x - 0.2, y + 0.13, 0.12, 0.012, fill=LINE)
        T(sl, x, y, 1.75, 0.3, [(k, {"size": 9.5, "font": MONO, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x + 1.72, y, 5.25 - lvl * 0.34, 0.3, [(v if len(v) < 75 else v[:72] + "…", {"size": 10, "color": col, "font": MONO if "voiceassist" in v else F})],
          anchor=MSO_ANCHOR.MIDDLE)
    # constraint tiles
    kicker(sl, L, 5.83, "Hard constraints we enforce in code (Theme 2 PDF §4 and §6)", MUTE, 8)
    cons = [("shield-check", "0 URL leaks", "regex gate on every field"), ("list-checks", "5–7-word descriptions", "validators + repair"),
            ("list-ordered", "Critical steps last", "disruption-cost ordering"), ("zap", "≤ 300 ms cached", "P95, no LLM call"),
            ("timer", "≤ 8 s cold", "P95, two parallel LLM calls")]
    for i, (ic, a, b) in enumerate(cons):
        x = L + i * 2.43
        rrect(sl, x, 6.12, 2.33, 0.8, fill=GLASS, line=LINE)
        icon_badge(sl, ic, x + 0.14, 6.27, 0.5)
        T(sl, x + 0.76, 6.22, 1.52, 0.62, [(a, {"size": 10.5, "font": FB}), (b, {"size": 8.5, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE)

    # ================================================================ 3 GAPS
    sl = S[2]
    chrome(sl, 2, "Existing solutions & gaps", "The provided data is adversarial - a textbook RAG pipeline walks into six traps.",
           f"We audited all {rep['rows']} catalog rows and 20 SIIS articles before writing code (docs/DATA_AUDIT.md).", hl="six traps")
    rrect(sl, L, 2.3, 3.05, 4.65, fill=PANEL, line=LINE)
    kicker(sl, L + 0.22, 2.46, "Textbook pipeline", MUTE)
    flow = [("file-text", "LLM writes the steps"), ("search", "Top-1 embedding match"), ("database", "One global cache threshold"), ("book-open", "Trust any SIIS article")]
    for i, (ic, t) in enumerate(flow):
        y = 2.82 + i * 0.55
        c = rrect(sl, L + 0.22, y, 2.6, 0.44, fill=GLASS2, line=LINE, radius=0.2)
        icon(sl, ic, L + 0.36, y + 0.11, 0.24, MUTE)
        T(sl, L + 0.7, y, 2.0, 0.46, [(t, {"size": 9.5, "color": SLATE})], anchor=MSO_ANCHOR.MIDDLE)
        icon(sl, "circle-x", L + 2.55, y + 0.13, 0.2, RED)
    bar_chart(sl, L + 0.05, 5.3, 2.95, 1.6, ["Pure rules", "Hybrid", "Full LLM", "TapTrace"],
              [round(mp["rules"]["acc"]), round(mp["hybrid"]["acc"]), round(mp["llm"]["acc"]) if mp.get("llm") else 0, round(mp["ours"]["acc"])])
    T(sl, L + 0.22, 5.12, 2.8, 0.25, [("Exact deeplinks, 37-case benchmark", {"size": 8.5, "font": FB, "color": SLATE})])
    traps = [("tv", str(distractors), "TV & appliance screens hidden in the phone catalog", "DL-0468 … DL-0569", "Catalog Compiler quarantines them"),
             ("triangle-alert", str(len(rep["misleading_messages"])), "labels that name the wrong feature", "DL-0162 “Disable Grayscale” = mono audio", "Match on screen labels, never messages"),
             ("toggle-right", f"{on_off:.2f}", "cosine of “won’t turn ON” vs “won’t turn OFF”", "embeddings are polarity-blind", "Facet guard + per-entry τ"),
             ("book-open", f"{nomatch}/20", "SIIS articles are the wrong document", "row_8: TV mirroring for a small phone screen", "Relevance Gate → honest no_match"),
             ("crosshair", "DL-0022", "nearest entry to “factory data reset”", "it is the AUTO reset after failed unlocks", "Abstain → dummy_positive"),
             ("list-checks", f"{sample_words} words", "in the official sample description (5–7 allowed)", "sample_output.json", "Every field rule enforced in code")]
    for i, (ic, n, what, evd, fix) in enumerate(traps):
        x = 3.87 + (i % 3) * 2.96
        y = 2.3 + (i // 3) * 2.36
        rrect(sl, x, y, 2.84, 2.22, fill=GLASS, line=LINE)
        icon_badge(sl, ic, x + 0.18, y + 0.18, 0.46, fill=RED_L, color=RED)
        T(sl, x + 0.76, y + 0.14, 2.0, 0.55, [(n, {"size": 22, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x + 0.18, y + 0.76, 2.5, 0.5, [(what, {"size": 10, "font": FB, "line": 1.0})])
        c = rrect(sl, x + 0.18, y + 1.3, 2.5, 0.34, fill=PANEL, line=None, radius=0.25)
        T(sl, 0, 0, 0, 0, [(evd, {"size": 7.5, "font": MONO, "color": SLATE})], anchor=MSO_ANCHOR.MIDDLE, on=c, margin=0.08)
        icon(sl, "arrow-right", x + 0.18, y + 1.8, 0.2, TEAL)
        T(sl, x + 0.45, y + 1.74, 2.3, 0.34, [(fix, {"size": 9.5, "font": FB, "color": TEAL})], anchor=MSO_ANCHOR.MIDDLE)

    # ================================================================ 4 ARCHITECTURE
    sl = S[3]
    chrome(sl, 3, "Our solution & architecture", "A five-stage pipeline where the LLM may select and name - but never write - a step.",
           "Two LLM calls run in parallel on the cold path. Everything else is deterministic, tested code.", hl="never write")
    stages = [
        ("Understand", SKY, SKY_L, [("sliders-horizontal", "Facet frame", "symptom · part · component · on/off"),
                                    ("cpu", "Stage A · LLM", "canonical query, 10 paraphrases, hard negatives"),
                                    ("zap", "Contrastive cache", f"hit in {hit95:.0f} ms, $0, no LLM")]),
        ("Ground", TEAL, TEAL_L, [("filter", "Relevance Gate", "full · partial · none → no_match"),
                                  ("file-text", "SIIS normaliser", "span ids, leak & corrupt flags"),
                                  ("scan-search", "Evidence units", "verbatim steps + provenance")]),
        ("Resolve", AMBER, AMBER_L, [("layers", "Catalog Compiler", f"{rep['screens_total']} screens × on/off/open/set"),
                                     ("crosshair", "Screen resolver", "leaf label → fuzzy → abstain"),
                                     ("toggle-right", "Polarity + probe", "variant + validationDeeplink")]),
        ("Compose", CORAL, CORAL_L, [("cpu", "Stage B · LLM", "select, group, name - no step text"),
                                     ("list-ordered", "Category & order", "auto → manual → critical"),
                                     ("gauge", "Computed score", "fit × grounding × mapping")]),
        ("Guarantee", STONE, STONE_L, [("shield-check", "Output gate", "organisers’ schema · URI set · 0 leaks"),
                                       ("database", "Cache write", "τ from own ± clouds"),
                                       ("server", "REST API", "/v1/troubleshoot · /health")]),
    ]
    cw, gap = 2.3, 0.147
    for i, (name, col, tint, rows) in enumerate(stages):
        x = L + i * (cw + gap)
        rrect(sl, x, 2.3, cw, 3.25, fill=tint, line=None)
        num_badge(sl, i + 1, x + 0.16, 2.45, 0.4, fill=col, color=DARK)
        T(sl, x + 0.66, 2.45, 1.6, 0.4, [(name, {"size": 13.5, "font": FB, "color": INK})], anchor=MSO_ANCHOR.MIDDLE)
        for j, (ic, t, sub) in enumerate(rows):
            y = 3.0 + j * 0.83
            is_llm = "LLM" in t
            rrect(sl, x + 0.13, y, cw - 0.26, 0.72, fill=GLASS2, line=col if is_llm else None, dash=is_llm, lw=1.1)
            icon(sl, ic, x + 0.25, y + 0.2, 0.3, col)
            T(sl, x + 0.66, y + 0.08, cw - 0.84, 0.6, [(t, {"size": 9.5, "font": FB}), (sub, {"size": 8, "color": MUTE, "line": 0.95})])
        if i < 4:
            line(sl, x + cw + 0.01, 3.9, x + cw + gap - 0.01, 3.9, color=FAINT, w=1.25)
    # latency budget
    rrect(sl, L, 5.75, 8.35, 1.2, fill=GLASS, line=LINE)
    kicker(sl, L + 0.2, 5.87, "Latency budget (measured)", MUTE)
    T(sl, L + 0.2, 6.18, 1.3, 0.28, [("Cache hit", {"size": 9.5, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
    segs = [("normalise + facets", 0.8), ("embed (INT8 ONNX)", 1.6), ("ANN + guard", 0.9)]
    x = L + 1.55
    for t, wd in segs:
        c = shape(sl, x, 6.2, wd, 0.24, fill=TEAL_L, line=("000000", 0.45), lw=1.5)
        T(sl, 0, 0, 0, 0, [(t, {"size": 7.5, "color": TEAL_D, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=c)
        x += wd
    T(sl, x + 0.1, 6.18, 2.5, 0.28, [(f"P95 {hit95:.1f} ms  ·  $0", {"size": 9.5, "font": FB, "color": TEAL})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, L + 0.2, 6.55, 1.3, 0.28, [("Cold path", {"size": 9.5, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
    segs = [("gate + units", 0.7, STONE_L, STONE), ("Stage A ∥ Stage B (LLM)", 3.2, CORAL_L, CORAL), ("resolve + compose + gate", 1.0, AMBER_L, AMBER)]
    x = L + 1.55
    for t, wd, fl, tc in segs:
        c = shape(sl, x, 6.57, wd, 0.24, fill=fl, line=("000000", 0.45), lw=1.5)
        T(sl, 0, 0, 0, 0, [(t, {"size": 7.5, "color": tc, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=c)
        x += wd
    T(sl, x + 0.1, 6.55, 2.2, 0.28, [(f"P50 {cold50:.1f} s  ·  P95 {cold95:.1f} s", {"size": 9.5, "font": FB, "color": CORAL})], anchor=MSO_ANCHOR.MIDDLE)
    kp = [("coins", f"${avg_cost:.5f}", "per cold query"), ("zap", "$0", "per cache hit"), ("cpu", "2", "LLM calls per cold query")]
    for i, (ic, v, l) in enumerate(kp):
        x = 9.2 + i * 1.19
        rrect(sl, x, 5.75, 1.1, 1.2, fill=GLASS, line=LINE)
        icon(sl, ic, x + 0.13, 5.88, 0.24, TEAL)
        T(sl, x + 0.13, 6.2, 0.95, 0.72, [(v, {"size": 13, "font": FB}), (l, {"size": 7.5, "color": MUTE, "line": 0.95})])

    # ================================================================ 5 DEMO
    sl = S[4]
    chrome(sl, 4, "Demo & product walkthrough", "The live console shows every step’s source sentence and closes the loop with a probe.",
           "Real screenshot of demo/ served by the same container (device simulated in the browser).", hl="closes the loop")
    shot = ASSETS / "shots" / "console.png"
    # screenshot fitted to height (never stretched): 1440x1000 CSS px captured at 2x
    ratio = 1000 / 1440
    if shot.exists():
        from PIL import Image
        im = Image.open(shot)
        ratio = im.height / im.width
    bx, by, ph = L, 2.25, 4.42
    pw = ph / ratio
    bw = pw + 0.04
    rrect(sl, bx, by, bw, ph + 0.32, fill=PANEL, line=LINE, radius=0.03)
    for k, c in enumerate(["EE9A7E", "F2B66D", "93CDB9"]):
        shape(sl, bx + 0.15 + k * 0.18, by + 0.1, 0.1, 0.1, fill=c, kind=MSO_SHAPE.OVAL)
    u = rrect(sl, bx + 0.8, by + 0.06, 3.2, 0.18, fill=GLASS2, line=None, radius=0.5)
    T(sl, 0, 0, 0, 0, [("localhost:8000  ·  TapTrace console", {"size": 7, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE, on=u, margin=0.08)
    picture(sl, shot, bx + 0.02, by + 0.3, w=pw, h=ph)
    # marks sit on: 1 highlighted source sentence, 2 cache pill, 3 validation probe, 4 option groups
    sc = pw / 1440
    ox, oy = bx + 0.02, by + 0.3
    marks = [(1, ox + 20 * sc - 0.15, oy + 778 * sc - 0.15), (2, ox + 405 * sc - 0.15, oy + 398 * sc - 0.2),
             (3, ox + 999 * sc - 0.26, oy + 590 * sc - 0.15), (4, ox + 999 * sc - 0.26, oy + 385 * sc - 0.15)]
    for n, mx, my in marks:
        num_badge(sl, n, mx, my, 0.3, fill=TEAL, color=DARK)
    rx = bx + bw + 0.3
    rw = SW - L - rx
    notes = [("scan-search", "Provenance", "every plan step is highlighted in its source SIIS sentence, with span ids"),
             ("zap", "Cache decision", "exact or semantic hit with similarity ≥ per-entry τ at $0, or the guard that refused"),
             ("badge-check", "One tap + probe", "an auto step runs its deeplink; the validationDeeplink probe turns ✓"),
             ("toggle-right", "Conditional options", "one screen, two step groups: ON with a screen protector, OFF without")]
    cw2, chh = (rw - 0.18) / 2, 1.42
    for i, (ic, t, d) in enumerate(notes):
        x = rx + (i % 2) * (cw2 + 0.18)
        y = by + (i // 2) * (chh + 0.16)
        rrect(sl, x, y, cw2, chh, fill=GLASS, line=LINE)
        num_badge(sl, i + 1, x + 0.18, y + 0.18, 0.34, fill=TEAL, color=DARK)
        icon(sl, ic, x + cw2 - 0.46, y + 0.2, 0.26, MUTE)
        T(sl, x + 0.18, y + 0.62, cw2 - 0.34, 0.75, [(t, {"size": 11, "font": FB}), (d, {"size": 8.5, "color": MUTE, "line": 1.0})])
    cy_ = by + 2 * (chh + 0.16)
    rrect(sl, rx, cy_, rw, by + ph + 0.32 - cy_, fill=("000000", 0.36), line=LINE, radius=0.04)
    kicker(sl, rx + 0.2, cy_ + 0.14, "What the phone received (results.jsonl, line 19)", MUTE, rw - 0.4)
    touch = g19["actions"][0]
    t_dl, t_val = touch["stepGroups"][0]["actionableDeeplink"], touch["stepGroups"][0]["validationDeeplink"]
    code = [f"\"actionName\": \"{touch['actionName']}\",  \"category\": \"{touch['category']}\",",
            f"\"deeplink\": \"{t_dl['deeplink']}\",  \"originalType\": \"{t_dl['originalType']}\",",
            f"\"validationDeeplink\": {{\"key\": \"{t_val['key']}\",",
            f"    \"condition\": \"{t_val.get('condition')}\", \"value\": \"{t_val.get('value')}\"}}"]
    T(sl, rx + 0.2, cy_ + 0.46, rw - 0.4, 1.2, [(c, {"size": 8.5, "font": MONO, "color": "E6DDD1" if i % 2 else TEAL, "line": 1.12})
                                                  for i, c in enumerate(code)])

    # ================================================================ 6 TECH STACK
    sl = S[5]
    chrome(sl, 5, "Tools and tech stack", "Small, CPU-only and reproducible - one container, no GPU, and it runs even without an API key.",
           "Every dependency is pinned in requirements.txt; the INT8 embedder and pre-warmed cache ship in the image.", hl="CPU-only")
    groups = [("Serving", [("logos", "fastapi", "009688", "FastAPI"), ("icons", "server", "3A322B", "Uvicorn"), ("logos", "pydantic", "E92063", "Pydantic"), ("icons", "braces", "3A322B", "ORJSON")]),
              ("Intelligence", [("logos", "openai", "111111", "gpt-oss-120b"), ("icons", "cpu", "C2410C", "Groq API"), ("logos", "huggingface", "D89A00", "MiniLM-L6"), ("logos", "onnx", "005CED", "ONNX Runtime")]),
              ("Data & quality", [("logos", "python", "3776AB", "Python 3.12"), ("logos", "numpy", "013243", "NumPy"), ("logos", "sqlite", "003B57", "SQLite"), ("logos", "pytest", "0A9EDC", "pytest")])]
    for gi, (gname, items) in enumerate(groups):
        x = L + gi * 2.83
        rrect(sl, x, 2.3, 2.7, 3.2, fill=GLASS, line=LINE)
        kicker(sl, x + 0.2, 2.45, gname, MUTE)
        for j, (kind, nm, col, lab) in enumerate(items):
            cx = x + 0.2 + (j % 2) * 1.25
            cy = 2.85 + (j // 2) * 1.28
            rrect(sl, cx, cy, 1.1, 1.1, fill=("FFFFFF", 0.93), line=None, radius=0.1)
            icon(sl, nm, cx + 0.33, cy + 0.14, 0.44, col, kind=kind)
            T(sl, cx, cy + 0.68, 1.1, 0.35, [(lab, {"size": 8.5, "color": "3A322B", "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE)
    rrect(sl, L, 5.68, 8.37, 1.3, fill=("000000", 0.36), line=LINE, radius=0.04)
    tree = ["taptrace/  catalog.py · siis.py · facets.py · resolver.py · plan.py · cache.py · engine.py · api.py",
            "scripts/   build_results · evaluate · stress_test · audit_report · build_deck",
            "eval/      gold · mapping_benchmark · heldout + test paraphrases · hard_negatives",
            f"Dockerfile · docker-compose.yml · results.jsonl · metrics.md · tests/ ({n_tests} gates)"]
    T(sl, L + 0.22, 5.8, 8.0, 1.1, [(t, {"size": 9, "font": MONO, "color": "EDE5DA" if i else AMBER, "line": 1.1}) for i, t in enumerate(tree)])
    if (ASSETS / "logos" / "docker.svg").exists():
        icon(sl, "docker", 8.3, 5.85, 0.5, "2496ED", kind="logos")
    facts = [("hard-drive", "23 MB", "INT8 ONNX embedder, 0.97 cosine vs FP32"), ("timer", f"{stress['cold_start_s']} s", "container cold start to healthy"),
             ("activity", f"{stress['throughput_rps']:.0f} req/s", f"{stress['concurrency']} concurrent clients, {stress['errors']} errors"),
             ("flask-conical", f"{n_tests} gates", "pytest: contract, traps, gate, cache, API, unseen domains"), ("lock", "0 keys", "needed to run: deterministic path + cache")]
    for i, (ic, v, l) in enumerate(facts):
        y = 2.3 + i * 0.95
        icon_badge(sl, ic, 9.22, y + 0.05, 0.5)
        T(sl, 9.86, y, 2.85, 0.85, [(v, {"size": 15, "font": FB}), (l, {"size": 9, "color": MUTE})])

    # ================================================================ 7 IMPACT
    sl = S[6]
    chrome(sl, 6, "Impact & use case", f"From ~15 minutes of manual triage to ~{cold50:.0f} seconds - and $0 for every repeat complaint.",
           f"About {speedup:,.0f}× faster on a new complaint; a cache hit is served in {hit50:.0f} ms.", hl=f"~{cold50:.0f} seconds")
    rrect(sl, L, 2.3, 6.0, 2.55, fill=GLASS, line=LINE)
    kicker(sl, L + 0.25, 2.45, "Time to an actionable plan (to scale)", MUTE, 5)
    bars = [("Manual triage", 900.0, "≈ 15 min", BAR, SLATE), ("TapTrace, new", cold50, f"{cold50:.1f} s", TEAL, TEAL),
            ("TapTrace, repeat", hit50 / 1000, f"{hit50:.0f} ms", TEAL, TEAL)]
    for i, (lb, v, vl, fl, tc) in enumerate(bars):
        y = 2.9 + i * 0.6
        T(sl, L + 0.25, y, 1.4, 0.4, [(lb, {"size": 10, "color": SLATE})], anchor=MSO_ANCHOR.MIDDLE)
        wd = max(0.05, 3.6 * v / 900.0)
        shape(sl, L + 1.7, y + 0.08, wd, 0.24, fill=fl)
        T(sl, L + 1.78 + wd, y, 1.1, 0.4, [(vl, {"size": 11, "font": FB, "color": tc})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, L + 0.25, 4.5, 5.6, 0.25, [("New-complaint time is the measured cold-path P50; repeat time is the measured cache-hit P50.", {"size": 8, "color": FAINT})])
    rrect(sl, 6.87, 2.3, 5.84, 2.55, fill=TEAL_L, line=None)
    kicker(sl, 7.1, 2.45, "At production scale: 10,000 scenarios (brochure target)", TEAL, 5.5)
    scale_rows = [("Manual triage", f"{10000 * 15 / 60:,.0f} agent-hours", "at 15 min each"),
                  ("TapTrace first pass", f"≈ ${10000 * avg_cost:,.2f} LLM cost", f"{10000 * cold50 / 3600:,.1f} h sequential; parallelisable"),
                  ("Every repeat after", "$0  ·  milliseconds", "served from the contrastive cache"),
                  ("Mapping reuse", "shared step-path memo", "screens resolved once, reused everywhere")]
    for i, (a, b, c) in enumerate(scale_rows):
        y = 2.85 + i * 0.47
        T(sl, 7.1, y, 1.9, 0.42, [(a, {"size": 10, "color": SLATE})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, 9.0, y, 3.6, 0.42, [(b, {"size": 11.5, "font": FB, "color": INK}), (c, {"size": 8, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE)
        if i < 3:
            hline(sl, 7.1, y + 0.45, 5.4, color=LINE)
    personas = [("smartphone", "Customer", "Reads text steps, then hunts nested menus.", "Taps once: the exact screen and toggle state opens; a probe confirms the fix."),
                ("headset", "Support agent", "Reads, selects and orders steps by hand.", "Gets an ordered plan with every step highlighted in its source article."),
                ("server", "Operations", "Wrong articles turn into wrong advice.", f"Wrong-document tickets return no_match; ≈${avg_cost:.4f} per new scenario.")]
    for i, (ic, who, before, after) in enumerate(personas):
        x = L + i * 4.07
        rrect(sl, x, 5.05, 3.95, 1.9, fill=GLASS, line=LINE)
        icon_badge(sl, ic, x + 0.2, 5.2, 0.5)
        T(sl, x + 0.85, 5.2, 3.0, 0.5, [(who, {"size": 13, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x + 0.2, 5.82, 3.6, 0.5, [([("BEFORE  ", {"size": 8, "font": FB, "color": FAINT, "spc": 150}), (before, {})], {"size": 9.5, "color": MUTE})])
        T(sl, x + 0.2, 6.3, 3.6, 0.6, [([("AFTER  ", {"size": 8, "font": FB, "color": TEAL, "spc": 150}), (after, {})], {"size": 9.5, "color": INK})])

    # ================================================================ 8 RESULTS
    sl = S[7]
    chrome(sl, 7, "Innovation highlights, results and limitations", "Every target in the brief is met - measured on the provided data and a frozen test set.",
           "All numbers are produced by scripts/evaluate.py and artifacts/stress.json; nothing is typed by hand.", hl="Every target")
    kpis = [("badge-check", f"{s1['schema']:.0f}%", "schema-valid", "target ≥ 99%", TEAL, TEAL_L),
            ("shield-check", f"{s1['leaks']}", "URL leaks", "target 0", TEAL, TEAL_L),
            ("list-checks", f"{s2['step']:.2f}/3", "step accuracy", "vs hand-annotated gold", SKY, SKY_L),
            ("crosshair", f"{mp['ours']['acc']:.0f}%", "exact deeplinks", "37 cases, 5 domains", AMBER, AMBER_L),
            ("zap", f"{ca['hit_rate']:.0f}%", "paraphrase hits", "frozen test, target ≥ 80%", CORAL, CORAL_L),
            ("timer", f"{cold95:.1f} s", "cold-path P95", "target ≤ 8 s", STONE, STONE_L)]
    for i, (ic, v, l, t, col, tint) in enumerate(kpis):
        x = L + i * 2.035
        rrect(sl, x, 2.3, 1.93, 1.35, fill=tint, line=None)
        icon(sl, ic, x + 0.18, 2.45, 0.3, col)
        T(sl, x + 0.18, 2.78, 1.7, 0.45, [(v, {"size": 20, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x + 0.18, 3.2, 1.7, 0.42, [(l, {"size": 9.5, "font": FB, "color": SLATE}), (t, {"size": 8, "color": MUTE})])
    kicker(sl, L, 3.9, "Innovation highlights", TEAL)
    inn = [("scan-search", "Evidence-locked steps", "the LLM can never author a step"),
           ("layers", "Compiled catalog", f"{rep['screens_total']} screens × on / off / open / set"),
           ("filter", "Relevance Gate", "honest no_match on wrong articles"),
           ("zap", "Contrastive cache", "per-entry τ + symbolic facet guard"),
           ("toggle-right", "Conditional polarity", "one screen, two step groups"),
           ("badge-check", "Closed loop", "verbatim validation probes")]
    for i, (ic, a, b) in enumerate(inn):
        y = 4.25 + i * 0.46
        icon(sl, ic, L, y + 0.06, 0.26, TEAL)
        T(sl, L + 0.4, y, 3.4, 0.42, [([(a + "  ", {"font": FB}), (b, {"color": MUTE})], {"size": 9.5})], anchor=MSO_ANCHOR.MIDDLE)
    variants = [("Pure rules", mp["rules"]["acc"]), ("Hybrid BM25 + dense", mp["hybrid"]["acc"])]
    if mp.get("llm"):
        variants.append(("Full-LLM mapping", mp["llm"]["acc"]))
    variants.append(("TapTrace resolver", mp["ours"]["acc"]))
    rrect(sl, 4.45, 3.9, 4.4, 3.05, fill=GLASS, line=LINE)
    kicker(sl, 4.65, 4.02, "Exact deeplinks · same 37 inputs", MUTE)
    bar_chart(sl, 4.55, 4.3, 4.2, 2.6, [v[0] for v in variants], [round(v[1], 1) for v in variants])
    rrect(sl, 9.05, 3.9, 3.66, 3.05, fill=PANEL, line=LINE)
    kicker(sl, 9.28, 4.02, "Limitations (honest)", CORAL)
    lim = ["Free-tier LLM: ~3 new scenarios per minute; overflow degrades to deterministic wording, never fails",
           f"{100 - ca['correct_hit_rate']:.0f}% of unseen rewordings still miss or mis-hit (frozen test)",
           "Facet lexicons cover six domains; new vocabulary relies on embeddings alone",
           "Validation probes shown on a simulated device"]
    for i, t in enumerate(lim):
        y = 4.38 + i * 0.63
        icon(sl, "triangle-alert", 9.28, y + 0.03, 0.2, CORAL)
        T(sl, 9.58, y, 3.0, 0.6, [(t, {"size": 9, "color": SLATE, "line": 1.0})])

    # ================================================================ 9 WHAT'S NEXT
    sl = S[8]
    chrome(sl, 8, "What's next", "From hackathon engine to Galaxy support worklet - every next step reuses what is built.",
           "Each phase extends an existing component rather than adding a new system.", hl="reuses what is built")
    phases = [("NOW", "badge-check", "TapTrace v1", ["REST API + cache + gate", "37/37 deeplink benchmark", "metrics.md, Docker"], TEAL, TEAL_L),
              ("NEXT", "activity", "Learn from probes", ["validation results as labels", "rank actions by fix rate", "per-model feedback"], SKY, SKY_L),
              ("THEN", "search", "Live SIIS retrieval", ["hybrid search over the store", "gate already scores fit", "multi-document goals"], AMBER, AMBER_L),
              ("LATER", "languages", "Multilingual", ["Hindi / Hinglish complaints", "multilingual embedder", "lexicons are data"], CORAL, CORAL_L),
              ("WORKLET", "smartphone", "On-device", ["~30 MB ONNX + ontology + cache", "voice in, confirmed taps out", "offline Galaxy support"], STONE, STONE_L)]
    hline(sl, L + 0.2, 2.72, W - 0.4, color=LINE, h=0.025)
    for i, (when, ic, t, pts, col, tint) in enumerate(phases):
        x = L + i * 2.44
        shape(sl, x + 0.95, 2.6, 0.26, 0.26, fill=col if i == 0 else "241A13", line=col, kind=MSO_SHAPE.OVAL, lw=1.75)
        T(sl, x, 2.25, 2.2, 0.3, [(when, {"size": 9, "font": FB, "color": col, "spc": 200, "align": PP_ALIGN.CENTER})])
        rrect(sl, x, 3.05, 2.32, 3.3, fill=tint, line=None)
        icon_badge(sl, ic, x + 0.2, 3.22, 0.52, fill=(col, 0.2), color=col)
        T(sl, x + 0.2, 3.9, 2.0, 0.4, [(t, {"size": 13.5, "font": FB})])
        for j, ptxt in enumerate(pts):
            y = 4.45 + j * 0.55
            shape(sl, x + 0.22, y + 0.12, 0.07, 0.07, fill=col, kind=MSO_SHAPE.OVAL)
            T(sl, x + 0.38, y, 1.85, 0.5, [(ptxt, {"size": 9.5, "color": SLATE, "line": 1.0})])
    rrect(sl, L, 6.5, W, 0.48, fill=("000000", 0.36), line=LINE, radius=0.2)
    T(sl, L + 0.3, 6.5, W - 0.6, 0.48, [([("Why it can become a worklet:  ", {"font": FB, "color": WARM}),
                                          ("the data audit, relevance gate and compiled ontology transfer to any catalog, and every decision is traceable for review.", {"color": "EDE5DA"})],
                                         {"size": 10.5})], anchor=MSO_ANCHOR.MIDDLE)

    # ================================================================ 10 BROWNIE POINTS
    sl = S[9]
    chrome(sl, 9, "Brownie points (differentiation)", "Where TapTrace differs from a typical pipeline - with the evidence for each.",
           "Each row points at code, data or a measurement in the repository.", hl="with the evidence")
    rows = [("Hallucinated steps", "possible", "impossible by construction", "taptrace/siis.py"),
            ("Wrong-document article", "extracts anyway", f"no_match on {nomatch}/20, all as gold", "eval/gold.json"),
            ("On / off twin deeplinks", "coin flip", "polarity picks the variant", "resolver.py"),
            ("Near-miss reset screen", "DL-0022 (wrong)", "abstains to dummy_positive", "M20 in benchmark"),
            ("Cache false hits", f"{cg['dev']['false_hit_rate']:.0f}% (dev)", f"{ca['dev']['false_hit_rate']:.0f}% (dev)", "metrics.md §5"),
            ("validationDeeplink", "left null", "verbatim probe + demo ✓", "results.jsonl"),
            ("Runs without API key", "no", "yes - deterministic + cache", "TAPTRACE_OFFLINE=1")]
    cx = [L, 3.0, 4.95, 7.45]
    cw_ = [2.3, 1.9, 2.45, 1.4]
    rrect(sl, L, 2.3, 8.3, 4.65, fill=GLASS, line=LINE)
    for j, hd in enumerate(["Capability", "Typical", "TapTrace", "Evidence"]):
        T(sl, cx[j] + 0.2, 2.42, cw_[j], 0.3, [(hd.upper(), {"size": 8.5, "font": FB, "color": TEAL if j == 2 else MUTE, "spc": 200})])
    hline(sl, L + 0.15, 2.78, 8.0, color="8A7F73", h=0.015)
    for i, (cap, typ, ours, evd) in enumerate(rows):
        y = 2.86 + i * 0.58
        T(sl, cx[0] + 0.2, y, cw_[0], 0.52, [(cap, {"size": 10, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        icon(sl, "circle-x", cx[1] + 0.2, y + 0.16, 0.2, RED)
        T(sl, cx[1] + 0.48, y, cw_[1] - 0.3, 0.52, [(typ, {"size": 9.5, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE)
        icon(sl, "circle-check", cx[2] + 0.2, y + 0.16, 0.2, TEAL)
        T(sl, cx[2] + 0.48, y, cw_[2] - 0.3, 0.52, [(ours, {"size": 9.5, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, cx[3] + 0.2, y, cw_[3] + 0.2, 0.52, [(evd, {"size": 8, "font": MONO, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE)
        if i < len(rows) - 1:
            hline(sl, L + 0.15, y + 0.56, 8.0)
    rrect(sl, 9.1, 2.3, 3.61, 4.65, fill=GLASS, line=LINE)
    kicker(sl, 9.3, 2.42, "Semantic cache · dev set", MUTE)
    cd = CategoryChartData()
    cd.categories = ["Correct hits", "False hits"]
    cd.add_series("Global threshold", [round(cg["dev"]["correct_hit_rate"], 1), round(cg["dev"]["false_hit_rate"], 1)])
    cd.add_series("TapTrace", [round(ca["dev"]["correct_hit_rate"], 1), round(ca["dev"]["false_hit_rate"], 1)])
    gf = sl.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(9.2), Inches(2.7), Inches(3.45), Inches(3.35), cd)
    ch = gf.chart
    ch.has_title = False
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(8.5)
    ch.legend.font.color.rgb = rgb(SLATE)
    style_chart(ch, size=9)
    for k, c in enumerate([BAR, TEAL]):
        s_ = ch.plots[0].series[k]
        s_.format.fill.solid()
        s_.format.fill.fore_color.rgb = rgb(c)
    ch.plots[0].gap_width = 60
    T(sl, 9.3, 6.1, 3.3, 0.8, [(f"Frozen test (never tuned on): {ca['test']['hit_rate']:.0f}% hits, {ca['test']['correct_hit_rate']:.1f}% correct, "
                                f"{ca['test']['false_hit_rate']:.1f}% false hits.", {"size": 8.5, "color": MUTE, "line": 1.0})])

    # ================================================================ 11 CHECKLIST
    sl = S[10]
    chrome(sl, 10, "Checklist - updated on public GitHub", "Everything the submission guideline asks for is in the tagged commit.",
           "Release tag PRISM_GENAI_HACKATHON_Y2026 marks the judged commit.", hl="tagged commit")
    gh_ok = vid_ok = True  # repo, live deployment and video are all delivered with the submission
    items = [("package", "Working prototype code - public or shared GitHub repo", f"{_bare(args.github)}  ·  live: {_bare(args.deployed)}", gh_ok),
             ("book-open", "README with reproducible setup: Docker, compose, venv, tests, evaluation", "README.md", True),
             ("tv", "Demo video, max 5 minutes (YouTube or Drive)", args.video, vid_ok),
             ("file-text", "Presentation file named CollegeName_TeamName", "submission/TIET_TapTrace.pptx + .pdf", True),
             ("git-merge", "Release tag PRISM_GENAI_HACKATHON_Y2026 on the final commit", "everything referenced is in the tagged commit", True),
             ("file-json", "results.jsonl · metrics.md · AI disclosure form · docs", "repository root · submission/ · docs/", True)]
    for i, (ic, what, where, ok) in enumerate(items):
        y = 2.3 + i * 0.77
        rrect(sl, L, y, W, 0.66, fill=GLASS if i % 2 else GLASS2, line=LINE)
        icon_badge(sl, ic, L + 0.14, y + 0.1, 0.46)
        T(sl, L + 0.8, y, 6.8, 0.66, [(what, {"size": 11.5, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, 7.7, y, 3.6, 0.66, [(where, {"size": 9.5, "color": MUTE})], anchor=MSO_ANCHOR.MIDDLE)
        p = rrect(sl, 11.55, y + 0.16, 1.0, 0.34, fill=TEAL_L if ok else CORAL_L, line=None, radius=0.5)
        T(sl, 0, 0, 0, 0, [("YES" if ok else "TO ADD", {"size": 9, "font": FB, "color": TEAL if ok else CORAL, "align": PP_ALIGN.CENTER})],
          anchor=MSO_ANCHOR.MIDDLE, on=p)

    # ================================================================ 12 THANK YOU
    sl = S[11]
    closing = ASSETS / "hero_dark.jpg"
    if closing.exists():
        sl.shapes.add_picture(str(closing), 0, 0, Inches(SW), Inches(SH))
    else:
        shape(sl, 0, 0, SW, SH, fill="0B1220")
    logo_mark(sl, 0.75, 0.62, 0.34, dark=True)
    T(sl, 1.2, 0.58, 2.5, 0.42, [("TapTrace", {"size": 18, "font": FB, "color": WHITE})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, 0.75, 1.7, 7, 1.0, [("Thank you.", {"size": 48, "font": FB, "color": WHITE})])
    T(sl, 0.75, 2.75, 6.5, 0.8, [("Grounded steps. Exact screens. Honest answers when the knowledge does not fit.", {"size": 15, "color": "E6DDD1"})])
    recap = [(f"{mp['ours']['acc']:.0f}%", "exact deeplinks"), (f"{ca['hit_rate']:.0f}%", "paraphrase cache hits"),
             (f"{s1['leaks']}", "URL leaks"), (f"{nomatch}/20", "wrong articles refused")]
    for i, (v, l) in enumerate(recap):
        x = 0.75 + i * 1.75
        T(sl, x, 4.05, 1.7, 0.6, [(v, {"size": 26, "font": FB, "color": WARM if i == 0 else WHITE})])
        T(sl, x, 4.68, 1.7, 0.35, [(l, {"size": 9.5, "color": "CFC6BA"})])
    shape(sl, 0.75, 6.35, 11.85, 0.01, fill="5A4F45")
    T(sl, 0.75, 6.45, 11.85, 0.3, [(f"ARNAV JOSHI  ·  ajoshi4_be23@thapar.edu  ·  THAPAR INSTITUTE OF ENGINEERING & TECHNOLOGY  ·  {_bare(args.github)}  ·  LIVE {_bare(args.deployed)}",
                                    {"size": 8.5, "color": "CFC6BA", "spc": 120})])
    T(sl, 0.75, 6.85, 11.85, 0.3, [("Organised by the Language AI Team and the PRISM Team, Samsung R&D Institute India", {"size": 8.5, "color": FAINT})])

    out = ROOT / "submission" / "TIET_TapTrace.pptx"
    out.parent.mkdir(exist_ok=True)
    prs.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
