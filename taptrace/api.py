"""REST API (PDF section 5).

POST /v1/troubleshoot   {"query": str, "siis_response": str | {"title","content"} | null}
                        -> {"query","query_variations","response":{"contexts":[...]},"meta":{...}}
                        ?explain=true adds a "trace" object (provenance spans, gate verdict, resolver
                        candidates, cache decision). Default responses are pure contract JSON.
GET  /health            -> 200 {"status":"ok"} only once catalog, embeddings, cache and LLM client are
                           initialised; 503 {"status":"starting"} before that.
GET  /v1/metrics        -> runtime counters (requests, hit rate, cumulative cost, cache size).
GET  /v1/catalog/noise-report -> what the Catalog Compiler found in deeplinks.json.
GET  /                  -> interactive demo (agent console + phone emulator), static, same container.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Engine

ROOT = Path(__file__).resolve().parents[1]


class SIISDoc(BaseModel):
    title: Optional[str] = ""
    content: str


class TroubleshootRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    siis_response: Optional[Union[str, SIISDoc]] = None


app = FastAPI(title="TapTrace - Smart Guided Troubleshooting Engine", version="1.0.0",
              default_response_class=ORJSONResponse)
# The demo page may also be opened from a local dev server (e.g. VS Code Live Server) or as a file;
# allow only local origins to call the API in that case.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|null)$",
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
_engine: Optional[Engine] = None
_ready = threading.Event()


def _boot() -> None:
    global _engine
    _engine = Engine(use_llm=os.getenv("TAPTRACE_OFFLINE") != "1")
    _ready.set()


@app.on_event("startup")
def startup() -> None:
    # Load synchronously: the container is not "healthy" until everything is initialised (PDF 5: /health).
    _boot()


@app.get("/health")
def health():
    if not _ready.is_set() or _engine is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    return {"status": "ok"}


@app.post("/v1/troubleshoot")
def troubleshoot(req: TroubleshootRequest, explain: bool = Query(False), no_cache: bool = Query(False)):
    """no_cache=true forces the cold path (and does not write the cache) - used to demo/benchmark the pipeline."""
    if not _ready.is_set():
        return JSONResponse({"error": "engine starting"}, status_code=503)
    q = req.query.strip()
    if not q:
        return JSONResponse({"error": "query must not be blank"}, status_code=400)
    siis = req.siis_response.model_dump() if isinstance(req.siis_response, SIISDoc) else req.siis_response
    return _engine.troubleshoot(q, siis, explain=explain, use_cache=not no_cache, write_cache=not no_cache)


@app.get("/v1/metrics")
def metrics():
    e = _engine
    s = dict(e.stats)
    s["hit_rate"] = round(s["hits"] / s["requests"], 4) if s["requests"] else 0.0
    s["cache_entries"] = len(e.cache)
    s["resolver_memo_hits"] = e.resolver.memo_hits
    s["startup_ms"] = e.startup_ms
    s["model"] = e.llm.label
    return s


@app.get("/v1/catalog/noise-report")
def noise_report():
    return _engine.catalog.noise_report()


@app.get("/v1/demo/scenarios")
def demo_scenarios():
    """The 20 provided complaints paired with their SIIS records (for the demo console)."""
    import json as _json

    siis = _json.loads((ROOT / "data" / "siis_responses.json").read_text(encoding="utf-8"))["responses"]
    qs = [l.strip() for l in (ROOT / "data" / "input.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [{"line": i, "query": q, "siis_id": r["id"], "siis_response": r["siis_response"]}
            for i, (q, r) in enumerate(zip(qs, siis), 1)]


class ParseRequest(BaseModel):
    siis_response: Union[str, SIISDoc]


@app.post("/v1/demo/parse")
def demo_parse(req: ParseRequest):
    """SIIS document as the engine sees it: sections, sentence span ids, corrupt/leak flags."""
    from .siis import parse_siis

    siis = req.siis_response.model_dump() if isinstance(req.siis_response, SIISDoc) else req.siis_response
    d = parse_siis(siis)
    return {"title": d.title, "sections": [{"idx": s.idx, "heading": s.clean_heading,
                                            "sentences": [{"sid": x.sid, "text": x.text, "corrupt": x.corrupt, "leak": x.leak}
                                                          for x in s.sentences]} for s in d.sections]}


_demo = ROOT / "demo"
if _demo.exists():
    app.mount("/demo", StaticFiles(directory=str(_demo)), name="demo")

    @app.get("/")
    def index():
        return FileResponse(str(_demo / "index.html"))
