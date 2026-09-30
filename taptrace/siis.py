"""SIIS Normalizer + grounded step-candidate extraction + Complaint-Knowledge Relevance Gate.

Design principle ("evidence-locked extraction"): every step the engine can ever emit is derived
from exactly one SIIS sentence by *deterministic, meaning-preserving* rewrites (strip filler such
as "Please"/"You can", split "go to Settings, tap X, and then tap Y" into atomic taps). Each step
keeps its source span id (S<section>.<sentence>) so provenance is auditable. The LLM stage may only
SELECT / GROUP / NAME these candidates - it can never author step text (see llm_stage.py).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .facets import CONTEXTS, Frame, frame
from .text import cap_first, ensure_period, normalize, tokens

# ----------------------------------------------------------------------------- lexicons
VERBS = set("""tap touch select choose navigate go open swipe press hold turn enable disable remove connect disconnect
plug unplug insert eject shine check inspect examine ensure make try restart reboot reset charge clean wipe use update
install uninstall back visit contact schedule send enter search set adjust change switch drag place keep avoid increase
decrease reinsert review verify confirm look test perform power force clear delete allow add find follow call wait
locate repeat return exit close rotate replace take bring start stop get access mimic""".split())


def _gerunds() -> Dict[str, str]:
    """gerund -> imperative for every step verb (charging->charge, tapping->tap, pressing->press)."""
    g = {}
    for v in VERBS:
        g[v + "ing"] = v
        if v.endswith("e") and not v.endswith("ee"):
            g[v[:-1] + "ing"] = v
        if re.fullmatch(r"[^aeiou]*[aeiou][bdgmnprt]", v):
            g[v + v[-1] + "ing"] = v
    return g


GERUND = _gerunds()
_TRY_GERUND = re.compile(r"^try (\w+ing)\b", re.I)
# "..., and then charging the device" / "... and then charge it" -> a separate step (one interaction per step)
_THEN_ACTION = re.compile(r",?\s+and then\s+(\w+)\b", re.I)
# "perform a factory data reset: go to Settings, tap ..." -> the Settings path becomes its own chain
_COLON_PATH = re.compile(r":\s+(?=(?:go to|navigate to|open)\s+(?:the\s+)?settings\b)", re.I)
# "back up your data and perform a factory data reset" -> backup and reset are two interactions
_BACKUP_THEN_CRIT = re.compile(r"(?<=\bdata)\s+and\s+(?=(?:perform|do|run)\s+(?:a\s+)?factory|reset\b)", re.I)
# Contexts whose sentences are dropped when the complaint does not mention them. Remedy-tool words
# (TV/monitor, Data Transfer, Wi-Fi) are deliberately NOT here: "back up your data to a TV" is a remedy.
EXCLUSIVE_CTX = frozenset({"fingerprint", "kids", "account_lock", "keyboard", "stylus", "email", "camera", "multiwindow", "rotation"})
_VAGUE_COND = re.compile(r"this is the case|if necessary|if needed|if so\b|if prompted|you wish to see|if you want", re.I)
_TAIL_IMPERATIVE = re.compile(r",\s+((?:please\s+)?(?:visit|contact|schedule|check|try|press|connect|remove|back up|use|go to|navigate|restart)\b.*)$", re.I)

GENERIC_SELF = re.compile(r"restart|reboot|force|safe mode|software|update|factory|reset|physical damage|liquid|cache|storage", re.I)
GENERIC_ESC = re.compile(r"service|support|repair|care plan|customer", re.I)
ESCALATION_STEP = re.compile(r"\b(service cent(?:er|re)|customer support|support cent(?:er|re)|repair|walk-in|mail-in|authorized)\b", re.I)
CRITICAL_RX = re.compile(r"\b(factory (?:data )?reset|reset (?:your|the) (?:phone|device|tablet)|restart\w*|reboot\w*|safe mode|"
                         r"software updates?|update (?:your |the )?(?:device )?software|firmware|remove the battery|delete all|"
                         r"reset (?:all |network |mobile network )?settings)\b", re.I)  # resets erase saved data -> disruptive
HEADING_CRIT = re.compile(r"\b(restart\w*|reboot\w*|force|safe mode|factory|reset)\b", re.I)
# Article preamble that announces steps instead of being one ("Let's go through some steps", "try the following steps").
META_STEP = re.compile(r"\b(troubleshooting steps|some steps to help|we can resolve|together to see|help resolve this|"
                       r"(?:try|go through|follow|perform) (?:the following|these|some|a few)(?: \w+)? (?:steps|solutions|tips)|"
                       r"the following (?:steps|solutions|tips))\b", re.I)
SETTINGS_OPEN = re.compile(r"^(?:go to|navigate to(?: and open)?|open|launch|from)\s+(?:the\s+)?settings(?: app)?\b", re.I)
LEAK_SENTENCE = re.compile(r"https?://|www\.|\[[^\]]+\]\([^)]+\)|\b[\w.+-]+@[\w-]+\.[\w.]+\b|\b[\w-]+\.(?:com|net|org|io|co|in)\b|"
                           r"website|provided links?|learn more|check out our|for more information|visit our|guide\b", re.I)

_LEADERS = [
    re.compile(r"^(?:note|tip|important)\s*:\s*", re.I),
    re.compile(r"^(?:first|next|then|now|finally|also|alternatively|additionally|afterwards?|simply|please|lastly|otherwise),?\s+", re.I),
    re.compile(r"^(?:carefully|gently|quickly|simply|immediately)\s+", re.I),
    re.compile(r"^let'?s\s+(?:try to\s+)?", re.I),
    re.compile(r"^it(?:'s| is) (?:crucial|important|essential|recommended|a good idea) to\s+", re.I),
    re.compile(r"^(?:you may need to|you might need to|you'll need to|you will need to|you need to|you can|you may|you should|"
               r"we recommend that you|it's recommended to|it is recommended to|be sure to|remember to)\s+(?:also\s+)?(?:still\s+)?", re.I),
    re.compile(r"^(?:to do this|to prevent this|to fix this|to resolve this issue|to resolve this|in such cases|in this case|"
               r"with some devices|from here|if so),\s+", re.I),
    re.compile(r"^(?:after [a-z]+ing|once [^,]{3,60}|when [^,]{3,60}|before proceeding|now that [^,]{3,60}),\s+", re.I),
    re.compile(r"^[A-Z][A-Za-z -]{2,30}:\s+(?=[A-Z])"),  # "Wireless connection: If you ..." label prefix
]
_LOCCOND = re.compile(r"^(on the (?:new|old) (?:device|phone)|on your (?:old|new) (?:device|phone)),\s+(.*)$", re.I)
_PURPOSE = re.compile(r"^to ([^,]{3,90}),\s+(.*)$", re.I)
_COND = re.compile(r"^(?:even )?(if [^,]{3,160}),\s+(.*)$", re.I)
_DEVCOND = re.compile(r"^((?:on|for) (?:devices|phones|models)[^:]{0,60}):\s*(.*)$", re.I)
_USING = re.compile(r"^using ([^,]{2,40}),\s+(.*)$", re.I)
_SPLIT_VERBS = r"(?:tap|select|touch|choose|swipe|search|enter|turn|press|drag|open|connect|insert|remove|plug|power|select|scan|visit|disconnect|reinsert)\b"
_SPLIT = re.compile(r",\s*(?:and\s+)?(?:then\s+)?(?=" + _SPLIT_VERBS + r")|"
                    r"\s+and then\s+(?=" + _SPLIT_VERBS + r"|navigate\b)|;\s+", re.I)
_TARGET = re.compile(r"^(?:tap the switch(?:es)? next to|search for and select|turn (?:on|off)|tap(?: on)?|select|touch|choose|open)\s+(?:the\s+)?(.+?)"
                     r"(?:\s+(?:again|to confirm|to disable it|to enable it|to turn (?:it )?(?:on|off)[^.]*|when it appears.*|option))?\.?$", re.I)
POL_OFF = re.compile(r"\b(turn(?:ing)? (?:it |this feature |this )?off|disabl\w*|switch(?:ing)? off|deactivat\w*|to turn off)\b", re.I)
POL_ON = re.compile(r"\b(turn(?:ing)? (?:it |this )?on|enabl\w*|switch(?:ing)? on|activat\w*)\b", re.I)
POL_SET = re.compile(r"\b(adjust|increase|decrease|stays? on for longer|set (?:it|the \w+) to|change (?:it|the \w+) to)\b", re.I)
_GENERIC_NOUNS = {"device", "phone", "tablet", "screen", "usb", "your", "button", "power", "side", "setting"}
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z])|(?<=[a-z]{2}\.)(?=[A-Z][a-z])")


@dataclass
class Sentence:
    sid: str
    text: str
    corrupt: bool = False
    leak: bool = False
    label: bool = False
    contexts: frozenset = frozenset()


@dataclass
class Section:
    idx: int
    heading: str
    sentences: List[Sentence] = field(default_factory=list)
    contexts: frozenset = frozenset()

    @property
    def clean_heading(self) -> str:
        h = re.sub(r"^(?:step\s*\d+\s*[:.-]\s*|\d+\.\s*)", "", self.heading, flags=re.I).strip(" #:")
        return h

    @property
    def generic(self) -> str:
        h = self.clean_heading
        if not h:
            return ""
        if GENERIC_SELF.search(h):
            return "self"
        if GENERIC_ESC.search(h) and not re.search(r"provider", h, re.I):
            return "escalation"
        return ""


@dataclass
class Doc:
    title: str
    sections: List[Section]
    fingerprint: str
    title_frame: Frame
    raw_chars: int

    def span_text(self, sid: str) -> str:
        for s in self.sections:
            for x in s.sentences:
                if x.sid == sid:
                    return x.text
        return ""

    def numbered(self, allowed: Optional[Set[int]] = None) -> str:
        out = []
        for s in self.sections:
            if allowed is not None and s.idx not in allowed:
                continue
            if s.heading:
                out.append(f"## [section {s.idx}] {s.clean_heading}")
            for x in s.sentences:
                if not (x.corrupt or x.leak):
                    out.append(f"{x.sid}: {x.text}")
        return "\n".join(out)


@dataclass
class Unit:
    """A grounded candidate action: consecutive steps that belong together."""
    uid: str
    section: int
    kind: str  # "nav" (Settings chain) | "other"
    steps: List[str]
    spans: List[str]
    targets: List[str] = field(default_factory=list)
    polarity: str = "open"
    condition: str = ""
    category: str = "manual"
    heading: str = ""

    @property
    def text(self) -> str:
        return " ".join(self.steps)


# ----------------------------------------------------------------------------- parsing
def _strip_prefix(content: str, title: str) -> str:
    head = content[:600]
    i = head.find("):")
    if i != -1 and (not title or title[:20].lower() in head[: i + 2].lower()):
        return content[i + 2 :].strip()
    return content


def _is_corrupt(text: str) -> bool:
    words = text.split()
    if any(len(re.sub(r"[^A-Za-z]", "", w)) >= 22 for w in words):
        return True
    return len(text) > 60 and len(text) / max(1, len(words)) > 14


def parse_siis(siis) -> Doc:
    if isinstance(siis, dict):
        title = normalize(str(siis.get("title") or ""))
        content = str(siis.get("content") or "")
    else:
        content = str(siis or "")
        first = content.strip().split("\n", 1)[0]
        title = normalize(re.sub(r"^#+\s*", "", first))[:120]
    content = _strip_prefix(content.replace("\r", ""), title)
    fp = hashlib.sha256((title + "\n" + content).encode("utf-8")).hexdigest()[:16]
    sections: List[Section] = [Section(0, "")]
    for line in content.split("\n"):
        line = normalize(line)
        if not line or line in ('""', "''"):
            continue
        m = re.match(r"^#{1,6}\s*(.*)$", line)
        if m:
            h = m.group(1).strip()
            if h:
                sections.append(Section(len(sections), h, contexts=_contexts(h)))
            continue
        sec = sections[-1]
        for piece in _SENT_SPLIT.split(line):
            piece = piece.strip()
            if not piece:
                continue
            s = Sentence(sid=f"S{sec.idx}.{len(sec.sentences) + 1}", text=piece)
            s.corrupt = _is_corrupt(piece)
            s.leak = bool(LEAK_SENTENCE.search(piece))
            s.label = piece.endswith(":")
            s.contexts = _contexts(piece)
            sec.sentences.append(s)
    sections = [s for s in sections if s.sentences or s.heading]
    return Doc(title=title, sections=sections, fingerprint=fp, title_frame=frame(title), raw_chars=len(content))


def _contexts(text: str) -> frozenset:
    return frozenset(k for k, rx in CONTEXTS.items() if rx.search(text))


# ----------------------------------------------------------------------------- sentence -> steps
def _core(text: str) -> tuple[str, str]:
    """Strip filler/leading clauses; return (imperative_core, condition)."""
    t = normalize(text).strip(" \"'")
    cond = ""
    for _ in range(4):
        before = t
        m = _DEVCOND.match(t) or _LOCCOND.match(t)
        if m:
            cond, t = cap_first(m.group(1)), m.group(2)
        m = _COND.match(t)
        if m:
            cond, t = cap_first(m.group(1).replace("Even if", "If")), m.group(2)
        m = _USING.match(t)
        if m:
            t = f"{m.group(2).rstrip('.')} using {m.group(1)}."
        m = _PURPOSE.match(t)
        if m and re.sub(r"^(?:carefully|gently|quickly|simply|please)\s+", "", m.group(2), flags=re.I).split(" ")[0].lower() \
                in VERBS | {"you", "please", "go", "navigate"}:
            t = m.group(2)
        for rx in _LEADERS:
            t = rx.sub("", t)
        if t == before:
            break
    m = _TRY_GERUND.match(t)
    if m and m.group(1).lower() in GERUND:  # "Try forcing a restart by ..." -> "Force a restart by ..."
        t = cap_first(GERUND[m.group(1).lower()]) + t[m.end():]
    if _first_word(t) not in VERBS:
        m = _TAIL_IMPERATIVE.search(t)
        if m:
            t = re.sub(r"^please\s+", "", m.group(1), flags=re.I)
    t = _THEN_ACTION.sub(_then_step, t)
    t = _COLON_PATH.sub("; ", t)
    t = _BACKUP_THEN_CRIT.sub("; ", t)
    if cond and _VAGUE_COND.search(cond):
        cond = ""
    return t.strip(), cond


def _then_step(m: re.Match) -> str:
    w = m.group(1).lower()
    verb = GERUND.get(w) or (w if w in VERBS else None)
    return f"; {verb} " if verb else m.group(0)


def _first_word(t: str) -> str:
    m = re.match(r"^[\"']?([A-Za-z]+)", t)
    return m.group(1).lower() if m else ""


def _norm_step(s: str) -> str:
    s = normalize(s).strip(" ,;")
    if SETTINGS_OPEN.match(s) and len(s.split()) <= 5:
        return "Navigate to and open Settings."
    s = re.sub(r"\byou can\s+", "", s, flags=re.I)
    return ensure_period(cap_first(s))


def sentence_steps(text: str) -> tuple[List[str], str]:
    core, cond = _core(text)
    fw = _first_word(core)
    if fw not in VERBS:
        return [], cond
    parts = [p for p in _SPLIT.split(core) if p and p.strip()]
    steps = [_norm_step(p) for p in parts]
    return [s for s in steps if len(s.split()) >= 2], cond


def _targets(steps: List[str]) -> List[str]:
    out = []
    for s in steps:
        m = _TARGET.match(s.rstrip("."))
        if m:
            tgt = re.sub(r"[\"']", "", m.group(1)).strip()
            tgt = re.sub(r"\s+(?:and|then)\s+.*$|\s+to (?:turn|disable|enable|confirm|open|access|check|see)\b.*$", "", tgt)
            if tgt and tgt.lower() not in ("ok", "it", "settings"):
                out.append(tgt)
    return out


def _polarity(text: str, context: str = "") -> str:
    for t in (text, context):
        off, on, st = POL_OFF.search(t), POL_ON.search(t), POL_SET.search(t)
        if off and not on:
            return "off"
        if on and not off:
            return "on"
        if off and on:
            return "off" if off.start() > on.start() else "on"
        if st:
            return "set"
    return "open"


def classify(text: str, kind: str) -> str:
    if CRITICAL_RX.search(text):
        return "critical"
    if kind == "nav":
        return "auto"
    return "manual"


# ----------------------------------------------------------------------------- relevance gate
@dataclass
class GateResult:
    fit: str  # full | partial | none
    fit_score: float
    kept_sections: List[int]
    dropped: Dict[int, str]
    reason: str


def gate(doc: Doc, cf: Frame, doc_sim: float) -> GateResult:
    """Complaint-Knowledge fit.

    full    : doc context present in complaint AND doc symptom family overlaps (or doc is symptom-agnostic)
    partial : otherwise -> keep only generic remedy sections (restart/update/reset/damage/cache/support)
    none    : nothing viable remains, or only escalation remains for a partial fit (PDF 4.2.3 -> no_match)
    """
    tf = doc.title_frame
    ctx_ok = tf.contexts <= cf.contexts
    sym_ok = (not tf.families) or bool(tf.families & cf.families)
    agnostic = not tf.families and not tf.contexts
    full = ctx_ok and sym_ok and not (agnostic and doc_sim < 0.25)
    kept, dropped = [], {}
    for s in doc.sections:
        foreign = (s.contexts & EXCLUSIVE_CTX) - cf.contexts - (tf.contexts if ctx_ok else frozenset())
        if foreign:
            dropped[s.idx] = f"section is about {sorted(foreign)}, absent from complaint"
            continue
        if not full and not s.generic:
            dropped[s.idx] = "partial fit: non-generic section"
            continue
        if re.match(r"^(glossary|understanding|what is)", s.clean_heading, re.I):
            dropped[s.idx] = "explanatory section"
            continue
        kept.append(s.idx)
    reason = (f"context_ok={ctx_ok} (doc {sorted(tf.contexts)} vs complaint {sorted(cf.contexts)}); "
              f"symptom_ok={sym_ok} (doc {sorted(tf.families)} vs complaint {sorted(cf.families)}); doc_sim={doc_sim:.2f}")
    if not kept:
        return GateResult("none", 0.0, [], dropped, reason + "; no viable section")
    score = (0.75 + 0.25 * min(1.0, max(0.0, doc_sim) / 0.6)) if full else (0.45 + 0.15 * min(1.0, max(0.0, doc_sim) / 0.6))
    return GateResult("full" if full else "partial", round(score, 3), kept, dropped, reason)


# ----------------------------------------------------------------------------- unit building
_CONTINUE_NAV = {"tap", "select", "touch", "choose", "swipe", "enter", "review", "search"}
_PRONOUN_STEP = re.compile(r"^\w+(?: \w+)? (?:it|them|this|that)\b", re.I)


def build_units(doc: Doc, gr: GateResult, cf: Frame) -> List[Unit]:
    """Turn kept sections into grounded candidate units.

    Rules (each motivated by a real SIIS pattern, see docs/DATA_AUDIT.md):
      * A sentence whose steps open Settings starts a *nav* unit (a Settings screen chain).
      * Following tap/select/swipe/enter sentences in the same section continue that chain
        ("Swipe to and tap Reset", "Tap Delete all").
      * An imperative sentence that immediately precedes a chain states its intent
        ("you can disable the full screen gesture function. Go to Settings ...") -> used for
        polarity/condition, not emitted as a separate step.
      * Other imperative sentences group per (section, category). Headed sections classify with
        their heading ("Force a Restart" makes "Press and hold ..." critical).
      * Escalation sentences (service centre / customer support) always form their own unit so
        the sequencer can place them after self-help.
      * Heading-less intro sections split on topic change.
      * Pronoun-led steps whose antecedent was not kept ("Enter it the same way") are dropped.
    """
    tfc = doc.title_frame.contexts if doc.title_frame.contexts <= cf.contexts else frozenset()
    allowed = cf.contexts | tfc
    units: List[Unit] = []
    for s in doc.sections:
        if s.idx not in gr.kept_sections:
            continue
        rows = []
        for x in s.sentences:
            if x.corrupt or x.leak or x.label or ((x.contexts & EXCLUSIVE_CTX) - allowed):
                rows.append((x, [], ""))
                continue
            st, cond = sentence_steps(x.text)
            rows.append((x, st, cond))
        cur_nav: Optional[Unit] = None
        groups: Dict[str, Unit] = {}
        last_other: Optional[Unit] = None
        prev_kept = False
        prev_text = ""
        for i, (x, steps, cond) in enumerate(rows):
            if not steps:
                prev_text, prev_kept = x.text, False
                if not x.corrupt and not x.leak and not x.label and cur_nav is None:
                    pass
                continue
            nxt = next((r for r in rows[i + 1 :] if r[1]), None)
            is_nav = "Navigate to and open Settings." in steps
            if not is_nav and nxt is not None and "Navigate to and open Settings." in nxt[1] and rows.index(nxt) == i + 1 \
                    and not ESCALATION_STEP.search(x.text):
                prev_text, prev_kept = x.text, True  # intent sentence for the next chain
                continue
            if META_STEP.search(" ".join(steps)) or (
                    not cond and not prev_kept and _PRONOUN_STEP.match(steps[0]) and not is_nav and cur_nav is None):
                prev_text, prev_kept = x.text, False
                continue
            if is_nav:
                cur_nav = Unit(uid="", section=s.idx, kind="nav", steps=list(steps), spans=[x.sid], targets=_targets(steps),
                               polarity=_polarity(x.text, prev_text), condition=cond or _cond_from(prev_text),
                               heading=s.clean_heading)
                units.append(cur_nav)
                last_other = None
            elif cur_nav is not None and _first_word(steps[0]) in _CONTINUE_NAV and not ESCALATION_STEP.search(x.text):
                cur_nav.steps += steps
                cur_nav.spans.append(x.sid)
                cur_nav.targets += _targets(steps)
                p = _polarity(x.text)
                if p != "open" and cur_nav.polarity == "open":
                    cur_nav.polarity = p
            elif cur_nav is not None and _refers_to_screen(steps, cur_nav) and not ESCALATION_STEP.search(x.text) \
                    and classify(" ".join(steps), "other") != "critical":
                # "Wait a few seconds and turn it on again" / "Make sure camera access is turned on": same screen,
                # so it stays in the chain (One Action = One Screen) instead of becoming a second action.
                cur_nav.steps += steps
                cur_nav.spans.append(x.sid)
                p = _polarity(x.text)
                if p in ("on", "off") and cur_nav.polarity in ("on", "off") and p != cur_nav.polarity:
                    cur_nav.polarity = "open"  # off-then-on = revisit the screen; a fixed on/off probe would contradict
                elif p != "open" and cur_nav.polarity == "open":
                    cur_nav.polarity = p
            else:
                cur_nav = None
                esc = bool(ESCALATION_STEP.search(x.text))
                cat = "manual" if esc else classify(" ".join(steps), "other")
                if not esc and cat == "manual" and HEADING_CRIT.search(s.clean_heading):
                    cat = "critical"
                key = "escalation" if esc else cat
                if not esc and re.match(r"^back up\b", steps[0], re.I) and not CRITICAL_RX.search(" ".join(steps)):
                    cat, key = "manual", "backup"  # grounded pre-reset backup; resolver may upgrade to auto
                    esc = False
                topic_break = (not s.heading and not esc and last_other is not None and last_other.category == cat
                               and not _shares_topic(last_other.text, " ".join(steps)))
                if topic_break:
                    key = f"{cat}#{len(units)}"
                    groups[cat] = None  # type: ignore[assignment]
                u = groups.get(key)
                if u is None:
                    u = Unit(uid="", section=s.idx, kind="other", steps=[], spans=[], category=cat, heading=s.clean_heading)
                    u.__dict__["escalation"] = esc
                    u.__dict__["backup"] = key == "backup"
                    groups[key] = u
                    if topic_break:
                        groups[cat] = u
                    units.append(u)
                if cond and not steps[0].lower().startswith(("if", "on ", "for ")):
                    steps = [f"{cond}, {steps[0][0].lower()}{steps[0][1:]}"] + steps[1:]
                u.steps += steps
                u.spans.append(x.sid)
                if not esc:
                    last_other = u
            prev_text, prev_kept = x.text, True
    for u in units:
        if u.kind == "nav":
            u.category = classify(u.text + " " + u.heading, "nav")
        u.__dict__.setdefault("escalation", False)
    units = _merge_same_screen(units)
    esc_units = [u for u in units if u.__dict__.get("escalation")]
    if len(esc_units) > 1:
        head = esc_units[0]
        for u in esc_units[1:]:
            head.steps += u.steps
            head.spans += u.spans
        units = [u for u in units if u is head or u not in esc_units]
    for i, u in enumerate(units, 1):
        u.uid = f"U{i}"
        u.steps = _dedupe(u.steps)
    return [u for u in units if u.steps]


_BACK_REF = re.compile(r"\b(?:it|this (?:setting|option|feature|switch))\b(?! (?:is|was|has|may|might|can|could|will)\b)", re.I)


def _refers_to_screen(steps: List[str], nav: Unit) -> bool:
    """A follow-up sentence right after a Settings chain that acts on the same screen: it refers back with a
    pronoun ("turn it on again") or names the chain's final target ("make sure camera access is turned on")."""
    text = " ".join(steps)
    last = nav.targets[-1].lower() if nav.targets else ""
    return bool(_BACK_REF.search(text)) or (len(last) > 3 and last in text.lower())


def _cond_from(prev: str) -> str:
    m = re.match(r"^(if [^,]{3,160}),", normalize(prev), re.I)
    return cap_first(m.group(1)) if m else ""


def _shares_topic(a: str, b: str) -> bool:
    ta = {t for t in tokens(a) if t not in _GENERIC_NOUNS and len(t) > 3}
    tb = {t for t in tokens(b) if t not in _GENERIC_NOUNS and len(t) > 3}
    return bool(ta & tb)


def _merge_same_screen(units: List[Unit]) -> List[Unit]:
    """One Action = One Screen: Settings chains that share the navigation path collapse into one unit."""
    out: List[Unit] = []
    for u in units:
        if u.kind == "nav":
            twin = next((o for o in out if o.kind == "nav" and _path(o) == _path(u) and o.category == u.category), None)
            if twin is not None:
                if twin.polarity != u.polarity and (twin.condition or u.condition):
                    twin.__dict__.setdefault("groups", [dict(steps=list(twin.steps), spans=list(twin.spans), polarity=twin.polarity, condition=twin.condition)])
                    twin.__dict__["groups"].append(dict(steps=list(u.steps), spans=list(u.spans), polarity=u.polarity, condition=u.condition))
                else:
                    twin.steps += [s for s in u.steps if s not in twin.steps]
                    twin.spans += u.spans
                    twin.targets += [t for t in u.targets if t not in twin.targets]
                continue
        out.append(u)
    return out


def _path(u: Unit) -> tuple:
    """Screen path = navigation taps up to (not including) the final in-screen interaction."""
    tg = [t.lower() for t in u.targets]
    if len(tg) >= 2 and re.match(r"^(clear|delete|reset|ok|restart|buttons|swipe)", tg[-1]):
        tg = tg[:-1]
    return tuple(tg[:3])


def _dedupe(steps: List[str]) -> List[str]:
    seen, out = set(), []
    for s in steps:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out
