"""Facet frames: a small, explainable symbolic view of a complaint or a knowledge section.

Used by (a) the Relevance Gate (does this SIIS document/section address THIS complaint?) and
(b) the semantic cache's contradiction guard (never serve a plan whose facets conflict).
The lexicons are domain vocabulary, not per-query rules: they cover the four domains named in the
Theme-2 PDF (Battery, Display, Camera, Performance) plus connectivity/audio for generalisation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Set

from .text import normalize


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


SYMPTOMS: Dict[str, re.Pattern] = {
    "BLANK": _rx(r"\b(blank|black|dark|white screen|no (?:image|display|picture|text)|nothing (?:shows|appears|is visible|loads|displays)|"
                 r"(?:does ?n[o']?t|won'?t|can'?t|cannot|not) (?:display|show anything|see)|hardly see|no visible content)"),
    "NO_POWER": _rx(r"(won'?t|doesn'?t|does not|will not|not|cannot|can'?t) (?:turn on|start|boot|power on)|won'?t start up|not turning on|dead phone|device not turning on"),
    "FLICKER": _rx(r"flicker|flash|blink|strob"),
    "DAMAGE": _rx(r"crack|broken|shatter|bleed|ink blot|dead pixel|physical damage|dropped|liquid|water damage"),
    "TOUCH": _rx(r"\btouch|unresponsive|(?:not|n't|never) respond|laggy|delay|input"),
    "DISTORT": _rx(r"distort|garbled|warped|glitch|discolou?r|lines on"),
    "SIZE": _rx(r"\bsmall\b|shrunk|does ?n'?t fill|not fill|full size|expand"),
    "OVERLAY": _rx(r"floating|hover|overlay|bubble|\bcircle\b"),
    "ROTATE": _rx(r"rotat|orientation|landscape|portrait"),
    "SLOW": _rx(r"\bslow|sluggish|freez|hang(?:s|ing)?\b|stutter|performance"),
    "DRAIN": _rx(r"drain|dies fast|battery (?:life|dies|runs out)|runs out"),
    "HEAT": _rx(r"overheat|too hot|getting hot|heating"),
    "CONNECT": _rx(r"not responding|can'?t connect|cannot connect|no internet|server|disconnect|\bsync|drop(?:s|ping)? (?:out|connection)|keeps dropping"),
    "AUDIO": _rx(r"no sound|can'?t hear|speaker|crackl|audio (?:issue|problem)"),
    "BLUR": _rx(r"blurr|out of focus|fuzzy"),
    "GHOST": _rx(r"too sensitive|by themselves|touch(?:es)? register(?:s)? by|taps? i never|ghost touch|phantom touch"),
    "NO_CHARGE": _rx(r"(?:won'?t|doesn'?t|does not|not|isn'?t) charg"),
    "PROTECTOR": _rx(r"screen protector|protective film"),
    "CRASH": _rx(r"crash"),
    "SELF_OFF": _rx(r"turn(?:s|ing)? off by itself|keeps turning off|shuts? (?:itself )?off|turn(?:s|ing)? on by itself"),
    "DIM": _rx(r"brightness|too dim|not bright enough"),
    "MISSING": _rx(r"(?:did not|didn'?t) (?:move|transfer|copy)|(?:photos|contacts|data|files|messages) (?:are|is|were) missing|"
                   r"missing (?:on|from|after)|lost (?:my )?(?:photos|contacts|data)"),
    "ZOOM": _rx(r"\bhuge\b|too big|too large|zoomed? in|enlarged"),
    "STAYS_ON": _rx(r"won'?t (?:turn|switch) off|stays on\b|never (?:goes to sleep|turns off)|won'?t (?:go to )?sleep"),
}

# Symptoms that describe the same observable failure family.
SYMPTOM_FAMILY = {
    "BLANK": "VIS", "NO_POWER": "VIS", "DAMAGE": "DMG", "FLICKER": "FLK", "TOUCH": "TCH", "DISTORT": "DST",
    "SIZE": "SIZE", "OVERLAY": "OVL", "ROTATE": "ROT", "SLOW": "SLOW", "DRAIN": "BAT", "HEAT": "HEAT",
    "CONNECT": "NET", "AUDIO": "AUD", "BLUR": "CAM", "GHOST": "GHOST", "NO_CHARGE": "CHG", "PROTECTOR": "PROT",
    "CRASH": "CRASH", "SELF_OFF": "SELF", "DIM": "DIM", "MISSING": "MISS", "ZOOM": "ZOOM", "STAYS_ON": "STAYON",
}
# "Sharp" symptoms decide the fix on their own: if a query has one the cached plan never dealt with,
# the plan is for a different problem however similar the wording (over-sensitive touch != laggy touch).
SHARP = frozenset({"GHOST", "NO_CHARGE", "PROTECTOR", "CRASH", "SELF_OFF", "DIM", "MISSING", "HEAT", "DRAIN",
                   "AUDIO", "BLUR", "ROTATE", "CONNECT", "ZOOM", "STAYS_ON"})

# Problem-domain contexts: if a knowledge doc is ABOUT one of these, the complaint must mention it.
CONTEXTS: Dict[str, re.Pattern] = {
    "email": _rx(r"\be-?mail|gmail|outlook"),
    "tv_mirroring": _rx(r"\btv\b|television|mirror|smart view|casting|\bcast\b|smartthings|projector"),
    "camera": _rx(r"camera(?! lens)|\bvideo\b|shutter|photo|selfie"),
    "rotation": _rx(r"rotat|orientation"),
    "multiwindow": _rx(r"multi ?window|split screen|pop-?up view|app pair|quick access panel|application edge|edge panel"),
    "fingerprint": _rx(r"fingerprint|biometric"),
    "kids": _rx(r"\bkids\b"),
    "account_lock": _rx(r"google account|forgot(?:ten)? (?:your )?(?:password|pin|pattern)|device manager|locked due|recovery menu"),
    "keyboard": _rx(r"third-party keyboard|keyboard issue"),
    "data_transfer": _rx(r"data transfer|transfer (?:my |your )?data|smart switch"),
    "stylus": _rx(r"stylus|s pen"),
    "wifi": _rx(r"wi-?fi|mobile data|internet connection"),
    "bluetooth": _rx(r"bluetooth"),
}

COMPONENTS: Dict[str, re.Pattern] = {
    "display": _rx(r"\bscreen|\bdisplay|touch|pixel"),
    "battery": _rx(r"\bbattery|\bcharg(?:e|er|ing)\b"),
    "audio": _rx(r"speaker|\bsound|audio|\bvolume|microphone|\bmic\b"),
    "camera": _rx(r"camera(?! lens)|\bphoto"),
    "network": _rx(r"wi-?fi|bluetooth|network|signal|\bsim\b|mobile data"),
    "hardware": _rx(r"back (?:glass|cover|panel)|\bled\b|notification light"),
}

DEVICE = [
    ("foldable", _rx(r"\bfold|flip|foldable|inner screen|cover screen|outer (?:cover )?screen")),
    ("tablet", _rx(r"\btablet|\btab\b")),
    ("phone", _rx(r"phone|smartphone|nexa|mobile")),
]
PART = [
    ("inner", _rx(r"inner (?:screen|display)|inside (?:screen|display)|main folding (?:screen|display)")),
    ("cover", _rx(r"cover (?:screen|display)|outer (?:cover )?(?:screen|display)|front (?:screen|display)")),
    ("half", _rx(r"\bhalf\b|one side|left side|right side")),
]
INTENT_ON = _rx(r"\b(?:turn on|switch on|enable|activate|bring back|show)\b")
INTENT_OFF = _rx(r"\b(?:remove|get rid of|turn off|switch off|disable|hide)\b")
POLARITY_ON = _rx(r"won'?t (?:turn|switch) on|can'?t (?:turn|switch) on|turn(?:s|ing)? on by itself|keeps turning on")
POLARITY_OFF = _rx(r"won'?t (?:turn|switch) off|can'?t (?:turn|switch) off|turn(?:s|ing)? off by itself|keeps turning off")
CONFIG_INTENT = _rx(r"\b(i want to|how (?:do|can) i|how to|i'?d like to|remove it|get rid of|turn off the|disable the|change the|set up)\b")


@dataclass(frozen=True)
class Frame:
    symptoms: FrozenSet[str] = frozenset()
    contexts: FrozenSet[str] = frozenset()
    device: str = ""
    part: str = ""
    pol: str = ""  # "on" | "off" | ""  (e.g. "won't turn ON" vs "won't turn OFF")
    config: bool = False
    components: FrozenSet[str] = frozenset()

    @property
    def families(self) -> Set[str]:
        return {SYMPTOM_FAMILY[s] for s in self.symptoms}

    def as_dict(self) -> dict:
        return {"symptoms": sorted(self.symptoms), "contexts": sorted(self.contexts), "device": self.device,
                "part": self.part, "polarity": self.pol, "config_request": self.config,
                "components": sorted(self.components)}


_NEGATED = _rx(r"\b(?:no|not|without|isn'?t|is not|never) (?:any )?(?:physical |visible |liquid )?(?:damage|damaged|cracks?|cracked|broken)\b")


def frame(text: str) -> Frame:
    t = _NEGATED.sub(" ", normalize(text))
    tc = _FINE.sub(" ", t)  # "...but the inner screen still works fine" does not name the affected part/component
    sy = frozenset(k for k, rx in SYMPTOMS.items() if rx.search(t))
    cx = frozenset(k for k, rx in CONTEXTS.items() if rx.search(t))
    dev = next((k for k, rx in DEVICE if rx.search(t)), "")
    part = next((k for k, rx in PART if rx.search(tc)), "")
    pol = "on" if POLARITY_ON.search(t) else ("off" if POLARITY_OFF.search(t) else "")
    if not pol and "NO_POWER" in sy:  # "won't start / won't boot" is the won't-turn-ON direction
        pol = "on"
    cfg = bool(CONFIG_INTENT.search(t)) and not (sy & {"BLANK", "NO_POWER", "DAMAGE", "FLICKER"})
    if cfg and not pol:  # configuration requests carry an intent direction: "remove the circle" vs "turn it on"
        pol = "off" if INTENT_OFF.search(t) else ("on" if INTENT_ON.search(t) else "")
    comp = frozenset(k for k, rx in COMPONENTS.items() if rx.search(tc))
    return Frame(sy, cx, dev, part, pol, cfg, comp)


_FINE = _rx(r"\b(?:the )?(?:(?:outer cover|outer|cover|front|inner|main|other)\s+)?(?:screen|display|phone display|side)(?: itself)?\s+"
            r"(?:is\s+|still\s+)?(?:works?|working|fine|ok|okay|normal(?:ly)?|perfectly)(?:\s+(?:fine|perfectly|normally|ok|okay))?\b")


def compatible(a: Frame, b: Frame) -> tuple[bool, str]:
    """Contradiction guard for the semantic cache. Returns (ok, reason)."""
    if a.pol and b.pol and a.pol != b.pol:
        return False, "polarity-conflict"
    if a.part and b.part and a.part != b.part:
        return False, "screen-part-conflict"
    if a.families and b.families and not (a.families & b.families):
        return False, "symptom-conflict"
    if (a.symptoms & SHARP) - b.symptoms:
        return False, "sharp-symptom-conflict"
    if (a.contexts ^ b.contexts) & {"email", "camera", "tv_mirroring", "data_transfer", "wifi", "bluetooth"}:
        return False, "context-conflict"
    if a.components and b.components and (a.components - b.components):
        return False, "component-conflict"  # query names a component the cached plan never dealt with
    return True, ""
