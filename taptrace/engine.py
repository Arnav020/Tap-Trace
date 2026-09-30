"""TapTrace engine: the end-to-end pipeline behind POST /v1/troubleshoot.

  [0]  facets + normalisation (no LLM)            -> cache key
  [3]  FAST PATH: exact / contrastive semantic hit -> return (no LLM, $0)
  [1a] SIIS normalise -> Relevance Gate            -> no_match if nothing viable (PDF 4.2.3)
  [1b] grounded candidate units (verbatim steps + span ids)
  [0b] Stage A LLM enrichment      \\ run in parallel, each with a hard timeout;
  [1c] Stage B LLM structuring     /  deterministic fallback on timeout / rate-limit
  [2]  screen resolution + category + sequencing + field compiler
  [gate] organisers' pydantic schema + catalog-integrity + zero-leak + rule audit
  [3w] cache write with per-entry calibrated threshold
"""
from __future__ import annotations

import concurrent.futures as cf_
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from .cache import SemanticCache
from .catalog import DUMMY_URI, Catalog
from .embed import Embedder
from .facets import frame
from .fields import has_leak
from .llm import LLM, Usage
from .llm_stage import canonical_fallback, enrich, fallback_variations, negatives_fallback, structure
from .plan import Composer, rule_errors, to_contract
from .resolver import Resolver
from .siis import build_units, gate, parse_siis
from .text import normalize

ROOT = Path(__file__).resolve().parents[1]
COLD_BUDGET_S = float(os.getenv("TAPTRACE_COLD_BUDGET_S", "7.0"))


class Engine:
    def __init__(self, root: Path = ROOT, use_llm: bool = True, cache_path: Optional[Path] = None,
                 cache_mode: str = "adaptive"):
        t0 = time.perf_counter()
        self.root = Path(root)
        self.embedder = Embedder(self.root / "artifacts" / "model")
        self.catalog = Catalog(self.root / "data" / "deeplinks.json")
        self.catalog.build_embeddings(self.embedder, self.root / "artifacts")
        self.resolver = Resolver(self.catalog, self.embedder)
        self.composer = Composer(self.catalog, self.resolver)
        self.llm = LLM() if use_llm else _NoLLM()
        cache_path = cache_path or os.getenv("TAPTRACE_CACHE_PATH") or (self.root / "artifacts" / "cache.sqlite")
        self.cache = SemanticCache(cache_path, self.embedder, mode=cache_mode)
        self.pool = cf_.ThreadPoolExecutor(max_workers=4)
        self.embedder.encode_one("warm up")  # first ONNX run allocates buffers; keep it off the request path
        self.startup_ms = int((time.perf_counter() - t0) * 1000)
        self.stats = {"requests": 0, "hits": 0, "cold": 0, "no_match": 0, "no_siis": 0, "cost_usd": 0.0}

    # ------------------------------------------------------------------ public
    def troubleshoot(self, query: str, siis: Any = None, explain: bool = False, use_cache: bool = True,
                     write_cache: bool = True, wait_budget: float = 0.0, cache_mode: Optional[str] = None) -> Dict:
        t0 = time.perf_counter()
        self.stats["requests"] += 1
        query = normalize(query)
        cf = frame(query)
        siis_given = siis not in (None, "", {})
        doc = parse_siis(siis) if siis_given else None
        trace: Dict[str, Any] = {"frame": cf.as_dict()}

        # ---- [3] fast path
        if use_cache:
            hit, info = self.cache.lookup(query, cf, doc.fingerprint if doc else None, mode=cache_mode)
            trace["cache"] = info
            if hit is not None:
                self.stats["hits"] += 1
                e = hit.entry
                p = e["payload"]
                meta = {"latency_ms": _ms(t0), "cache_hit": True, "model": p["model"], "cost_usd": 0.0,
                        "fallback": p.get("fallback"), "cache_match": hit.match, "cache_similarity": round(hit.sim, 4),
                        "cache_threshold": hit.tau, "cache_entry": e["id"]}
                out = {"query": query, "query_variations": e["variations"], "response": p["response"], "meta": meta}
                if explain:
                    out["trace"] = {**trace, "served_from": e["query"], "original_trace": p.get("trace")}
                return out

        # ---- no knowledge and no cache: honest fallback
        if not siis_given:
            self.stats["no_siis"] += 1
            variations = fallback_variations(query, cf)
            meta = {"latency_ms": _ms(t0), "cache_hit": False, "model": self.llm.label, "cost_usd": 0.0,
                    "fallback": "no_siis_context"}
            out = {"query": query, "query_variations": variations, "response": {"contexts": []}, "meta": meta}
            if explain:
                out["trace"] = trace
            return out

        # ---- cold path
        self.stats["cold"] += 1
        timeout = max(2.0, COLD_BUDGET_S - 1.0)
        f_enrich = self.pool.submit(enrich, self.llm, query, cf, timeout, wait_budget)

        doc_body = doc.title + ". " + " ".join(x.text for s in doc.sections for x in s.sentences)[:1500]
        doc_sim = float(self.embedder.encode_one(query) @ self.embedder.encode_one(doc_body))
        g = gate(doc, cf, doc_sim)
        trace["gate"] = {"fit": g.fit, "fit_score": g.fit_score, "reason": g.reason, "kept_sections": g.kept_sections,
                         "dropped": g.dropped}
        units = build_units(doc, g, cf) if g.fit != "none" else []
        fallback = None
        if g.fit == "partial" and units and all(u.__dict__.get("escalation") for u in units):
            trace["gate"]["verdict"] = "partial fit with escalation-only remedies -> no_match"
            units = []
        resolutions = self.composer.resolve_units(units)
        trace["units"] = [{"uid": u.uid, "kind": u.kind, "category": u.category, "polarity": u.polarity,
                           "targets": u.targets, "spans": u.spans, "steps": u.steps,
                           "evidence": {sid: doc.span_text(sid) for sid in u.spans}} for u in units]

        struct, s_usage = (None, Usage())
        if units:
            enrich_peek = f_enrich.result() if f_enrich.done() else None
            canonical = (enrich_peek[0]["canonical_query"] if enrich_peek else canonical_fallback(query, cf))
            struct, s_usage = structure(self.llm, query, canonical, units, resolutions, timeout, wait_budget)
            if struct and struct.get("no_viable_solution") and g.fit == "partial":
                trace["llm_verdict"] = "LLM confirms no viable solution for a partial fit -> no_match"
                units = []
        try:
            enr, e_usage = f_enrich.result(timeout=max(0.5, COLD_BUDGET_S - (time.perf_counter() - t0)))
        except Exception:  # noqa: BLE001
            enr = {"canonical_query": canonical_fallback(query, cf), "variations": fallback_variations(query, cf),
                   "hard_negatives": negatives_fallback(query), "source": "deterministic-timeout"}
            e_usage = Usage(error="timeout")

        goals = self.composer.compose(units, resolutions, cf, doc, g.fit_score, struct) if units else []
        goals, gate_log = self._hard_gate(goals)
        if not goals:
            fallback = "no_match"
            self.stats["no_match"] += 1
        response = to_contract(goals)
        usage = e_usage + s_usage
        self.stats["cost_usd"] += usage.cost_usd
        model = self.llm.label if (usage.ok) else "deterministic (no LLM)"
        meta = {"latency_ms": _ms(t0), "cache_hit": False, "model": model, "cost_usd": round(usage.cost_usd, 6),
                "fallback": fallback, "knowledge_fit": g.fit,
                "tokens": {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens},
                "llm_stages": {"enrichment": enr.get("source"), "structuring": "llm" if struct else "deterministic"}}
        trace.update({"enrichment": {"canonical_query": enr["canonical_query"], "hard_negatives": enr["hard_negatives"]},
                      "structuring": struct, "actions": [t for gg in goals for t in gg.get("_trace", [])],
                      "output_gate": gate_log, "resolver_memo_hits": self.resolver.memo_hits})
        out = {"query": query, "query_variations": enr["variations"][:10], "response": response, "meta": meta}
        if write_cache:
            self.cache.put(query, enr["canonical_query"], enr["variations"][:10], enr["hard_negatives"], cf,
                           doc.fingerprint, {"response": response, "model": model, "fallback": fallback,
                                             "trace": trace if explain else None})
        if explain:
            out["trace"] = trace
        return out

    # ------------------------------------------------------------------ hard output gate
    def _hard_gate(self, goals):
        """Non-negotiables enforced in code: catalog integrity, zero leakage, manual=no deeplink, rules."""
        log = []
        for g in goals:
            kept = []
            for a in g["actions"]:
                bad = False
                for sg in a["stepGroups"]:
                    dl = sg.get("actionableDeeplink")
                    if dl and dl["deeplink"] != DUMMY_URI and dl["deeplink"] not in self.catalog.all_uris:
                        bad = True
                        log.append(f"dropped '{a['actionName']}': URI not in catalog")
                    vd = sg.get("validationDeeplink")
                    if vd and vd["deeplink"] not in self.catalog.val_uris:
                        sg["validationDeeplink"] = None
                        log.append(f"removed validation from '{a['actionName']}': URI not in catalog")
                    if a["category"] == "manual" and dl:
                        sg["actionableDeeplink"], sg["validationDeeplink"] = None, None
                        log.append(f"manual action '{a['actionName']}' stripped of deeplink")
                    sg["steps"] = [s for s in sg["steps"] if not has_leak(s)]
                    if not sg["steps"]:
                        bad = True
                if not bad:
                    kept.append(a)
            g["actions"] = kept
        goals = [g for g in goals if g["actions"]]
        errs = rule_errors(goals)
        if errs:
            log.append({"rule_errors": errs})
        return goals, log


class _NoLLM(LLM):
    def __init__(self):
        self.provider, self.available, self.model = "none", False, "deterministic"


def _ms(t0: float) -> int:
    return int(round((time.perf_counter() - t0) * 1000))
