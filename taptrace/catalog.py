"""Catalog Compiler: turns the noisy 578-row deeplinks.json into a Settings *ontology*.

Facts this module encodes (all verified against data/deeplinks.json, see docs/DATA_AUDIT.md):
  * URIs are opaque masked tokens -> we NEVER match on them, only on metadata (PDF 7.4).
  * `message` is unreliable (DL-0162 "Disable Grayscale" is mono audio; DL-0497 offURL says "Enable")
    -> lowest weight; `validation.key` (the on-screen label) + description object are primary.
  * 21 TV / refrigerator / air-conditioner rows are distractors for a phone engine -> quarantined.
  * DL-0294/0295 have corrupted type fields -> quarantined.
  * 6 exact-duplicate pairs -> deterministic canonical = lowest id.
  * The same screen appears as up to four *variants*: open (onClickURL), on (onURL), off (offURL),
    set (updateURL). Variant choice is driven by step polarity, not by embedding similarity.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .text import jaccard, key_norm, normalize, tokens

DUMMY_URI = "voiceassist://dummy_positive"

VARIANT_OF_TYPE = {"onClickURL": "open", "onURL": "on", "offURL": "off", "updateURL": "set"}

_TAIL = re.compile(r"\s+(?:in|via) (?:device Settings|TechCorp [A-Za-z ]+|TV Settings|TV VoiceAssist)(?: on the device)?\.?$")
_TAIL2 = re.compile(r"\s+on the device(?: via device Settings)?\.?$")


@dataclass
class Row:
    id: str
    uri: str
    description: str
    message: str
    original_type: Optional[str]
    control_type: Optional[int]
    qna: str
    validation: Optional[dict]
    variant: str  # open | on | off | set | diag | placeholder
    domain: str  # phone | tv | iot | corrupt | placeholder
    obj: str  # parsed object of the description ("touch sensitivity")
    label: str  # on-screen label (validation.key) or object
    label_norm: str
    obj_norm: str
    duplicate_of: Optional[str] = None
    group: str = ""  # screen group id (siblings share it)

    @property
    def usable(self) -> bool:
        return self.domain == "phone" and self.duplicate_of is None


def _parse_object(desc: str, otype: Optional[str]) -> tuple[str, str]:
    d = normalize(desc)
    core = _TAIL.sub("", d)
    core = _TAIL2.sub("", core).rstrip(".")
    variant = VARIANT_OF_TYPE.get(otype or "", "diag")
    m = re.match(r"^Opens the (.+)$", core)
    if m:
        obj = m.group(1)
        obj = re.sub(r"\s+settings page$", "", obj)
        obj = re.sub(r"\s+page$", "", obj)
        obj = re.sub(r"\s+settings$", "", obj)
        return obj, variant
    for pat in (r"^Enables (.+)$", r"^Disables (.+)$"):
        m = re.match(pat, core)
        if m:
            return m.group(1), variant
    m = re.match(r"^Updates the (.+?) to a specified value$", core)
    if m:
        return m.group(1), variant
    return core, variant


def _domain(e: dict) -> str:
    d = e["description"] + " " + e["message"]
    if e["deeplink"] == DUMMY_URI:
        return "placeholder"
    if re.search(r"\bTV (Settings|VoiceAssist)\b", d):
        return "tv"
    if re.search(r"Refrigerator|Air Conditioner", d):
        return "iot"
    if e.get("originalType") is None and e["message"] in ("Offurl", "Onurl"):
        return "corrupt"
    return "phone"


class Catalog:
    def __init__(self, path: Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.raw_count = raw.get("count")
        self.rows: List[Row] = []
        self.by_uri: Dict[str, Row] = {}
        self.val_uris: set[str] = set()
        seen: Dict[tuple, str] = {}
        for e in raw["deeplinks"]:
            obj, variant = _parse_object(e["description"], e.get("originalType"))
            dom = _domain(e)
            if dom == "placeholder":
                variant = "placeholder"
            val = e.get("validation")
            label = (val or {}).get("key") or obj
            r = Row(
                id=e["id"], uri=e["deeplink"], description=e["description"], message=e["message"] or "",
                original_type=e.get("originalType"), control_type=e.get("control_type"),
                qna=e.get("qna_description") or "", validation=val, variant=variant, domain=dom,
                obj=obj, label=label, label_norm=key_norm(label), obj_norm=key_norm(obj),
            )
            sig = (normalize(e["description"]), e.get("originalType"))
            if sig in seen:
                r.duplicate_of = seen[sig]
            else:
                seen[sig] = r.id
            self.rows.append(r)
            self.by_uri[r.uri] = r
            if val and val.get("deeplink"):
                self.val_uris.add(val["deeplink"])
        self.all_uris = set(self.by_uri)
        self._group_screens()
        self.usable = [r for r in self.rows if r.usable]
        self.label_index: Dict[str, List[Row]] = {}
        for r in self.usable:
            for k in {r.label_norm, r.obj_norm}:
                if k:
                    self.label_index.setdefault(k, []).append(r)
        self.fingerprint = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
        self._tok_cache = {r.id: set(tokens(r.label + " " + r.obj)) for r in self.usable}
        self.embeddings: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ screens
    def _group_screens(self) -> None:
        """Siblings = same on-screen label AND near-identical object (Jaccard >= 0.6).

        "Charging" vibration (0058/59) and "Charging" sounds (0060/61) share a label but are different
        screens -> Jaccard 0.25 keeps them apart; Wi-Fi 0313 (open) and 0573/0574 (toggles) merge.
        """
        groups: List[List[Row]] = []
        for r in self.rows:
            if r.domain != "phone":
                r.group = f"Q-{r.id}"
                continue
            placed = False
            for g in groups:
                h = g[0]
                if h.label_norm == r.label_norm and (
                    jaccard(tokens(h.obj), tokens(r.obj)) >= 0.6 or h.obj_norm == r.obj_norm
                ):
                    g.append(r)
                    placed = True
                    break
            if not placed:
                groups.append([r])
        for i, g in enumerate(groups):
            for r in g:
                r.group = f"S{i:03d}"
        self.groups: Dict[str, List[Row]] = {}
        for r in self.rows:
            self.groups.setdefault(r.group, []).append(r)

    def siblings(self, row: Row) -> List[Row]:
        return [r for r in self.groups.get(row.group, [row]) if r.usable]

    def pick_variant(self, row: Row, desired: str) -> Row:
        """Choose the sibling whose control semantics match the step polarity.

        Preference chains are explicit so the choice is explainable in the trace:
          open -> open, on, set        (a plain "tap X" opens the screen; if only toggles exist, the
                                         step is about activating X, e.g. "Select Back up data" -> on)
          on   -> on, open, set
          off  -> off, open, set
          set  -> set, open, on
        """
        chain = {
            "open": ["open", "on", "set", "diag"],
            "on": ["on", "open", "set"],
            "off": ["off", "open", "set"],
            "set": ["set", "open", "on"],
        }.get(desired, ["open", "on", "set", "diag"])
        sib = self.siblings(row)
        for v in chain:
            cands = sorted([s for s in sib if s.variant == v], key=lambda s: s.id)
            if cands:
                return cands[0]
        return row

    # ------------------------------------------------------------------ dense index
    def row_text(self, r: Row) -> str:
        return f"{r.label}. {r.obj}. {r.qna}"

    def build_embeddings(self, embedder, cache_dir: Path) -> None:
        cache = Path(cache_dir) / f"catalog_emb_{self.fingerprint}_{embedder.model_id}.npy"
        if cache.exists():
            self.embeddings = np.load(cache)
        else:
            self.embeddings = embedder.encode([self.row_text(r) for r in self.usable])
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.save(cache, self.embeddings)

    # ------------------------------------------------------------------ report
    def noise_report(self) -> dict:
        tv = [r.id for r in self.rows if r.domain == "tv"]
        iot = [r.id for r in self.rows if r.domain == "iot"]
        corrupt = [r.id for r in self.rows if r.domain == "corrupt"]
        dups = [(r.duplicate_of, r.id) for r in self.rows if r.duplicate_of]
        mislead = []
        for r in self.rows:
            if r.domain != "phone":
                continue
            if r.variant == "off" and not r.message.lower().startswith("disable"):
                mislead.append((r.id, r.message, "offURL whose message is not 'Disable ...'"))
            elif r.variant == "on" and not r.message.lower().startswith("enable"):
                mislead.append((r.id, r.message, "onURL whose message is not 'Enable ...'"))
            elif r.variant in ("on", "off"):
                split = lambda t: {p for x in tokens(t) for p in x.split("-")}
                mt = split(r.message) - {"enable", "disable"}
                if mt and not (mt & (split(r.obj) | split(r.label))):
                    mislead.append((r.id, r.message, f"message names a different feature than '{r.obj}'"))
        multi = {g: [r.id for r in rs] for g, rs in self.groups.items() if len([r for r in rs if r.usable]) > 1}
        return {
            "declared_count": self.raw_count,
            "rows": len(self.rows),
            "usable_phone_rows": len(self.usable),
            "tv_distractors": tv,
            "iot_distractors": iot,
            "corrupt_rows": corrupt,
            "exact_duplicates": dups,
            "misleading_messages": mislead,
            "screens_with_multiple_variants": len(multi),
            "screens_total": len({r.group for r in self.usable}),
        }


def row_public(r: Row) -> dict:
    d = asdict(r)
    return {k: d[k] for k in ("id", "uri", "label", "obj", "variant", "domain", "group")}
