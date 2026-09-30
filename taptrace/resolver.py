"""Screen-exact, polarity-aware deeplink resolution with abstention.

Resolution ladder for a Settings chain (unit.kind == "nav"):
  1. EXACT LABEL  - a tapped target equals a screen's on-screen label (validation.key) or parsed object.
                    Deepest target wins (PDF 6.2: "exact target screens rather than high-level parent menus").
  2. FUZZY        - leaf-target token overlap + dense cosine; accepted only above threshold AND with a
                    margin over the best *different* screen. Never used for critical actions: a near-miss
                    reset/restart screen is worse than no link (DL-0022 auto-factory-reset trap).
  3. ABSTAIN      - chain opens Settings but no screen matches -> voiceassist://dummy_positive with a
                    generated 5-7 word description/message naming the concrete screen (catalog rule).
Variant (open/on/off/set) is then chosen among the screen's siblings from step polarity.
URIs are always copied from the catalog object; they are never constructed or edited.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .catalog import DUMMY_URI, Catalog, Row
from .text import key_norm, tokens, word_count

FUZZY_MIN_CONF = 0.66
FUZZY_MIN_OVERLAP = 0.67
FUZZY_MIN_COS = 0.50
FUZZY_MIN_MARGIN = 0.04
PHYSICAL_METHOD = re.compile(r"\b(monitor|tv|hdmi|mouse|keyboard|computer|pc|cable|adapter)\b", re.I)
_GENERIC_TARGETS = {"display", "apps", "connections", "general management", "advanced features", "settings",
                    "accessibility", "security and privacy", "battery", "sounds and vibration", "notifications",
                    "reset", "storage", "about phone", "software update", "device care"}


@dataclass
class Resolution:
    row: Optional[Row]
    uri: Optional[str]
    confidence: float
    method: str  # exact-label | fuzzy | dummy | none
    screen_name: str = ""
    candidates: List[dict] = field(default_factory=list)
    reason: str = ""

    @property
    def is_dummy(self) -> bool:
        return self.uri == DUMMY_URI


class Resolver:
    def __init__(self, catalog: Catalog, embedder):
        self.cat = catalog
        self.emb = embedder
        self.memo: dict = {}  # step-path -> Resolution  (U6: reuse across scenarios)
        self.memo_hits = 0

    # ------------------------------------------------------------------ public
    def resolve_chain(self, targets: List[str], step_text: str, polarity: str, critical: bool) -> Resolution:
        key = ("chain", tuple(t.lower() for t in targets), polarity, critical)
        if key in self.memo:
            self.memo_hits += 1
            return self.memo[key]
        res = self._exact(targets, polarity)
        if res is None and not critical:
            res = self._fuzzy(targets, step_text, polarity)
        if res is None:
            res = Resolution(None, DUMMY_URI, 0.5, "dummy", screen_name=self._screen_name(targets),
                             reason="chain opens Settings but no catalog screen matches the target exactly")
        self.memo[key] = res
        return res

    def resolve_latent(self, step_text: str) -> Optional[Resolution]:
        """Non-navigation step that still names a Settings feature ("Back up your personal data").

        Accepted only when the step's head verb+object are contained in the screen label and the step
        does not prescribe a physical method (monitor/HDMI/mouse) - that would be a different action.
        """
        if PHYSICAL_METHOD.search(step_text):
            return None
        head = [t for t in tokens(step_text)][:4]
        if len(head) < 2:
            return None
        cands = []
        for r in self.cat.usable:
            if r.variant not in ("on", "open"):
                continue
            rt = set(tokens(r.label)) | set(tokens(r.obj))
            if head[0] in rt and any(t in rt for t in head[1:4]):  # verb AND an object word in the label
                cands.append(r)
        if not cands:
            return None
        qv = self.emb.encode_one(step_text)
        best = max(cands, key=lambda r: float(self.cat.embeddings[self._idx(r)] @ qv))
        cos = float(self.cat.embeddings[self._idx(best)] @ qv)
        if cos < 0.35:  # the verb+object label-containment check above is the primary guard
            return None
        row = self.cat.pick_variant(best, "on")
        return Resolution(row, row.uri, round(0.6 + 0.4 * cos, 3), "latent-label", screen_name=row.label,
                          reason=f"head '{' '.join(head[:2])}' contained in label '{row.label}' (cos={cos:.2f})")

    # ------------------------------------------------------------------ ladder
    def _exact(self, targets: List[str], polarity: str) -> Optional[Resolution]:
        """Only the leaf (and, if the leaf is an in-screen button/option, its screen) may match.

        Walking further up would land on parent menus ("Battery", "Display") - PDF pitfall 6.2.
        """
        if not targets:
            return None
        cands = [targets[-1]]
        if len(targets) >= 2 and targets[-2].lower() not in _GENERIC_TARGETS:
            cands.append(targets[-2])
        for t in cands:
            k = key_norm(re.sub(r"^(?:the|your)\s+", "", t, flags=re.I))
            rows = self.cat.label_index.get(k)
            if rows:
                by_label = [r for r in rows if r.label_norm == k] or rows
                base = sorted(by_label, key=lambda r: r.id)[0]
                row = self.cat.pick_variant(base, polarity)
                return Resolution(row, row.uri, 1.0, "exact-label", screen_name=row.label,
                                  reason=f"tapped target '{t}' equals screen label '{row.label}'; variant={row.variant}")
        return None

    def _fuzzy(self, targets: List[str], step_text: str, polarity: str) -> Optional[Resolution]:
        leafs = [t for t in targets if t.lower() not in _GENERIC_TARGETS]
        if not leafs:
            return None
        leaf = leafs[-1]
        q_tokens = set(tokens(leaf))
        if not q_tokens:
            return None
        qv = self.emb.encode_one(f"{leaf}. {step_text}")
        sims = self.cat.embeddings @ qv
        order = np.argsort(-sims)[:15]
        scored = []
        for i in order:
            r = self.cat.usable[int(i)]
            rt = set(tokens(r.label)) | set(tokens(r.obj))
            overlap = len(q_tokens & rt) / len(q_tokens)
            conf = 0.55 * overlap + 0.45 * float(sims[i])
            scored.append((conf, overlap, float(sims[i]), r))
        scored.sort(key=lambda x: -x[0])
        cands = [{"id": r.id, "label": r.label, "conf": round(c, 3), "overlap": round(o, 2), "cos": round(s, 3)}
                 for c, o, s, r in scored[:5]]
        if not scored:
            return None
        c0, o0, s0, r0 = scored[0]
        second = next((c for c, _, _, r in scored[1:] if r.group != r0.group), 0.0)
        if c0 >= FUZZY_MIN_CONF and o0 >= FUZZY_MIN_OVERLAP and s0 >= FUZZY_MIN_COS and c0 - second >= FUZZY_MIN_MARGIN:
            row = self.cat.pick_variant(r0, polarity)
            return Resolution(row, row.uri, round(c0, 3), "fuzzy", screen_name=row.label, candidates=cands,
                              reason=f"leaf '{leaf}' ~ '{r0.label}' conf={c0:.2f} margin={c0 - second:.2f}")
        return None

    def _idx(self, row: Row) -> int:
        if not hasattr(self, "_pos"):
            self._pos = {r.id: i for i, r in enumerate(self.cat.usable)}
        return self._pos[row.id]

    @staticmethod
    def _screen_name(targets: List[str]) -> str:
        """Concrete screen reached by the chain: deepest target that is a screen, not an in-screen button."""
        tg = [re.sub(r"^(?:the|your)\s+", "", t, flags=re.I) for t in targets]
        while len(tg) > 1 and re.match(r"^(clear|delete|ok|tap|swipe|reset$|buttons)", tg[-1], re.I):
            tg = tg[:-1]
        if not tg:
            return "Settings"
        leaf = tg[-1]
        if leaf.lower() in _GENERIC_TARGETS and len(tg) >= 2 and word_count(tg[-2]) <= 3:
            leaf = f"{tg[-2]} {leaf}"
        return leaf
