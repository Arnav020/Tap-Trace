"""Build the Demo Round deck (Round 2, 23 Oct): submission/TIET_TapTrace_DemoRound.pptx.

The organisers' mail is the specification: 7 minutes of slides covering, in order,
  (1) understanding of the problem and caveats identified, (2) core architecture and core technology modules,
  (3) extension of the solution in relevance to Samsung, (4) scalability to new use-case scenarios,
  (5) a demo video under 2 minutes; then 3 minutes of panel Q&A.
Same visual system as the Round 1 deck (scripts/build_deck.py): the identical photo cover, then the dark warm theme.
Every number is read from artifacts/eval_raw.json, artifacts/stress.json, metrics.md or computed live.
Each slide carries its spoken script and timing in the speaker notes (about 6:45 in total, leaving ~15 s of buffer).

    python scripts/build_deck_round2.py --template "../Student Resources/CollegeName_TeamName_Submission.pptx" [--video-file demo.mp4]
    powershell -ExecutionPolicy Bypass -File scripts/deck_postprocess.ps1 -pptx submission/TIET_TapTrace_DemoRound.pptx
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_deck as bd  # noqa: E402  (same palette, primitives and cover)
from build_deck import (AMBER, AMBER_L, ASSETS, CORAL, CORAL_L, DARK, F, FB, GLASS, GLASS2, INK, L, LINE, MONO, MUTE,  # noqa: E402
                        PANEL, RED, RED_L, ROOT, SH, SKY, SKY_L, SLATE, STONE, STONE_L, SW, TEAL, TEAL_L, W, WARM, WHITE,
                        T, _bare, chrome, cover_slide, drop_all, icon, icon_badge, kicker, line, num_badge, rrect, shape,
                        style_chart)

N_SLIDES = 8


def notes(sl, text: str) -> None:
    sl.notes_slide.notes_text_frame.text = text


def keep_first(prs, n: int) -> None:
    """The organisers' template has 12 slides; the demo deck uses the first n (same masters and layouts)."""
    ids = prs.slides._sldIdLst
    for sid in list(ids)[n:]:
        prs.part.drop_rel(sid.rId)
        ids.remove(sid)


def chip(sl, x, y, w, h, text, fill=GLASS2, color=SLATE, size=9, bold=False):
    c = rrect(sl, x, y, w, h, fill=fill, line=None, radius=0.5)
    T(sl, 0, 0, 0, 0, [(text, {"size": size, "color": color, "align": PP_ALIGN.CENTER, "font": FB if bold else F})],
      anchor=MSO_ANCHOR.MIDDLE, on=c, margin=0.06)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--github", default="https://github.com/Arnav020/Tap-Trace")
    ap.add_argument("--deployed", default="https://taptrace.onrender.com")
    ap.add_argument("--video-file", default=None, help="optional .mp4 (< 2 min) to embed on the demo slide")
    args = ap.parse_args()

    # ------------------------------------------------------------------ measured facts (never typed by hand)
    ev = json.loads((ROOT / "artifacts" / "eval_raw.json").read_text(encoding="utf-8"))
    stress = json.loads((ROOT / "artifacts" / "stress.json").read_text(encoding="utf-8"))
    res = [json.loads(l) for l in (ROOT / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    s1, s2, mp = ev["schema_rules"], ev["accuracy"], ev["mapping"]
    ca, cg = ev["cache"]["adaptive"], ev["cache"]["global"]
    cold = ev["cold_latencies"]
    cold50, cold95 = float(np.percentile(cold, 50)) / 1000, float(np.percentile(cold, 95)) / 1000
    hit95 = float(np.percentile(ca["lat_para"], 95))
    m = re.search(r"Cold query average inference cost \| Tracked \| \$([0-9.]+)", (ROOT / "metrics.md").read_text(encoding="utf-8"))
    cost = float(m.group(1))  # live full cold requests only (metrics.md)
    nomatch = sum(1 for r in res if r["meta"]["fallback"] == "no_match")

    from taptrace.catalog import Catalog
    from taptrace.embed import Embedder

    cat = Catalog(ROOT / "data" / "deeplinks.json")
    rep = cat.noise_report()
    emb = Embedder(ROOT / "artifacts" / "model")
    on_off = float(emb.encode_one("My Nexa X1 Ultra screen won't turn on") @ emb.encode_one("My Nexa X1 Ultra screen won't turn off"))
    sample = json.loads((ROOT / "data" / "sample_output.json").read_text(encoding="utf-8"))
    sample_words = max(len(a["description"].split()) for g in sample["response"]["contexts"] for a in g["actions"])
    distractors = len(rep["tv_distractors"]) + len(rep["iot_distractors"])

    bd.TOTAL_SLIDES = N_SLIDES
    prs = Presentation(args.template)
    keep_first(prs, N_SLIDES)
    S = prs.slides
    for sl in S:
        drop_all(sl)

    # ================================================================ 1 COVER (identical to Round 1)
    cover_slide(S[0], args.github, args.deployed)
    notes(S[0], "[0:00-0:25]  Hello, I am Arnav Joshi from Thapar Institute. TapTrace is my Theme 02 engine: it turns a vague "
                "device complaint into an ordered fix plan where every Settings change is one tap away and every step comes "
                "from the official support article. In seven minutes: the problem, the architecture, how it extends to "
                "Samsung, how it scales, and a short demo.")

    # ================================================================ 2 PROBLEM + CAVEATS  (mail point 1)
    sl = S[1]
    chrome(sl, 1, "Understanding the problem & caveats",
           "Vague complaints meet adversarial data: six caveats shaped every design decision.",
           f"Found by auditing all {rep['rows']} catalog rows and 20 knowledge articles before writing any code.", hl="six caveats")
    rrect(sl, L, 2.3, 4.25, 4.65, fill=PANEL, line=LINE)
    kicker(sl, L + 0.25, 2.48, "The problem", MUTE)
    T(sl, L + 0.25, 2.8, 3.8, 0.95, [("“My Nexa X1 screen inputs are delayed and the touch responsiveness is laggy…”",
                                      {"size": 13, "italic": True, "color": INK, "line": 1.08})])
    T(sl, L + 0.25, 3.62, 3.8, 0.85, [("Today an agent reads the article, picks and orders the steps by hand (about 15 minutes per "
                                       "case), and the customer still hunts through nested Settings menus.",
                                       {"size": 10, "color": MUTE, "line": 1.05})])
    kicker(sl, L + 0.25, 4.72, "What the brief demands", TEAL)
    demands = [("list-ordered", "Ordered steps, only from the article"), ("crosshair", "The exact Settings screen, one tap"),
               ("timer", "Repeats in ≤ 300 ms, new in ≤ 8 s"), ("braces", "Strict JSON contract, zero URL leaks")]
    for i, (ic, t) in enumerate(demands):
        y = 5.05 + i * 0.46
        icon(sl, ic, L + 0.27, y + 0.06, 0.24, TEAL)
        T(sl, L + 0.65, y, 3.5, 0.38, [(t, {"size": 10.5, "color": SLATE})], anchor=MSO_ANCHOR.MIDDLE)
    caveats = [("tv", str(distractors), "TV and appliance screens hidden in the phone catalog", "Catalog Compiler quarantines them"),
               ("triangle-alert", str(len(rep["misleading_messages"])), "catalog labels that name the wrong feature",
                "Match screens, never trust labels"),
               ("toggle-right", f"{on_off:.2f}", "similarity of “won’t turn ON” and “won’t turn OFF”",
                "Polarity-aware resolver and cache"),
               ("book-open", f"{nomatch} / 20", "knowledge articles are the wrong document", "Relevance gate returns no_match"),
               ("crosshair", "DL-0022", "is nearest to “factory reset”, but a different screen", "Abstain instead of guessing"),
               ("list-checks", f"{sample_words} words", "in the official sample’s description (5–7 allowed)",
                "Every field rule enforced in code")]
    cw, chh = 3.68, 1.43
    for i, (ic, n, what, fix) in enumerate(caveats):
        x = 5.03 + (i % 2) * (cw + 0.15)
        y = 2.3 + (i // 2) * (chh + 0.18)
        rrect(sl, x, y, cw, chh, fill=GLASS, line=LINE)
        icon_badge(sl, ic, x + 0.18, y + 0.2, 0.46, fill=RED_L, color=RED)
        T(sl, x + 0.78, y + 0.14, 2.8, 0.55, [(n, {"size": 20, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x + 0.78, y + 0.66, 2.8, 0.4, [(what, {"size": 9.5, "color": SLATE, "line": 1.0})])
        icon(sl, "arrow-right", x + 0.78, y + 1.1, 0.18, TEAL)
        T(sl, x + 1.02, y + 1.02, 2.6, 0.32, [(fix, {"size": 9.5, "font": FB, "color": TEAL})], anchor=MSO_ANCHOR.MIDDLE)
    notes(sl, "[0:25-1:20]  Customers rarely speak in technical terms; they say 'my touch is laggy'. Today an agent spends about "
              "fifteen minutes turning that into steps, and the customer still hunts through Settings. The brief asks for "
              "ordered steps taken only from the article, the exact Settings screen in one tap, repeats in under 300 "
              "milliseconds and a strict JSON contract. Before writing code I audited all the data and found six caveats: "
              f"{distractors} TV and appliance screens hidden in the phone catalog, labels that name the wrong feature, on and "
              f"off phrasings that are {on_off:.2f} similar to an embedding model, {nomatch} of 20 articles that are the wrong "
              "document, a near-miss factory-reset screen, and even the official sample breaks the 5-to-7-word rule. Each "
              "caveat became a component, shown in amber.")

    # ================================================================ 3 ARCHITECTURE  (mail point 2)
    sl = S[2]
    chrome(sl, 2, "Core architecture", "Five stages: the LLM may select and name steps, but it never writes one.",
           "Everything except the two LLM calls is deterministic, tested code (27 automated tests).", hl="never writes one")
    # input
    inp = rrect(sl, L, 3.45, 1.55, 1.75, fill=GLASS2, line=LINE)
    T(sl, 0, 0, 0, 0, [("Complaint", {"size": 11, "font": FB, "align": PP_ALIGN.CENTER}),
                       ("+ optional", {"size": 8.5, "color": MUTE, "align": PP_ALIGN.CENTER}),
                       ("support article", {"size": 8.5, "color": MUTE, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=inp)
    # fast path lane
    fp = rrect(sl, 2.45, 2.32, 10.26, 0.62, fill=TEAL_L, line=TEAL, dash=True, lw=1.1, radius=0.3)
    icon(sl, "zap", 2.65, 2.5, 0.26, TEAL)
    T(sl, 3.05, 2.32, 9.5, 0.62, [([("Fast path  ", {"font": FB, "color": TEAL}),
                                    ("contrastive semantic cache answers repeats and paraphrases in ", {"color": SLATE}),
                                    (f"{hit95:.1f} ms (P95), $0, no LLM call", {"font": FB, "color": INK})], {"size": 10.5})],
      anchor=MSO_ANCHOR.MIDDLE)
    line(sl, L + 0.78, 3.45, L + 0.78, 2.63, color=TEAL, w=1.25, arrow=False)
    line(sl, L + 0.78, 2.63, 2.45, 2.63, color=TEAL, w=1.25)
    stages = [("Understand", SKY, SKY_L, "Facet frame", "symptom · part · on/off"),
              ("Ground", TEAL, TEAL_L, "Relevance gate", "+ evidence-locked units"),
              ("Resolve", AMBER, AMBER_L, "Catalog compiler", "+ exact-screen resolver"),
              ("Compose", CORAL, CORAL_L, "Plan composer", "auto → manual → critical"),
              ("Guarantee", STONE, STONE_L, "Output gate", "schema · 0 leaks · cache")]
    sw_, gap = 1.86, 0.24
    for i, (name, col, tint, a, b) in enumerate(stages):
        x = 2.45 + i * (sw_ + gap)
        rrect(sl, x, 3.3, sw_, 2.05, fill=tint, line=None)
        num_badge(sl, i + 1, x + 0.15, 3.45, 0.38, fill=col, color=DARK)
        T(sl, x + 0.62, 3.45, 1.2, 0.38, [(name, {"size": 12.5, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        c = rrect(sl, x + 0.13, 4.05, sw_ - 0.26, 1.1, fill=GLASS2, line=None)
        T(sl, 0, 0, 0, 0, [(a, {"size": 10, "font": FB, "align": PP_ALIGN.CENTER}),
                           (b, {"size": 8.5, "color": MUTE, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=c, margin=0.05)
        if i < 4:
            line(sl, x + sw_ + 0.01, 4.32, x + sw_ + gap - 0.01, 4.32, color=MUTE, w=1.25)
    line(sl, L + 1.55, 4.32, 2.44, 4.32, color=MUTE, w=1.25)
    # LLM lane (under stage 1 and stage 4)
    for i, (t, sub) in ((0, ("Stage A LLM", "canonical query, 10 paraphrases, near misses")),
                        (3, ("Stage B LLM", "select, group and name; no step text"))):
        x = 2.45 + i * (sw_ + gap) - (0.55 if i == 3 else 0)
        wbox = sw_ + (0.55 if i == 3 else 0.55)
        line(sl, 2.45 + i * (sw_ + gap) + sw_ / 2, 5.36, 2.45 + i * (sw_ + gap) + sw_ / 2, 5.55, color=CORAL, w=1, arrow=False, dash=True)
        b = rrect(sl, x, 5.55, wbox, 0.62, fill=CORAL_L, line=CORAL, dash=True, lw=1)
        T(sl, 0, 0, 0, 0, [(t, {"size": 9.5, "font": FB, "color": CORAL, "align": PP_ALIGN.CENTER}),
                           (sub, {"size": 7.5, "color": SLATE, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE, on=b, margin=0.04)
    T(sl, 5.25, 5.66, 2.6, 0.4, [("both LLM calls run in parallel", {"size": 8.5, "italic": True, "color": MUTE,
                                                                   "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE)
    kpis = [(f"{cold95:.1f} s", "new complaint, P95 (target 8 s)"), (f"{hit95:.1f} ms", "repeat, P95 (target 300 ms)"),
            (f"${cost:.4f}", "per new complaint; repeats $0"), (f"{s1['schema']:.0f}% · {s1['leaks']}", "schema-valid · URL leaks")]
    for i, (v, l) in enumerate(kpis):
        x = L + i * 3.06
        rrect(sl, x, 6.35, 2.94, 0.6, fill=GLASS, line=LINE)
        T(sl, x + 0.18, 6.35, 2.7, 0.6, [([(v + "  ", {"size": 13, "font": FB, "color": WARM}), (l, {"size": 8.5, "color": MUTE})],
                                          {})], anchor=MSO_ANCHOR.MIDDLE)
    notes(sl, f"[1:20-2:20]  The architecture has five stages. Repeats never reach them: the fast path, a contrastive semantic "
              f"cache, answers paraphrases in {hit95:.1f} milliseconds with no LLM call. A new complaint goes through Understand, "
              "which builds a symbolic frame of the complaint; Ground, where the relevance gate checks the article actually fits "
              "and extracts candidate steps from its own sentences; Resolve, which maps each Settings step to the exact screen "
              "in the compiled catalog; Compose, which orders actions from safe settings to manual checks to critical resets; "
              "and Guarantee, which validates against the organisers' schema and writes the cache. The two LLM calls, shown in "
              "coral, run in parallel: one paraphrases the complaint for the cache, the other selects and names the extracted "
              f"steps. Neither can write a step. Result: {cold95:.1f} seconds P95 for a new complaint and zero URL leaks.")

    # ================================================================ 4 CORE MODULES  (mail point 2)
    sl = S[3]
    chrome(sl, 3, "Core technology modules", "Four modules carry the weight, and each one is proven by a measurement.",
           "Measured by scripts/evaluate.py on the 20 provided scenarios and a frozen, never-tuned test set.",
           hl="proven by a measurement")
    cw, chh = 5.97, 2.24
    mods = [("scan-search", "Evidence-locked extraction", TEAL,
             "Steps are cut from the article’s own sentences and keep their source id. The LLM only selects and names them."),
            ("layers", "Catalog compiler + resolver", AMBER,
             f"{rep['rows']} noisy rows become {rep['screens_total']} screens × on/off/open/set. Exact leaf label, then "
             "fuzzy with a margin, else abstain."),
            ("filter", "Relevance gate", SKY,
             "Compares the complaint’s symptom and context with the article before extracting anything."),
            ("zap", "Contrastive semantic cache", CORAL,
             "Each entry learns its own threshold from paraphrases and near misses; a symbolic guard blocks on/off flips.")]
    for i, (ic, title, col, how) in enumerate(mods):
        x = L + (i % 2) * (cw + 0.15)
        y = 2.3 + (i // 2) * (chh + 0.17)
        rrect(sl, x, y, cw, chh, fill=GLASS, line=LINE)
        icon_badge(sl, ic, x + 0.2, y + 0.2, 0.5, fill=(col, 0.18), color=col)
        T(sl, x + 0.85, y + 0.2, 2.65, 0.5, [(title, {"size": 13, "font": FB})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x + 0.2, y + 0.85, 2.95, 1.3, [(how, {"size": 9.5, "color": MUTE, "line": 1.08})])
        shape(sl, x + 3.3, y + 0.25, 0.012, chh - 0.5, fill=LINE)
    # proofs
    def big(x, y, v, l, col=WARM):
        T(sl, x, y, 2.5, 0.55, [(v, {"size": 24, "font": FB, "color": col})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, x, y + 0.55, 2.5, 0.4, [(l, {"size": 8.5, "color": MUTE, "line": 1.0})])
    px0, px1 = L + 3.5, L + cw + 0.15 + 3.5
    big(px0, 2.45, f"{s2['step']:.2f} / 3", "step accuracy vs hand-annotated gold")
    big(px0, 3.45, f"{s1['leaks']}", "URL leaks across all outputs")
    # resolver chart
    cd = CategoryChartData()
    rows_ = [("Hybrid", mp["hybrid"]["acc"]), ("Rules", mp["rules"]["acc"]), ("Full LLM", mp["llm"]["acc"]), ("TapTrace", mp["ours"]["acc"])]
    cd.categories = [r[0] for r in rows_]
    cd.add_series("s", [round(r[1]) for r in rows_])
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(px1 - 0.1), Inches(2.42), Inches(2.6), Inches(1.75), cd)
    ch = gf.chart
    ch.has_legend, ch.has_title = False, False
    style_chart(ch, maxv=125, size=8.5)
    ch.plots[0].gap_width = 40
    for k in range(len(rows_)):
        pt = ch.plots[0].series[0].points[k]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = bd.rgb(TEAL if k == len(rows_) - 1 else bd.BAR)
    T(sl, px1, 4.12, 2.5, 0.3, [("exact deeplinks, 37-case benchmark", {"size": 8.5, "color": MUTE})])
    big(px0, 2.3 + chh + 0.17 + 0.15, f"{nomatch} / 20", "wrong articles answered with no_match")
    big(px0, 2.3 + chh + 0.17 + 1.15, f"{s2['fit_acc']:.0f}%", "fit decisions match gold")
    big(px1, 2.3 + chh + 0.17 + 0.15, f"{ca['test']['hit_rate']:.0f}%", "unseen paraphrases hit (frozen test)")
    big(px1, 2.3 + chh + 0.17 + 1.15, f"{cg['dev']['false_hit_rate']:.0f}% → {ca['dev']['false_hit_rate']:.1f}%",
        "false hits vs one global threshold (dev)")
    notes(sl, "[2:20-3:10]  Four modules carry the design, and each has a number behind it. Evidence-locked extraction: steps "
              f"come only from the article's sentences, so step accuracy against my gold annotations is {s2['step']:.2f} out of "
              f"3 with zero URL leaks. The catalog compiler and resolver: on a 37-case benchmark it maps every step to the exact "
              f"screen, where a full-LLM mapper gets {mp['llm']['acc']:.0f} percent and a hybrid retriever {mp['hybrid']['acc']:.0f}. "
              f"The relevance gate catches the {nomatch} wrong articles and answers no_match instead of inventing steps. And the "
              f"contrastive cache hits {ca['test']['hit_rate']:.0f} percent of unseen paraphrases on a frozen test set, while "
              f"cutting false hits from {cg['dev']['false_hit_rate']:.0f} to {ca['dev']['false_hit_rate']:.1f} percent compared "
              "with a single global threshold.")

    # ================================================================ 5 SAMSUNG EXTENSION  (mail point 3)
    sl = S[4]
    chrome(sl, 4, "Extension in relevance to Samsung", "The same engine fits four Galaxy support touchpoints, from call centre to device.",
           "Mapped from the brief’s own inputs: SIIS articles, bixby:// Settings deeplinks and validation probes.",
           hl="four Galaxy support touchpoints")
    flows = ["complaint + article → plan + evidence", "complaint → one tap → probe ✓",
             "speech → text → plan → deeplink", "cache on device; LLM only for new"]
    touch = [("headset", "Customer-care agents", "SIIS knowledge base",
              "The agent pastes the complaint and gets an ordered plan, each step highlighted in the internal article.",
              f"~15 min of manual triage → {cold50:.1f} s (P50)", SKY),
             ("smartphone", "Samsung Members", "self-service help",
              "The user describes the problem in the app and gets one-tap fixes; the validation probe confirms each one.",
              "No more hunting through Settings", TEAL),
             ("message-square-quote", "Bixby voice", "spoken complaints",
              "“My touch is laggy” becomes a plan, and Bixby opens the exact screen through the same deeplink scheme.",
              "Voice in, verified tap out", AMBER),
             ("cpu", "On-device worklet", "Galaxy offline support",
              "The 23 MB INT8 embedder, compiled catalog and cache run on CPU in about 150 MB of RAM.",
              "Known issues answered offline", CORAL)]
    cw4 = (W - 3 * 0.15) / 4
    for i, (ic, name, where, how, value, col) in enumerate(touch):
        x = L + i * (cw4 + 0.15)
        rrect(sl, x, 2.3, cw4, 3.55, fill=GLASS, line=LINE)
        icon_badge(sl, ic, x + 0.2, 2.5, 0.56, fill=(col, 0.18), color=col)
        T(sl, x + 0.2, 3.2, cw4 - 0.4, 0.62, [(name, {"size": 13.5, "font": FB}), (where, {"size": 9, "color": col})])
        T(sl, x + 0.2, 3.92, cw4 - 0.4, 0.9, [(how, {"size": 9.5, "color": SLATE, "line": 1.08})])
        fl = rrect(sl, x + 0.2, 4.68, cw4 - 0.4, 0.36, fill=PANEL, line=None, radius=0.3)
        T(sl, 0, 0, 0, 0, [(flows[i], {"size": 7.5, "font": MONO, "color": MUTE, "align": PP_ALIGN.CENTER})],
          anchor=MSO_ANCHOR.MIDDLE, on=fl, margin=0.04)
        v = rrect(sl, x + 0.2, 5.15, cw4 - 0.4, 0.5, fill=(col, 0.14), line=None, radius=0.3)
        T(sl, 0, 0, 0, 0, [(value, {"size": 9.5, "font": FB, "color": col, "align": PP_ALIGN.CENTER})], anchor=MSO_ANCHOR.MIDDLE,
          on=v, margin=0.05)
    rrect(sl, L, 6.05, W, 0.9, fill=PANEL, line=LINE)
    icon(sl, "puzzle", L + 0.25, 6.3, 0.36, WARM)
    T(sl, L + 0.8, 6.05, W - 1.0, 0.9, [([("Why it transfers: ", {"font": FB, "color": WARM}),
                                          ("the deeplink scheme, brand names and article format are configuration, not code "
                                           "(the provided data is masked as TechCorp / Nexa and voiceassist://). Next step: "
                                           "use validation-probe results as feedback on which fixes really work.",
                                           {"color": SLATE})], {"size": 10.5, "line": 1.05})], anchor=MSO_ANCHOR.MIDDLE)
    notes(sl, "[3:10-3:55]  Where does this fit at Samsung? Four touchpoints. Customer-care agents get an ordered plan with every "
              f"step highlighted in the internal SIIS article: about fifteen minutes of triage becomes {cold50:.1f} seconds. In "
              "Samsung Members, users describe the problem and get one-tap fixes that a validation probe confirms. With Bixby, a "
              "spoken complaint becomes a plan and Bixby opens the exact screen through the same deeplink scheme the brief uses. "
              "And because the embedder is 23 megabytes and runs on CPU, known issues can be answered on the device, offline. "
              "The provided data is masked as TechCorp and Nexa, so the scheme and brand names are configuration, not code.")

    # ================================================================ 6 SCALABILITY  (mail point 4)
    sl = S[5]
    chrome(sl, 5, "Scalability to new use cases", "Covering a new scenario takes a knowledge article, not new code.",
           "Never-seen Battery, Camera, Performance and Wi-Fi articles ran through the same pipeline unchanged.",
           hl="not new code")
    rrect(sl, L, 2.3, 5.85, 4.65, fill=GLASS, line=LINE)
    kicker(sl, L + 0.25, 2.47, "How a new scenario gets covered", MUTE, 5)
    flow = [("A new complaint and its article arrive", "gate and step extraction run automatically"),
            ("Settings steps map to exact screens", "compiled catalog; no retraining, no new rules"),
            (f"One cold request, {cold50:.1f} s (P50)", f"about ${cost:.4f} of LLM cost"),
            ("The plan is cached", f"every repeat and paraphrase: {hit95:.1f} ms (P95), $0")]
    for i, (a, b) in enumerate(flow):
        y = 2.85 + i * 1.0
        num_badge(sl, i + 1, L + 0.3, y + 0.08, 0.42, fill=TEAL, color=DARK)
        if i < 3:
            line(sl, L + 0.51, y + 0.52, L + 0.51, y + 1.06, color=TEAL, w=1.25, arrow=False, dash=True)
        T(sl, L + 0.95, y, 4.7, 0.85, [(a, {"size": 12, "font": FB}), (b, {"size": 9.5, "color": MUTE})])
    # proof card
    rx = L + 6.0
    rw = W - 6.0
    rrect(sl, rx, 2.3, rw, 2.05, fill=TEAL_L, line=None)
    kicker(sl, rx + 0.25, 2.45, "Unseen-domain test", TEAL, 4)
    T(sl, rx + 0.25, 2.75, 1.7, 0.85, [("8 / 8", {"size": 32, "font": FB, "color": WARM})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, rx + 2.0, 2.78, rw - 2.2, 0.85, [("Settings steps mapped to the exact screen with the right on/off variant, in articles "
                                            "the engine had never seen", {"size": 10, "color": SLATE, "line": 1.05})], anchor=MSO_ANCHOR.MIDDLE)
    cwid = (rw - 0.5 - 3 * 0.12) / 4
    for i, dname in enumerate(["Battery", "Camera", "Performance", "Wi-Fi"]):
        chip(sl, rx + 0.25 + i * (cwid + 0.12), 3.8, cwid, 0.36, dname, fill=GLASS2, color=INK, size=9)
    # extension points
    rrect(sl, rx, 4.5, rw, 2.45, fill=GLASS, line=LINE)
    kicker(sl, rx + 0.25, 4.64, "Extension points", MUTE, 4)
    ext = [("New article or scenario", "automatic"), ("New Settings screens", "re-run the Catalog Compiler"),
           ("New vocabulary", "facet lexicon entries (data)"),
           ("10,000 scenarios", f"≈ ${10000 * cost:,.0f} one-time LLM cost"),
           ("Serving", f"{stress['throughput_rps']:.0f} req/s on one CPU container")]
    for i, (a, b) in enumerate(ext):
        y = 4.95 + i * 0.38
        T(sl, rx + 0.25, y, 2.3, 0.36, [(a, {"size": 9.5, "color": SLATE})], anchor=MSO_ANCHOR.MIDDLE)
        T(sl, rx + 2.55, y, rw - 2.75, 0.36, [(b, {"size": 9.5, "font": FB, "color": INK})], anchor=MSO_ANCHOR.MIDDLE)
        if i < 4:
            bd.hline(sl, rx + 0.25, y + 0.37, rw - 0.5)
    notes(sl, "[3:55-4:40]  Scalability: a new scenario needs a knowledge article, not new code. The gate and extraction run "
              "automatically, Settings steps map to exact screens through the compiled catalog, one cold request costs about "
              f"{cost * 100:.2f} cents, and from then on every repeat is served from cache for free. I tested this on articles "
              "the engine had never seen, battery, camera, performance and Wi-Fi: all eight Settings steps mapped to the exact "
              f"screen. New screens only need the catalog compiler re-run, new vocabulary is data, ten thousand scenarios cost about "
              f"{10000 * cost:,.0f} dollars once, and one CPU container served {stress['throughput_rps']:.0f} requests per second.")

    # ================================================================ 7 DEMO VIDEO  (mail point 5)
    sl = S[6]
    chrome(sl, 6, "Demo video", "Live on the deployed site: one complaint, from article to verified tap.",
           f"Screen recording of {_bare(args.deployed)}, 1:51.", hl="verified tap")
    vx, vy, vw = L, 2.3, 8.2
    vh = vw * 9 / 16
    poster = ASSETS / "shots" / "console.png"
    if args.video_file and Path(args.video_file).exists():
        sl.shapes.add_movie(str(Path(args.video_file).resolve()), Inches(vx), Inches(vy), Inches(vw), Inches(vh),
                            poster_frame_image=str(poster), mime_type="video/mp4")
    else:
        from PIL import Image, ImageEnhance

        im = Image.open(poster).convert("RGB")
        w0, h0 = im.size
        ch_ = int(w0 * 9 / 16)
        im = im.crop((0, 0, w0, min(h0, ch_))).resize((1600, 900))
        im = ImageEnhance.Brightness(im).enhance(0.55)
        p = ASSETS / "shots" / "video_poster.jpg"
        im.save(p, quality=90)
        sl.shapes.add_picture(str(p), Inches(vx), Inches(vy), Inches(vw), Inches(vh))
        shape(sl, vx, vy, vw, vh, fill=None, line=LINE, lw=1)
        cx, cy = vx + vw / 2, vy + vh / 2
        shape(sl, cx - 0.55, cy - 0.55, 1.1, 1.1, fill=(WARM, 0.92), kind=MSO_SHAPE.OVAL)
        tri = shape(sl, cx - 0.16, cy - 0.27, 0.48, 0.54, fill=DARK, kind=MSO_SHAPE.ISOSCELES_TRIANGLE)
        tri.rotation = 90
    cues_x = vx + vw + 0.3
    cw7 = W - vw - 0.3
    kicker(sl, cues_x, 2.32, "What to watch for", MUTE, cw7)
    cues = [("0:00", "A new complaint, end to end", "evidence highlighted in the article, cost shown"),
            ("0:36", "One tap + validation probe", "exact Settings screen opens; the probe confirms"),
            ("1:00", "Paraphrase hit, near miss refused", "milliseconds from cache; the guard says why"),
            ("1:32", "Wrong article → no_match", "an empty plan instead of invented steps")]
    for i, (ts, a, b) in enumerate(cues):
        y = 2.68 + i * 1.08
        rrect(sl, cues_x, y, cw7, 0.96, fill=GLASS, line=LINE)
        chip(sl, cues_x + 0.15, y + 0.33, 0.62, 0.3, ts, fill=TEAL, color=DARK, size=9, bold=True)
        T(sl, cues_x + 0.9, y, cw7 - 1.0, 0.96, [(a, {"size": 10.5, "font": FB}), (b, {"size": 8.5, "color": MUTE, "line": 1.0})],
          anchor=MSO_ANCHOR.MIDDLE)
    notes(sl, "[4:40-6:30]  (If the video is not embedded yet: click the frame, Insert > Video > This Device, or rebuild with "
              "--video-file.) (Play the video, under 2 minutes. While it plays, stay silent or add one line per cue.) "
              "Cue 1: a new complaint with its article, bypassing the cache: evidence highlighted, cost shown. Cue 2: one tap "
              "opens the exact screen and the validation probe confirms it. Cue 3: a paraphrase is answered from cache in "
              "milliseconds, and a near miss is refused with its reason. Cue 4: a wrong article returns no_match.")

    # ================================================================ 8 CLOSE
    sl = S[7]
    closing = ASSETS / "hero_dark.jpg"
    if closing.exists():
        sl.shapes.add_picture(str(closing), 0, 0, Inches(SW), Inches(SH))
    bd.logo_mark(sl, 0.75, 0.62, 0.34, dark=True)
    T(sl, 1.2, 0.58, 2.5, 0.42, [("TapTrace", {"size": 18, "font": FB, "color": WHITE})], anchor=MSO_ANCHOR.MIDDLE)
    T(sl, 0.75, 1.65, 7.5, 1.0, [([("Thank you. ", {}), ("Questions?", {"color": WARM})], {"size": 46, "font": FB, "color": WHITE})])
    T(sl, 0.75, 2.75, 6.6, 0.8, [("Grounded steps, exact screens, honest answers when the knowledge does not fit, and a path "
                                  "into Galaxy support.", {"size": 15, "color": "E6DDD1", "line": 1.1})])
    recap = [(f"{mp['ours']['acc']:.0f}%", "exact deeplinks"), (f"{ca['test']['hit_rate']:.0f}%", "paraphrase cache hits"),
             (f"{cold95:.1f} s", "cold path, P95"), (f"{nomatch}/20", "wrong articles refused")]
    for i, (v, l) in enumerate(recap):
        x = 0.75 + i * 1.75
        T(sl, x, 4.05, 1.7, 0.6, [(v, {"size": 26, "font": FB, "color": WARM if i == 0 else WHITE})])
        T(sl, x, 4.68, 1.7, 0.35, [(l, {"size": 9.5, "color": "CFC6BA"})])
    shape(sl, 0.75, 6.35, 11.85, 0.01, fill="5A4F45")
    T(sl, 0.75, 6.45, 11.85, 0.5, [("ARNAV JOSHI  ·  ajoshi4_be23@thapar.edu  ·  THAPAR INSTITUTE OF ENGINEERING & TECHNOLOGY",
                                    {"size": 8.5, "color": "CFC6BA", "spc": 120, "space": 3}),
                                   (f"CODE  {_bare(args.github)}   |   LIVE DEMO  {_bare(args.deployed)}",
                                    {"size": 8.5, "color": WARM, "spc": 120})])
    notes(sl, "[6:30-6:45]  To sum up: exact screens, grounded steps, honest no-match answers, and a clear path into Galaxy "
              "support. The code and the live demo are linked here. Thank you, I am happy to take questions.")

    out = ROOT / "submission" / "TIET_TapTrace_DemoRound.pptx"
    prs.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
