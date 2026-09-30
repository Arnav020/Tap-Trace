"""The two LLM stages, their prompts, and the programmatic validation of everything they return.

Stage A - Enrichment:  canonical technical query + 8-10 register-diverse paraphrases + hard negatives
                       (hard negatives are internal: they calibrate the per-entry cache threshold).
Stage B - Structuring: the LLM receives the complaint and the *grounded candidate units* (steps
                       already extracted verbatim from SIIS) and may only SELECT, ORDER-BY-INTENT,
                       GROUP into goals, NAME and DESCRIBE them. It cannot write step text, so it
                       cannot hallucinate a step or inject a URL. (U1 "evidence-locked extraction")
Both stages have deterministic fallbacks so the API never fails because a free-tier LLM is busy.
"""
from __future__ import annotations

import json
import random
import re
from typing import Dict, List, Optional, Tuple

from .facets import Frame
from .fields import (fit_description, has_leak, topic_for, valid_action_name, valid_title)
from .llm import LLM, Usage
from .text import is_title_case, normalize, raw_tokens, sentence_case, title_case, word_count

REGISTERS = ["formal", "casual", "keyword-only", "frustrated", "typo-inclusive"]

ENRICH_SYSTEM = """You normalise customer complaints about TechCorp Nexa phones and tablets for a troubleshooting engine.
Return ONLY a JSON object with keys:
  "canonical_query": one precise technical sentence (<= 20 words) stating device type, affected component, symptom and trigger. No brand names beyond what the user said. No speculation about causes.
  "variations": exactly 10 objects {"register": R, "text": T}. R cycles through formal, casual, keyword-only, frustrated, typo-inclusive (2 each).
       Every T must describe EXACTLY the same problem (same component, same symptom, same trigger, same on/off direction) as the complaint, worded differently. keyword-only = 3-7 keywords, no sentence. typo-inclusive = 1-3 realistic typos.
  "hard_negatives": exactly 4 strings that reuse similar words but describe a DIFFERENT problem needing a DIFFERENT fix (flip on/off or won't-turn-on vs won't-turn-off, change the component e.g. screen->battery, change inner->cover screen, change the trigger).
Never include URLs, e-mail addresses, markdown, or advice."""

STRUCT_SYSTEM = """You are the structuring stage of a device troubleshooting engine. You receive a customer complaint and
CANDIDATE ACTIONS whose steps were extracted verbatim from the official knowledge article. You must NOT write, add or reword steps.
Decide, using only the complaint and the candidates:
  - you MUST return an entry in "actions" for EVERY candidate id and place every kept id in exactly one goal,
  - keep=false ONLY for a candidate that clearly targets a different problem than the complaint (e.g. "charge a dead
    device for an hour" when the phone is on); restarts, resets, safe mode, damage checks and Settings changes that
    could plausibly help must be kept,
  - which step indices inside a kept candidate are irrelevant to this complaint (drop_steps, 0-based; never drop Settings navigation steps),
  - whether the complaint contains several distinct problems that different candidates address (then create several goals), else one goal.
Return ONLY JSON:
{"no_viable_solution": bool,
 "goals": [{"topic": "1-3 word Title Case noun phrase naming the problem, e.g. Black Screen",
            "title": "2-3 words, sentence case, e.g. Black screen recovery",
            "config_request": bool (true only if the user asks to change/remove a feature rather than fix a fault),
            "units": ["U1", ...]}],
 "actions": {"U1": {"keep": bool, "name": "Title Case, 2-6 words, starts with a verb, names ONE screen/feature",
                    "description": "Starts with 'It will', exactly 5 to 7 words total, plain-language benefit, no period",
                    "drop_steps": []}}}
No URLs, no markdown, no extra keys."""


# ----------------------------------------------------------------------------- Stage A
def enrich(llm: LLM, query: str, cf: Frame, timeout: float = 6.0, wait_budget: float = 0.0) -> Tuple[dict, Usage]:
    data, usage = llm.json_call(ENRICH_SYSTEM, f"Complaint: {query}", max_tokens=1400, timeout=timeout,
                                wait_budget=wait_budget)
    out = {"canonical_query": "", "variations": [], "hard_negatives": [], "source": "deterministic"}
    if isinstance(data, dict):
        cq = normalize(str(data.get("canonical_query") or ""))
        if cq and not has_leak(cq) and word_count(cq) <= 30:
            out["canonical_query"] = cq
        vs = []
        for v in data.get("variations") or []:
            t = normalize(v.get("text") if isinstance(v, dict) else str(v))
            if t and not has_leak(t):
                vs.append(t)
        out["variations"] = distinct(vs, query)[:10]
        out["hard_negatives"] = [normalize(str(n)) for n in (data.get("hard_negatives") or []) if n and not has_leak(str(n))][:6]
        out["source"] = "llm"
    if not out["canonical_query"]:
        out["canonical_query"] = canonical_fallback(query, cf)
    if len(out["variations"]) < 8:
        pool = out["variations"] + paraphrase_fallback(query, cf)
        v = distinct(pool, query)
        if len(v) < 8:  # relax only as far as needed; exact duplicates are never allowed
            v = distinct(pool, query, max_jaccard=0.97)
        out["variations"] = v[:10]
        out["source"] += "+fallback"
    if len(out["hard_negatives"]) < 2:
        out["hard_negatives"] += negatives_fallback(query)
    return out, usage


def distinct(items: List[str], query: str, max_jaccard: float = 0.85) -> List[str]:
    kept: List[str] = []
    seen = [set(raw_tokens(query))]
    for it in items:
        tk = set(raw_tokens(it))
        if not tk:
            continue
        if any(len(tk & s) / len(tk | s) > max_jaccard for s in seen):
            continue
        kept.append(it)
        seen.append(tk)
    return kept


def canonical_fallback(query: str, cf: Frame) -> str:
    topic, _ = topic_for(cf, cf.contexts)
    dev = {"foldable": "Foldable phone", "tablet": "Tablet", "phone": "Phone"}.get(cf.device, "Device")
    if topic == "Device Issue":  # no known facet: restate the complaint rather than invent a component
        return f"{dev} issue: {normalize(query).rstrip('.')}"
    part = {"inner": " inner display", "cover": " cover display"}.get(cf.part, "")
    syms = ", ".join(sorted(s.lower() for s in cf.symptoms))
    return f"{dev}{part} issue: {topic.lower()}" + (f" ({syms})" if syms else "")


_SWAPS = [("screen", "display"), ("phone", "device"), ("goes", "turns"), ("won't", "will not"), ("completely", "totally"),
          ("black", "dark"), ("blank", "empty"), ("flickers", "flashes"), ("laggy", "slow to respond"), ("cracked", "broken")]


def paraphrase_fallback(query: str, cf: Frame) -> List[str]:
    """Deterministic register transforms (used only when the LLM is unavailable)."""
    q = normalize(re.sub(r'^\s*\d+\.\s*"?|"', "", query))
    core = q.rstrip(".")
    swapped = core
    for a, b in _SWAPS:
        swapped = re.sub(rf"\b{re.escape(a)}\b", b, swapped, flags=re.I)
    kw = [t for t in raw_tokens(core) if len(t) > 3 and t not in {"when", "with", "that", "this", "have", "after", "even", "just"}][:6]
    rnd = random.Random(len(q))

    def typo(s: str) -> str:
        w = s.split(" ")
        idx = [i for i, x in enumerate(w) if len(x) > 4][:2]
        for i in idx:
            x = w[i]
            j = rnd.randrange(1, len(x) - 1)
            w[i] = x[:j] + x[j + 1] + x[j] + x[j + 2 :]
        return " ".join(w)

    clauses = [c.strip() for c in re.split(r"[;,]|\band\b|\bso\b|\bwhile\b", core) if len(c.strip().split()) >= 3]
    first = clauses[0] if clauses else core
    first_sw = swapped.split(",")[0].split(";")[0]
    fl = first[0].lower() + first[1:]
    return [
        f"I am experiencing the following problem: {fl}.",
        f"Could you please help? {swapped}.",
        f"ugh {fl.lower()}, pls help",
        " ".join(kw),
        f"This is so frustrating - {first_sw.lower()}!!",
        typo(first.lower()),
        f"{' '.join(kw[:4])} problem",
        f"why does this keep happening: {first_sw.lower()}?",
        typo(swapped.lower()),
        f"{fl} - how do I fix it?",
        f"Support request: {first_sw}.",
    ]


def negatives_fallback(query: str) -> List[str]:
    q = normalize(query).lower()
    flips = [("turn on", "turn off"), ("screen", "battery"), ("inner", "cover"), ("display", "speaker")]
    out = []
    for a, b in flips:
        if a in q:
            out.append(q.replace(a, b))
    return out[:4] or [q.replace("screen", "speaker")]


# ----------------------------------------------------------------------------- Stage B
def render_units(units, resolutions) -> str:
    lines = []
    for u in units:
        res = resolutions.get(u.uid)
        scr = f' screen="{res.screen_name}"' if res is not None and res.screen_name else ""
        lines.append(f"{u.uid} [category={u.category}{scr}]")
        for i, s in enumerate(u.steps):
            lines.append(f"   {i}) {s}")
    return "\n".join(lines)


def structure(llm: LLM, query: str, canonical: str, units, resolutions, timeout: float = 6.0,
              wait_budget: float = 0.0) -> Tuple[Optional[dict], Usage]:
    user = f"Complaint: {query}\nCanonical: {canonical}\n\nCANDIDATES:\n{render_units(units, resolutions)}"
    data, usage = llm.json_call(STRUCT_SYSTEM, user, max_tokens=1600, timeout=timeout, wait_budget=wait_budget)
    if not isinstance(data, dict):
        return None, usage
    uids = {u.uid for u in units}
    clean = {"no_viable_solution": bool(data.get("no_viable_solution")), "goals": [], "actions": {}}
    for uid, a in (data.get("actions") or {}).items():
        if uid not in uids or not isinstance(a, dict):
            continue
        name = title_case(normalize(str(a.get("name") or "")))
        desc = str(a.get("description") or "")
        drops = [int(i) for i in (a.get("drop_steps") or []) if str(i).lstrip("-").isdigit()]
        clean["actions"][uid] = {"keep": a.get("keep", True) is not False,
                                 "name": name if valid_action_name(name) and 2 <= word_count(name) <= 6 else None,
                                 "description": desc, "drop_steps": drops}
    seen = set()
    for g in data.get("goals") or []:
        if not isinstance(g, dict):
            continue
        us = [u for u in (g.get("units") or []) if u in uids and u not in seen]
        seen.update(us)
        topic = title_case(normalize(str(g.get("topic") or "")))
        title = sentence_case(normalize(str(g.get("title") or "")))
        clean["goals"].append({
            "topic": topic if 1 <= word_count(topic) <= 4 and re.match(r"^[A-Za-z][A-Za-z0-9 -]*$", topic) else None,
            "title": title if valid_title(title) else None,
            "config_request": bool(g.get("config_request")),
            "units": us,
        })
    return clean, usage


def fallback_variations(query: str, cf: Frame) -> List[str]:
    """8-10 distinct deterministic paraphrases (no LLM)."""
    pool = paraphrase_fallback(query, cf)
    v = distinct(pool, query)
    if len(v) < 8:
        v = distinct(pool, query, max_jaccard=0.97)
    return v[:10]
