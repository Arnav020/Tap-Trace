"""Provider-agnostic LLM adapter (any OpenAI-compatible endpoint: Groq, Gemini, OpenAI, OpenRouter, Ollama).

* Keys come ONLY from the environment / a local .env file (never hard-coded, never logged).
* JSON-object mode + temperature 0 + fixed seed for maximal determinism.
* Every call returns token usage; cost = prompt_tokens x in_rate + completion_tokens x out_rate
  (PDF Appendix C cost derivation). Default rates are Groq's published list prices for
  openai/gpt-oss-120b ($0.15 / $0.60 per 1M tokens, verified 2026-09-30); override with env vars.
* Rate limits (free tiers) are handled with a bounded Retry-After wait, then a model fallback chain:
  when the primary model is rate-limited (e.g. Groq free-tier daily cap), the same call goes to a fallback
  model on the same provider (openai/gpt-oss-20b, $0.075 / $0.30 per 1M, verified 2026-09-30) and the
  primary is skipped until its Retry-After passes. Only if every model fails does the engine fall back to
  its deterministic path. Every Usage records which model actually answered and is costed at its own rate.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

PRESETS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "key": "GROQ_API_KEY",
             "in": 0.15, "out": 0.60, "fallback": ("openai/gpt-oss-20b", 0.075, 0.30)},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "model": "gemini-2.5-flash",
               "key": "GEMINI_API_KEY", "in": 0.0, "out": 0.0},
    "openai": {"base_url": None, "model": "gpt-4o-mini", "key": "OPENAI_API_KEY", "in": 0.15, "out": 0.60},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-oss-120b",
                   "key": "OPENROUTER_API_KEY", "in": 0.0, "out": 0.0},
}


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*\"?([^\"\n]*)\"?\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip()


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    ok: bool = False
    error: str = ""
    model: str = ""  # provider/model that actually answered ("" if none)

    def __add__(self, o: "Usage") -> "Usage":
        models = [m for m in dict.fromkeys((self.model, o.model)) if m]
        return Usage(self.prompt_tokens + o.prompt_tokens, self.completion_tokens + o.completion_tokens,
                     round(self.cost_usd + o.cost_usd, 8), max(self.latency_ms, o.latency_ms), self.ok or o.ok,
                     self.error or o.error, " + ".join(models))


class LLM:
    def __init__(self, model: Optional[str] = None):
        load_dotenv()
        provider = os.getenv("TAPTRACE_LLM_PROVIDER", "").lower()
        if not provider:
            provider = next((p for p, c in PRESETS.items() if os.getenv(c["key"])), "none")
        self.provider = provider
        self.available = False
        self.model = "deterministic"
        if provider == "none" or provider not in PRESETS or os.getenv("TAPTRACE_OFFLINE") == "1":
            return
        cfg = PRESETS[provider]
        key = os.getenv(cfg["key"])
        if not key:
            return
        from openai import OpenAI

        self.model = model or os.getenv("TAPTRACE_LLM_MODEL", cfg["model"])
        self.rate_in = float(os.getenv("TAPTRACE_PRICE_IN_PER_M", cfg["in"]))
        self.rate_out = float(os.getenv("TAPTRACE_PRICE_OUT_PER_M", cfg["out"]))
        self.client = OpenAI(api_key=key, base_url=cfg["base_url"], max_retries=0)
        self.available = True
        # fallback chain: (model, in_rate, out_rate); TAPTRACE_LLM_FALLBACK_MODEL="" disables it
        fb = cfg.get("fallback")
        fb_model = os.getenv("TAPTRACE_LLM_FALLBACK_MODEL", fb[0] if fb else "")
        self.chain = [(self.model, self.rate_in, self.rate_out)]
        if fb_model and fb_model != self.model:
            same = fb and fb_model == fb[0]
            self.chain.append((fb_model, fb[1] if same else self.rate_in, fb[2] if same else self.rate_out))
        self._cooldown_until = {}  # model -> time.time() until which it is rate-limited

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}" if self.available else "deterministic (no LLM)"

    def json_call(self, system: str, user: str, max_tokens: int = 1200, timeout: float = 7.0,
                  wait_budget: float = 0.0) -> Tuple[Optional[dict], Usage]:
        """One JSON-mode completion. `wait_budget` seconds may be spent honouring 429 Retry-After."""
        if not self.available:
            return None, Usage(error="llm-unavailable")
        t0 = time.perf_counter()
        last_err = ""
        live = [c for c in self.chain if self._cooldown_until.get(c[0], 0) <= time.time()] or self.chain[:1]
        for i, (model, rate_in, rate_out) in enumerate(live):
            is_last = i == len(live) - 1
            data, usage, last_err, retry_after = self._call(model, rate_in, rate_out, system, user, max_tokens, timeout,
                                                            wait_budget if is_last else 0.0, t0)
            if usage.ok:
                return data, usage
            if retry_after is not None:  # rate-limited: skip this model until its Retry-After passes
                self._cooldown_until[model] = time.time() + retry_after
            elif "Timeout" in last_err:
                break  # the latency budget is spent; do not start a second model
        return None, Usage(latency_ms=int((time.perf_counter() - t0) * 1000), error=last_err)

    def _call(self, model, rate_in, rate_out, system, user, max_tokens, timeout, wait_budget, t0):
        extra = {}
        if "gpt-oss" in model:
            extra["reasoning_effort"] = "low"
        spent = 0.0
        last_err = ""
        retry_after = None
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0,
                    seed=7,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    timeout=timeout,
                    extra_body=extra or None,
                )
                u = resp.usage
                pt, ct = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
                usage = Usage(pt, ct, round(pt * rate_in / 1e6 + ct * rate_out / 1e6, 8),
                              int((time.perf_counter() - t0) * 1000), True, "", f"{self.provider}/{model}")
                text = (resp.choices[0].message.content or "").strip()
                return _parse_json(text), usage, "", None
            except Exception as e:  # noqa: BLE001 - any provider error degrades to the next model / deterministic
                last_err = type(e).__name__
                retry_after = _retry_after(e)
                if retry_after is not None and spent + retry_after <= wait_budget:
                    time.sleep(retry_after)
                    spent += retry_after
                    continue
                if attempt == 0 and "Timeout" not in last_err and "RateLimit" not in last_err and "Authentication" not in last_err:
                    continue
                break
        return None, Usage(error=last_err), last_err, retry_after


def _retry_after(e: Exception) -> Optional[float]:
    if "RateLimit" not in type(e).__name__:
        return None
    try:
        h = e.response.headers  # type: ignore[attr-defined]
        v = h.get("retry-after")
        if v:
            return min(float(v) + 0.5, 90.0)
    except Exception:  # noqa: BLE001
        pass
    return 8.0


def _parse_json(text: str) -> Optional[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None
