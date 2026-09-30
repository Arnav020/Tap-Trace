"""End-to-end stress test against the real HTTP server (PDF section 8, Phase 4).

Starts uvicorn in a subprocess (deterministic mode so no LLM quota is spent), measures cold start
(process launch -> /health 200), then fires concurrent requests (exact repeats, unseen paraphrases,
no-SIIS misses) and validates every response body against the organisers' schema.
Writes artifacts/stress.json (read by scripts/evaluate.py).

    python scripts/stress_test.py [--requests 400] [--concurrency 16]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from taptrace.plan import contract  # noqa: E402

PORT = 8765


def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/troubleshoot", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
        status = r.status
    return status, data, (time.perf_counter() - t0) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=400)
    ap.add_argument("--concurrency", type=int, default=16)
    args = ap.parse_args()
    tmp = ROOT / "artifacts" / "scratch_stress.sqlite"
    shutil.copy(ROOT / "artifacts" / "cache.sqlite", tmp)
    env = dict(os.environ, TAPTRACE_OFFLINE="1", TAPTRACE_CACHE_PATH=str(tmp), PYTHONIOENCODING="utf-8")
    t0 = time.perf_counter()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "taptrace.api:app", "--port", str(PORT), "--log-level", "warning"],
                            cwd=str(ROOT), env=env)
    try:
        cold_start = None
        while time.perf_counter() - t0 < 60:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as r:
                    if r.status == 200:
                        cold_start = time.perf_counter() - t0
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        results = [json.loads(l) for l in (ROOT / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        para = [x["text"] for x in json.loads((ROOT / "eval" / "heldout_paraphrases.json").read_text(encoding="utf-8"))["items"]]
        mix = []
        for k in range(args.requests):
            if k % 3 == 0:
                mix.append(("exact", {"query": results[k % 20]["query"]}))
            elif k % 3 == 1:
                mix.append(("paraphrase", {"query": para[k % len(para)]}))
            else:
                mix.append(("unknown", {"query": f"my smartwatch strap number {k} broke"}))
        lat = {"exact": [], "paraphrase": [], "unknown": []}
        errors = schema_bad = hits = 0
        t1 = time.perf_counter()
        with cf.ThreadPoolExecutor(args.concurrency) as pool:
            futs = {pool.submit(post, body): kind for kind, body in mix}
            for f in cf.as_completed(futs):
                kind = futs[f]
                try:
                    status, data, ms = f.result()
                    lat[kind].append(ms)
                    hits += bool(data["meta"]["cache_hit"])
                    try:
                        contract.ContextDeeplinkResponse(**data["response"])
                    except Exception:  # noqa: BLE001
                        schema_bad += 1
                    if status != 200:
                        errors += 1
                except Exception:  # noqa: BLE001
                    errors += 1
        wall = time.perf_counter() - t1
        pc = lambda xs, q: round(float(np.percentile(xs, q)), 1) if xs else None  # noqa: E731
        out = {"requests": args.requests, "concurrency": args.concurrency, "cold_start_s": round(cold_start or -1, 2),
               "throughput_rps": round(args.requests / wall, 1), "errors": errors, "schema_invalid": schema_bad, "cache_hits": hits,
               "latency_ms": {k: {"p50": pc(v, 50), "p95": pc(v, 95), "n": len(v)} for k, v in lat.items()}}
        (ROOT / "artifacts" / "stress.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(out, indent=2))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
