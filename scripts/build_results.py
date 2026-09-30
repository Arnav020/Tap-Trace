"""Pre-warm the fast-path cache and produce results.jsonl (PDF Appendix B format, one line per input).

    python scripts/build_results.py            # uses the LLM if a key is configured (.env), else deterministic
    python scripts/build_results.py --offline  # force deterministic mode
    python scripts/build_results.py --keep     # do not clear the cache first
    python scripts/build_results.py --reuse-enrichment <old cache.sqlite>
        # re-plan with the stored Stage A output of an earlier run (same query text). Stage A depends only on the
        # query, so a plan-assembly change does not need it re-generated; those rows run Stage B only and are
        # marked meta.llm_stages.enrichment = "reused:<model>" (evaluate.py excludes them from cold latency/cost).

Input line i of data/input.txt is paired with the i-th record of data/siis_responses.json (file order;
ids skip row_6/row_18). Every line is re-validated with the organisers' schema before it is written.
Per-row explain traces (provenance spans, gate verdict, resolver decisions) go to artifacts/traces/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from taptrace.engine import Engine  # noqa: E402
from taptrace.facets import frame  # noqa: E402
from taptrace.plan import contract  # noqa: E402
from taptrace.siis import parse_siis  # noqa: E402


def degraded(out) -> bool:
    st = out["meta"].get("llm_stages", {})
    if st.get("enrichment") != "llm" and not str(st.get("enrichment", "")).startswith("reused:"):
        return True
    return out["meta"].get("fallback") is None and st.get("structuring") != "llm"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--reuse-enrichment", default=None, help="old cache.sqlite whose Stage A output is reused per query")
    ap.add_argument("--pace", type=float, default=22.0,
                    help="seconds between LLM scenarios (Groq free tier = 8K tokens/min; ~2.7K tokens per cold query)")
    args = ap.parse_args()
    if args.offline:
        os.environ["TAPTRACE_OFFLINE"] = "1"
    eng = Engine(use_llm=not args.offline)
    reuse = {}
    if args.reuse_enrichment:
        import sqlite3

        for (body,) in sqlite3.connect(args.reuse_enrichment).execute("SELECT body FROM entries"):
            e = json.loads(body)
            src = (e.get("payload") or {}).get("model", "llm")
            reuse[e["query"]] = {"canonical_query": e["canonical"], "variations": e["variations"],
                                 "hard_negatives": e["negatives"], "source": f"reused:{src.split(' + ')[0]}"}
        print(f"reusing Stage A output for {len(reuse)} queries from {args.reuse_enrichment}")
    if not args.keep:
        eng.cache.clear()
    siis = json.loads((ROOT / "data" / "siis_responses.json").read_text(encoding="utf-8"))["responses"]
    queries = [l.strip() for l in (ROOT / "data" / "input.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(queries) == len(siis) == 20, (len(queries), len(siis))
    tdir = ROOT / "artifacts" / "traces"
    tdir.mkdir(parents=True, exist_ok=True)
    lines = []
    t_all = time.perf_counter()
    for i, (q, rec) in enumerate(zip(queries, siis), 1):
        if i > 1 and eng.llm.available:
            time.sleep(args.pace)  # pacing keeps each measured cold latency free of rate-limit queueing
        from taptrace.text import normalize as _norm
        enr_in = reuse.get(_norm(q))
        out = eng.troubleshoot(q, rec["siis_response"], explain=True, use_cache=False, write_cache=False, wait_budget=60,
                               enrichment=enr_in)
        if eng.llm.available and degraded(out):
            # a free-tier timeout degraded a stage to its deterministic fallback: retry once, keep the better result
            time.sleep(args.pace)
            out2 = eng.troubleshoot(q, rec["siis_response"], explain=True, use_cache=False, write_cache=False, wait_budget=60,
                                    enrichment=enr_in)
            if not degraded(out2):
                out = out2
                out["meta"]["retried"] = True
        trace = out.pop("trace")
        enr = trace["enrichment"]
        eng.cache.put(out["query"], enr["canonical_query"], out["query_variations"], enr["hard_negatives"], frame(out["query"]),
                      parse_siis(rec["siis_response"]).fingerprint,
                      {"response": out["response"], "model": out["meta"]["model"], "fallback": out["meta"]["fallback"], "trace": trace})
        contract.ContextDeeplinkResponse(**out["response"])  # hard re-validation
        (tdir / f"{i:02d}_{rec['id']}.json").write_text(json.dumps({"input_line": i, "siis_id": rec["id"], **out, "trace": trace},
                                                                   indent=2, ensure_ascii=False), encoding="utf-8")
        lines.append(json.dumps(out, ensure_ascii=False))
        m = out["meta"]
        n = sum(len(g["actions"]) for g in out["response"]["contexts"])
        print(f"{i:2d} {rec['id']:7s} fit={str(m.get('knowledge_fit')):8s} fallback={str(m['fallback']):8s} actions={n:2d} "
              f"{m['latency_ms']:5d}ms ${m['cost_usd']:.6f} {m.get('llm_stages')}", flush=True)
    (ROOT / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote results.jsonl ({len(lines)} lines) in {time.perf_counter() - t_all:.1f}s; cache entries={len(eng.cache)}")


if __name__ == "__main__":
    main()
