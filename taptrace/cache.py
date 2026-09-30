"""Contrastively calibrated semantic cache (U4).

Why not a single cosine threshold? A false hit serves a *wrong fix* instantly and confidently, which is
worse than a miss. "Screen won't turn ON" and "screen won't turn OFF" embed very close together.
So every entry stores:
  anchors   : the original query, the canonical query and its 8-10 paraphrases (positives)
  negatives : LLM/rule-generated hard negatives (similar words, different fix)
  tau       : an entry-specific threshold placed above the closest negative and at or below the
              typical positive, clipped to [TAU_FLOOR, TAU_CEIL]
  frame     : symbolic facets; a hit additionally requires facet compatibility (polarity, screen part,
              symptom family, problem context), and the same SIIS knowledge fingerprint if SIIS was sent.
Persistence: SQLite (entries + float32 anchor vectors), so a restart is warm and scales to 10k+ entries.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .facets import Frame, compatible
from .text import normalize

TAU_FLOOR = 0.66
TAU_CEIL = 0.90
TAU_GLOBAL = 0.78  # used only for the ablation "global threshold" variant
NEG_MARGIN = 0.03


def qkey(text: str) -> str:
    return " ".join(normalize(text).lower().replace('"', "").split())


def frame_from_dict(d: dict) -> Frame:
    return Frame(frozenset(d.get("symptoms", [])), frozenset(d.get("contexts", [])), d.get("device", ""),
                 d.get("part", ""), d.get("polarity", ""), bool(d.get("config_request")),
                 frozenset(d.get("components", [])))


@dataclass
class Hit:
    entry: dict
    sim: float
    match: str  # exact | semantic
    tau: float


class SemanticCache:
    def __init__(self, path: Path, embedder, mode: str = "adaptive"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.emb = embedder
        self.mode = mode
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, qkey TEXT, body TEXT, anchors BLOB, created REAL)")
        self.db.commit()
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        self.entries: List[dict] = []
        self.exact = {}
        mats, owners = [], []
        for eid, qk, body, blob, _ in self.db.execute("SELECT id, qkey, body, anchors, created FROM entries ORDER BY created"):
            e = json.loads(body)
            e["_frame"] = frame_from_dict(e["frame"])
            idx = len(self.entries)
            self.entries.append(e)
            for k in e.get("exact_keys", [qk]):
                self.exact.setdefault(k, idx)
            vec = np.frombuffer(blob, dtype=np.float32).reshape(-1, self.emb.dim)
            mats.append(vec)
            owners += [idx] * len(vec)
        self.A = np.vstack(mats) if mats else np.zeros((0, self.emb.dim), dtype=np.float32)
        self.owner = np.array(owners, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM entries")
            self.db.commit()
            self._load()

    # ------------------------------------------------------------------ write
    def put(self, query: str, canonical: str, variations: List[str], negatives: List[str], frame: Frame,
            siis_fp: Optional[str], payload: dict) -> dict:
        anchors_txt = [query, canonical] + list(variations)
        A = self.emb.encode(anchors_txt)
        # Division of labour: negatives the symbolic guard already rejects (polarity / part / component /
        # symptom conflicts) must not inflate the metric threshold - only guard-passing ones do.
        from .facets import frame as _frame
        guarded = [n for n in negatives if compatible(_frame(n), frame)[0]]
        N = self.emb.encode(guarded) if guarded else np.zeros((0, self.emb.dim), dtype=np.float32)
        tau, stats = calibrate(A, N)
        stats["negatives_rejected_by_guard"] = len(negatives) - len(guarded)
        e = {
            "id": f"C{int(time.time() * 1000)}-{len(self.entries)}",
            "query": query,
            "canonical": canonical,
            "variations": list(variations),
            "negatives": list(negatives),
            "frame": frame.as_dict(),
            "siis_fp": siis_fp,
            "tau": tau,
            "calibration": stats,
            "payload": payload,
            "exact_keys": sorted({qkey(query), qkey(canonical)}),
        }
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)",
                            (e["id"], qkey(query), json.dumps(e), A.astype(np.float32).tobytes(), time.time()))
            self.db.commit()
            e["_frame"] = frame
            idx = len(self.entries)
            self.entries.append(e)
            for k in e["exact_keys"]:
                self.exact.setdefault(k, idx)
            self.A = np.vstack([self.A, A.astype(np.float32)])
            self.owner = np.concatenate([self.owner, np.full(len(A), idx, dtype=np.int64)])
        return e

    def recalibrate(self) -> int:
        """Recompute facet frames + thresholds of all entries from stored anchors/negatives (no LLM).

        Needed whenever the facet lexicon or the calibration rule changes; keeps 10k+ entries consistent.
        """
        from .facets import frame as _frame
        n = 0
        with self.lock:
            rows = list(self.db.execute("SELECT id, qkey, body, anchors, created FROM entries"))
            for eid, qk, body, blob, created in rows:
                e = json.loads(body)
                fr = _frame(e["query"])
                A = np.frombuffer(blob, dtype=np.float32).reshape(-1, self.emb.dim)
                guarded = [x for x in e["negatives"] if compatible(_frame(x), fr)[0]]
                N = self.emb.encode(guarded) if guarded else np.zeros((0, self.emb.dim), dtype=np.float32)
                tau, stats = calibrate(A, N)
                stats["negatives_rejected_by_guard"] = len(e["negatives"]) - len(guarded)
                e["frame"], e["tau"], e["calibration"] = fr.as_dict(), tau, stats
                self.db.execute("UPDATE entries SET body=? WHERE id=?", (json.dumps(e), eid))
                n += 1
            self.db.commit()
        self._load()
        return n

    # ------------------------------------------------------------------ read
    def lookup(self, query: str, frame: Frame, siis_fp: Optional[str], mode: Optional[str] = None) -> Tuple[Optional[Hit], dict]:
        mode = mode or self.mode
        info = {"checked": len(self.entries)}
        k = qkey(query)
        if k in self.exact:
            e = self.entries[self.exact[k]]
            if siis_fp is None or e["siis_fp"] == siis_fp:
                return Hit(e, 1.0, "exact", e["tau"]), info
            info["exact_rejected"] = "different SIIS knowledge"
        if not len(self.entries):
            return None, info
        v = self.emb.encode_one(normalize(query))
        sims = self.A @ v
        best = np.full(len(self.entries), -1.0, dtype=np.float32)
        np.maximum.at(best, self.owner, sims)
        for idx in np.argsort(-best)[:3]:
            e = self.entries[int(idx)]
            s = float(best[idx])
            tau = e["tau"] if mode == "adaptive" else TAU_GLOBAL
            cand = {"entry": e["id"], "sim": round(s, 4), "tau": tau}
            if s < tau:
                cand["rejected"] = "below-threshold"
                info.setdefault("candidates", []).append(cand)
                break
            if mode == "adaptive":
                ok, why = compatible(frame, e["_frame"])
                if not ok:
                    cand["rejected"] = why
                    info.setdefault("candidates", []).append(cand)
                    continue
            if siis_fp is not None and e["siis_fp"] != siis_fp:
                cand["rejected"] = "different SIIS knowledge"
                info.setdefault("candidates", []).append(cand)
                continue
            return Hit(e, s, "semantic", tau), info
        return None, info


def calibrate(A: np.ndarray, N: np.ndarray) -> Tuple[float, dict]:
    """Per-entry threshold from the entry's own positive and negative clouds.

    pos_i  = max cosine of positive i to the OTHER anchors (leave-one-out, mimics an unseen paraphrase)
    neg_j  = max cosine of negative j to any anchor
    tau    = clip(max(neg) + margin, TAU_FLOOR, max(TAU_FLOOR, min(TAU_CEIL, min(pos))))
             where neg only contains negatives that pass the facet guard (see SemanticCache.put).
    """
    if len(A) >= 3:
        S = A @ A.T
        np.fill_diagonal(S, -1.0)
        pos = S.max(axis=1)[2:]  # the paraphrases (skip query & canonical themselves)
        pos_p20 = float(np.percentile(pos, 20))
    else:
        pos, pos_p20 = np.array([1.0]), 1.0
    neg_max = float((N @ A.T).max()) if len(N) else 0.0
    # Above the closest guard-passing negative, but never above the entry's own paraphrase cloud (p20):
    # a negative that sits INSIDE the positive cloud cannot be separated by any cosine threshold, only by
    # the symbolic guard - raising tau further would just destroy recall.
    # v2: cap at the LEAST similar LLM paraphrase (not p20). LLM paraphrase clouds are tighter than real
    # users' rewordings (measured in evaluation v1), so a p20 cap rejected genuine paraphrases.
    cap = max(TAU_FLOOR, min(TAU_CEIL, float(pos.min())))
    tau = float(min(max(TAU_FLOOR, neg_max + NEG_MARGIN), cap))
    return round(tau, 4), {"pos_p20": round(pos_p20, 4), "pos_min": round(float(pos.min()), 4),
                           "neg_max": round(neg_max, 4), "n_pos": int(len(pos)), "n_neg": int(len(N))}
