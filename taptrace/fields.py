"""Field compiler: deterministic generators + validators for every PDF 4.1 rule, and the zero-leak scrubber.

PDF 7.5: "Asking an LLM to respect word count constraints in natural language is unreliable. Enforce
programmatic validation, trimming, and correction loops in the application layer." Everything here is
code; LLM-proposed text is accepted only if it passes these validators, otherwise we fall back to the
deterministic generator (which is guaranteed valid by construction and unit-tested).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .facets import Frame
from .text import cap_first, is_sentence_case, is_title_case, normalize, sentence_case, title_case, word_count

# ----------------------------------------------------------------------------- zero leak
LEAK_RX = re.compile(
    r"https?://\S*|\bwww\.\S*|\[[^\]]*\]\([^)]*\)|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b|"
    r"\b[a-z0-9-]+\.(?:com|net|org|io|co|in|info|biz|us|uk|app|dev)\b(?:/\S*)?",
    re.I,
)


def has_leak(text: str) -> bool:
    return bool(LEAK_RX.search(text or ""))


def scrub(text: str) -> str:
    t = LEAK_RX.sub("", text or "")
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"\s+([.,;:])", r"\1", t)
    return t


# ----------------------------------------------------------------------------- goal / title
GOAL_RX = re.compile(r"^Follow these steps to perform this [A-Z][A-Za-z0-9-]*(?: [A-Za-z0-9-]+)* (?:Troubleshooting|Configuration)$")

TOPICS: List[Tuple[str, str, str]] = [  # symptom -> (Topic for goal, title)
    ("DAMAGE", "Screen Damage", "Screen damage repair"),
    ("FLICKER", "Screen Flicker", "Screen flicker fix"),
    ("TOUCH", "Touchscreen Response", "Touchscreen response fix"),
    ("BLANK", "Black Screen", "Black screen recovery"),
    ("NO_POWER", "Device Power", "Device power recovery"),
    ("DISTORT", "Display Distortion", "Display distortion check"),
    ("SIZE", "Screen Size", "Screen size settings"),
    ("OVERLAY", "Floating Menu", "Floating menu removal"),
    ("ROTATE", "Screen Rotation", "Screen rotation fix"),
    ("DRAIN", "Battery Drain", "Battery drain fix"),
    ("HEAT", "Device Overheating", "Overheating fix"),
    ("SLOW", "Slow Performance", "Performance slowdown fix"),
    ("CONNECT", "Connectivity", "Connectivity fix"),
    ("AUDIO", "Audio", "Audio issue fix"),
    ("BLUR", "Camera Quality", "Camera quality fix"),
]
CONTEXT_TOPICS = {"email": ("Email App Screen", "Email app screen"),
                  "data_transfer": ("Data Transfer Screen", "Data transfer screen"),
                  "camera": ("Camera Display", "Camera display fix"),
                  "wifi": ("Wi-Fi Connection", "Wi-Fi connection fix")}


def topic_for(cf: Frame, doc_contexts) -> Tuple[str, str]:
    for c in ("email", "data_transfer", "camera", "wifi"):
        if c in cf.contexts and c in doc_contexts:
            return CONTEXT_TOPICS[c]
    for sym, topic, title in TOPICS:
        if sym in cf.symptoms:
            return topic, title
    return "Device Issue", "Device issue fix"


def goal_text(topic: str, config: bool) -> str:
    return f"Follow these steps to perform this {title_case(topic)} {'Configuration' if config else 'Troubleshooting'}"


def valid_goal(g: str) -> bool:
    return bool(GOAL_RX.match(g or ""))


def valid_title(t: str) -> bool:
    return 2 <= word_count(t) <= 3 and is_sentence_case(t) and not has_leak(t)


# ----------------------------------------------------------------------------- action name
_GERUND = {"restarting": "Restart", "checking": "Check", "charging": "Charge", "updating": "Update",
           "cleaning": "Clean", "resetting": "Reset", "using": "Use", "adjusting": "Adjust"}
_NAME_VERBS = set("""check charge attempt force restart perform verify review clear contact visit schedule adjust configure
turn open back access connect remove clean try update use set enable disable inspect test locate transfer select""".split())

NAME_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^back up|^backup", re.I), "Back Up Phone Data"),
    (re.compile(r"factory (data )?reset", re.I), "Perform Factory Data Reset"),
    (re.compile(r"safe mode", re.I), "Restart in Safe Mode"),
    (re.compile(r"forc\w* (a )?restart", re.I), "Force Restart the Device"),
    (re.compile(r"restart|reboot", re.I), "Restart Your Device"),
    (re.compile(r"back up|backup", re.I), "Back Up Phone Data"),
    (re.compile(r"check the device, charger|cable for damage", re.I), "Check Charger and Cable"),
    (re.compile(r"walk-in|mail-in|repair|service cent|customer support", re.I), "Schedule Screen Repair Service"),
    (re.compile(r"different, undamaged charger|different charger", re.I), "Try a Different Charger"),
    (re.compile(r"screen protector|\bwipe\b|\bclean", re.I), "Clean the Screen Surface"),
    (re.compile(r"hdmi|monitor", re.I), "Mirror Screen to a Monitor"),
    (re.compile(r"mouse|keyboard", re.I), "Access Data With a Mouse"),
    (re.compile(r"data transfer|receive on this phone|wireless transfer", re.I), "Run the Data Transfer App"),
    (re.compile(r"updates? for any third-party apps|app updates", re.I), "Update Third-Party Apps"),
    (re.compile(r"charger and let it charge|charge for at least", re.I), "Charge the Device"),
    (re.compile(r"liquid damage|physical damage|corrosion|cracks", re.I), "Check for Physical Damage"),
]


def _clean_heading(h: str) -> str:
    h = re.sub(r"\b(?:the|your)\s+", "", h, flags=re.I)
    h = re.sub(r"'s\b", "", h)
    h = re.sub(r"[^A-Za-z0-9 &/-]", "", h)
    return normalize(h)


def heading_name(heading: str) -> Optional[str]:
    h = _clean_heading(heading)
    if not h:
        return None
    words = h.split(" ")
    first = words[0].lower()
    if first in _GERUND:
        words[0] = _GERUND[first]
        first = words[0].lower()
    if first in _NAME_VERBS and 2 <= len(words) <= 6:
        return title_case(" ".join(words))
    return None


def action_name(kind: str, category: str, heading: str, text: str, label: str = "", variant: str = "",
                grouped: bool = False, is_dummy: bool = False, escalation: bool = False) -> str:
    if kind == "nav" and category == "auto" and label and not is_dummy:
        if grouped:
            return title_case(f"Adjust {label}")
        return title_case({"on": f"Turn On {label}", "off": f"Turn Off {label}", "set": f"Adjust {label}"}.get(
            variant, f"Configure {label}"))
    if escalation:
        return "Schedule Screen Repair Service" if re.search(r"screen|display", text, re.I) else "Visit an Authorized Service Center"
    hn = heading_name(heading)
    if kind == "nav" and is_dummy:  # screen not in the catalog: name it after the section or the screen itself
        return hn or (title_case(f"Open {label}") if label else "Open Device Settings")
    if hn and not (category == "critical" and not re.search(r"restart|reset|safe|force|reboot|update", hn, re.I)):
        return hn
    for rx, name in NAME_RULES:
        if rx.search(text) or rx.search(heading):
            return name
    core = re.sub(r"^(?:if|when|on|for) [^,]{2,120},\s*", "", normalize(text), flags=re.I)
    core = re.sub(r"\b(?:the|your|a|an|any|my)\b\s*", "", core, flags=re.I)
    words = [w for w in re.sub(r"[^A-Za-z0-9 -]", " ", core).split(" ") if w][:4]
    return title_case(" ".join(words)) if words else "Follow Guided Steps"


def valid_action_name(n: str) -> bool:
    return bool(n) and is_title_case(n) and 1 <= word_count(n) <= 7 and not has_leak(n)


# ----------------------------------------------------------------------------- description
DESC_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^back up|^backup", re.I), "It will keep your personal data safe"),
    (re.compile(r"factory (data )?reset", re.I), "It will restore original factory settings"),
    (re.compile(r"safe mode", re.I), "It will reveal problems caused by apps"),
    (re.compile(r"forc\w* (a )?restart|volume down", re.I), "It will reboot an unresponsive device"),
    (re.compile(r"restart|reboot", re.I), "It will refresh the device software"),
    (re.compile(r"clear cache|clear data|storage", re.I), "It will remove corrupted temporary app data"),
    (re.compile(r"back up|backup", re.I), "It will keep your personal data safe"),
    (re.compile(r"third-party apps|app updates", re.I), "It will fix bugs in installed apps"),
    (re.compile(r"power on|power button \(or side button\) for 15", re.I), "It will confirm the device powers on"),
    (re.compile(r"touch sensitivity", re.I), "It will tune how the screen responds"),
    (re.compile(r"navigation bar|gesture", re.I), "It will stop gestures blocking your taps"),
    (re.compile(r"wi-?fi|internet", re.I), "It will confirm your internet connection works"),
    (re.compile(r"walk-in|mail-in|repair|service cent|customer support", re.I), "It will get your device professionally repaired"),
    (re.compile(r"liquid damage|physical damage|corrosion|cracks", re.I), "It will reveal physical or liquid damage"),
    (re.compile(r"different, undamaged charger|different charger", re.I), "It will rule out a faulty charger"),
    (re.compile(r"charger and let it charge|charge for at least|fully charged", re.I), "It will restore enough battery power"),
    (re.compile(r"check the device, charger|cable for damage", re.I), "It will rule out damaged charging parts"),
    (re.compile(r"screen protector|\bwipe\b|\bclean", re.I), "It will remove interference from the screen"),
    (re.compile(r"hdmi|monitor", re.I), "It will mirror your screen externally"),
    (re.compile(r"mouse|keyboard", re.I), "It will let you reach your data"),
    (re.compile(r"data transfer|wireless transfer", re.I), "It will move your data between devices"),
    (re.compile(r"third-party apps|app updates", re.I), "It will fix bugs in installed apps"),
    (re.compile(r"power on|power button", re.I), "It will confirm the device powers on"),
]
DESC_DEFAULT = "It will help resolve this issue"


def valid_description(d: str) -> bool:
    return bool(d) and d.startswith("It will ") and 5 <= word_count(d) <= 7 and not has_leak(d) and not d.endswith(".")


def description_for(text: str) -> str:
    for rx, d in DESC_RULES:
        if rx.search(text):
            return d
    return DESC_DEFAULT


def fit_description(candidate: Optional[str], fallback_text: str) -> Tuple[str, str]:
    """Return (description, source). Repair order: accept -> trim trailing period/fillers -> deterministic."""
    if candidate:
        c = normalize(candidate).rstrip(".")
        if not c.lower().startswith("it will"):
            c = "It will " + c[0].lower() + c[1:] if c else c
        c = "It will" + c[7:]
        if valid_description(c):
            return c, "llm"
        words = c.split(" ")
        fillers = {"really", "quickly", "easily", "simply", "also", "just", "very", "the", "your", "a", "an"}
        while word_count(" ".join(words)) > 7:
            idx = next((i for i in range(len(words) - 1, 1, -1) if words[i].lower() in fillers), None)
            if idx is None:
                break
            words.pop(idx)
        c2 = " ".join(words)
        if valid_description(c2):
            return c2, "llm-repaired"
    return description_for(fallback_text), "deterministic"


# ----------------------------------------------------------------------------- dummy deeplink text
def _fit_words(options: List[str], lo: int = 5, hi: int = 7) -> str:
    for o in options:
        if lo <= word_count(o) <= hi:
            return o
    o = options[-1]
    words = o.split(" ")[:hi]
    while len(words) < lo:
        words.append("screen" if "screen" not in words else "now")
    return " ".join(words)


def dummy_texts(screen: str) -> Tuple[str, str]:
    s = normalize(screen)
    desc = _fit_words([f"Opens the {s} screen in Settings", f"Opens the {s} settings screen", f"Opens {s} in Settings",
                       f"Opens the {s} screen", f"Opens {s}"])
    msg = _fit_words([f"Open {s} in device Settings", f"Open the {s} screen", f"Open {s} in Settings", f"Open {s}"])
    return desc, msg


# ----------------------------------------------------------------------------- steps
_IMPERATIVE_BAD_START = re.compile(r"^(?:you|we|i|it|this|there|the|your|my)\b", re.I)


def clean_step(s: str) -> str:
    s = scrub(s)
    s = cap_first(normalize(s))
    if s and s[-1] not in ".!?":
        s += "."
    return s


def valid_step(s: str) -> bool:
    return bool(s) and not has_leak(s) and not _IMPERATIVE_BAD_START.match(s) and word_count(s) >= 2


def validate_goal_obj(goal: dict) -> List[str]:
    """Full rule audit of a Goal dict (used in the response gate, tests and metrics)."""
    errs = []
    if not valid_goal(goal.get("goal", "")):
        errs.append("goal-syntax")
    if not valid_title(goal.get("title", "")):
        errs.append("title-2to3-sentence-case")
    sc = goal.get("score")
    if not isinstance(sc, float) or not 0.0 <= sc <= 1.0:
        errs.append("score-range")
    names = set()
    for a in goal.get("actions", []):
        if not valid_action_name(a.get("actionName", "")):
            errs.append(f"actionName:{a.get('actionName')}")
        if a.get("actionName") in names:
            errs.append(f"duplicate-action:{a.get('actionName')}")
        names.add(a.get("actionName"))
        if not valid_description(a.get("description", "")):
            errs.append(f"description:{a.get('description')}")
        cat = a.get("category")
        for g in a.get("stepGroups", []):
            for st in g.get("steps", []):
                if not valid_step(st):
                    errs.append(f"step:{st}")
            if cat == "manual" and g.get("actionableDeeplink"):
                errs.append("manual-with-deeplink")
    cats = [a.get("category") for a in goal.get("actions", [])]
    if "critical" in cats and any(c != "critical" for c in cats[cats.index("critical"):]):
        errs.append("critical-not-last")
    return errs
