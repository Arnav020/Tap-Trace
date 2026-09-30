"""Deterministic text utilities shared by every stage (normalisation, tokens, casing, word counts)."""
from __future__ import annotations

import re
import unicodedata
from typing import List

_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")

# Canonical spellings so "wifi" / "Wi-Fi" / "wi fi" collapse to one token.
_CANON = {
    "wifi": "wi-fi",
    "wlan": "wi-fi",
    "bt": "bluetooth",
    "display": "screen",
    "monitor": "screen",
    "touchscreen": "touch",
    "powered": "power",
    "cellphone": "phone",
    "smartphone": "phone",
    "mobile": "phone",
    "handset": "phone",
    "reboot": "restart",
    "rebooting": "restart",
    "backup": "back-up",
    "brightness": "bright",
}

STOPWORDS = frozenset(
    """a an the and or but if then than to of in on at for from by with without into onto over under
    is are was were be been being am do does did done doing have has had having it its it's this that these
    those there here my your our their his her i me we you they them he she us so as up down out off not no
    can could would should will shall may might must just only also very too much more most some any all each
    every both either neither via when while after before again once about against between through during
    what which who whom whose why how where settings setting page device phone option options""".split()
)

_SUFFIXES = ("ing", "edly", "ed", "es", "s", "ly")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("—", " - ").replace("–", " - ")
    for h in ("‐", "‑", "‒", "­"):
        text = text.replace(h, "-" if h != "­" else "")
    return _WS.sub(" ", text).strip()


def stem(tok: str) -> str:
    if len(tok) <= 4 or "-" in tok:
        return tok
    for suf in _SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


def raw_tokens(text: str) -> List[str]:
    return _TOKEN.findall(normalize(text).lower())


def tokens(text: str, keep_stop: bool = False) -> List[str]:
    out = []
    for t in raw_tokens(text):
        t = _CANON.get(t, t)
        if not keep_stop and t in STOPWORDS:
            continue
        out.append(stem(t))
    return out


def key_norm(text: str) -> str:
    """Normalised phrase used for exact screen-label equality (keeps stopwords, canonicalises)."""
    toks = [_CANON.get(t, t) for t in raw_tokens(text)]
    return " ".join(toks)


def word_count(text: str) -> int:
    return len([w for w in normalize(text).split(" ") if w])


_MINOR = frozenset("a an the and or but nor for of in at to by via as vs".split())
_KEEP_CASE = {"wi-fi": "Wi-Fi", "techcorp": "TechCorp", "nexa": "Nexa", "usb": "USB", "hdmi": "HDMI",
              "ldi": "LDI", "pc": "PC", "ok": "OK", "qr": "QR", "sim": "SIM", "nfc": "NFC", "dex": "DeX",
              "voiceassist": "VoiceAssist", "gmail": "Gmail", "google": "Google", "android": "Android",
              "bluetooth": "Bluetooth", "tv": "TV", "ui": "UI", "id": "ID"}


def title_case(text: str) -> str:
    words = normalize(text).split(" ")
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _KEEP_CASE:
            out.append(_KEEP_CASE[lw])
        elif i > 0 and lw in _MINOR:
            out.append(lw)
        elif "-" in w:
            out.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def sentence_case(text: str) -> str:
    words = normalize(text).split(" ")
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _KEEP_CASE:
            out.append(_KEEP_CASE[lw])
        elif i == 0:
            out.append(lw[:1].upper() + lw[1:])
        else:
            out.append(lw)
    return " ".join(out)


def is_title_case(text: str) -> bool:
    return title_case(text) == normalize(text)


def is_sentence_case(text: str) -> bool:
    return sentence_case(text) == normalize(text)


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def ensure_period(s: str) -> str:
    s = normalize(s).rstrip(" ;:,")
    if not s:
        return s
    return s if s[-1] in ".!?" else s + "."


def cap_first(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
