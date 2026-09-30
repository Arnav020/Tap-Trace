"""Automated gates for every non-negotiable in the Theme-2 PDF (section 4) plus our own guarantees.

Run:  pytest -q        (deterministic mode, no API key and no network needed)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

os.environ["TAPTRACE_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parents[1]

from taptrace.cache import SemanticCache, calibrate  # noqa: E402
from taptrace.catalog import DUMMY_URI  # noqa: E402
from taptrace.engine import Engine  # noqa: E402
from taptrace.facets import compatible, frame  # noqa: E402
from taptrace.fields import (DESC_RULES, DESC_DEFAULT, LEAK_RX, TOPICS, dummy_texts, goal_text, scrub, valid_description,  # noqa: E402
                             valid_goal, valid_title, validate_goal_obj)
from taptrace.plan import contract  # noqa: E402
from taptrace.siis import CRITICAL_RX, _polarity, _targets, sentence_steps  # noqa: E402

J = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))  # noqa: E731
SIIS = J("data/siis_responses.json")["responses"]
QUERIES = [l.strip() for l in (ROOT / "data/input.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
GOLD = J("eval/gold.json")["scenarios"]


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    return Engine(use_llm=False, cache_path=tmp_path_factory.mktemp("c") / "cache.sqlite")


# ----------------------------------------------------------------------------- provided data untouched
def test_provided_files_are_byte_identical():
    expected = {
        "data/schema.py": "833e54d26e329c44f897173cdcb37e0421bb66d5c8b44bcebf390141bf069406",
        "data/deeplinks.json": "5b49ec7c66b18080c28eb1bdc0c76b6c2050a9dd05ae8c73befea1e608297922",
        "data/siis_responses.json": "5513880c11d358b13ca25722385ea0043772d85f7132f5f244906b3d411119b6",
        "data/input.txt": "0b025b78102e97b2aa5c0b39e1bf76789b872452d7a89279806f05df1cf29c37",
    }
    for p, h in expected.items():
        assert hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h, p


# ----------------------------------------------------------------------------- field rules by construction
def test_every_description_template_is_valid():
    for _, d in DESC_RULES:
        assert valid_description(d), d
    assert valid_description(DESC_DEFAULT)


def test_every_topic_title_and_goal_is_valid():
    for _, topic, title in TOPICS:
        assert valid_title(title), title
        assert valid_goal(goal_text(topic, False)) and valid_goal(goal_text(topic, True))


@pytest.mark.parametrize("screen", ["email app Storage", "Factory data reset", "Safe mode", "Software update", "X"])
def test_dummy_texts_are_5_to_7_words(screen):
    desc, msg = dummy_texts(screen)
    assert 5 <= len(desc.split()) <= 7 and 5 <= len(msg.split()) <= 7


def test_leak_scrubber_catches_siis_leaks():
    for bad in ["visit https://x.com/help", "go to www.techcorp.com", "mail kidshome.pin@TechCorp.com now",
                "see [guide](http://a.b)", "open techcorp.com/support"]:
        assert LEAK_RX.search(bad) and not LEAK_RX.search(scrub(bad)), bad


# ----------------------------------------------------------------------------- catalog audit
def test_catalog_noise_findings(eng):
    rep = eng.catalog.noise_report()
    assert len(rep["tv_distractors"]) + len(rep["iot_distractors"]) == 21
    assert len(rep["corrupt_rows"]) == 2 and len(rep["exact_duplicates"]) == 6
    assert any(m[0] == "DL-0162" for m in rep["misleading_messages"])  # "Disable Grayscale" is mono audio


# ----------------------------------------------------------------------------- deeplink resolution
def test_mapping_benchmark_all_domains_and_traps(eng):
    cases = J("eval/mapping_benchmark.json")["cases"]
    ok = 0
    for c in cases:
        steps, _ = sentence_steps(c["text"])
        if "Navigate to and open Settings." not in steps:
            got = None
        else:
            r = eng.resolver.resolve_chain(_targets(steps), " ".join(steps), _polarity(c["text"]),
                                           bool(CRITICAL_RX.search(" ".join(steps))))
            got = "DUMMY" if r.is_dummy else r.row.id
        ok += got == c["expect"]
    assert ok == len(cases)


def test_factory_reset_never_maps_to_auto_factory_reset_trap(eng):
    r = eng.resolver.resolve_chain(["General management", "Reset", "Factory data reset"], "Tap Factory data reset.", "open", True)
    assert r.is_dummy and r.uri == DUMMY_URI


# ----------------------------------------------------------------------------- engine end-to-end (deterministic)
def test_all_20_scenarios_contract_rules_and_gate(eng):
    by_line = {g["line"]: g for g in GOLD}
    for i, (q, rec) in enumerate(zip(QUERIES, SIIS), 1):
        out = eng.troubleshoot(q, rec["siis_response"], use_cache=False, write_cache=False)
        contract.ContextDeeplinkResponse(**out["response"])
        assert set(out) == {"query", "query_variations", "response", "meta"}
        assert 8 <= len(out["query_variations"]) <= 10
        blob = json.dumps(out, ensure_ascii=False)
        assert not LEAK_RX.search(json.dumps(out["response"])), i
        for g in out["response"]["contexts"]:
            assert validate_goal_obj(g) == [], (i, validate_goal_obj(g))
            for a in g["actions"]:
                for sg in a["stepGroups"]:
                    dl = sg["actionableDeeplink"]
                    if dl:
                        assert dl["deeplink"] == DUMMY_URI or dl["deeplink"] in eng.catalog.all_uris
                    if a["category"] == "manual":
                        assert dl is None
        gold_nm = by_line[i]["fit"] == "no_match"
        assert (out["meta"]["fallback"] == "no_match") == gold_nm, (i, out["meta"])
        assert "bixby://" not in blob


def test_touch_sensitivity_conditional_polarity(eng):
    out = eng.troubleshoot(QUERIES[18], SIIS[18]["siis_response"], use_cache=False, write_cache=False)
    uris = {sg["actionableDeeplink"]["deeplink"] for g in out["response"]["contexts"] for a in g["actions"]
            for sg in a["stepGroups"] if sg["actionableDeeplink"]}
    rows = {r.id: r.uri for r in eng.catalog.rows}
    assert rows["DL-0125"] in uris and rows["DL-0126"] in uris and rows["DL-0169"] in uris and rows["DL-0542"] in uris


def test_critical_last_and_backup_before_reset(eng):
    out = eng.troubleshoot(QUERIES[18], SIIS[18]["siis_response"], use_cache=False, write_cache=False)
    acts = out["response"]["contexts"][0]["actions"]
    cats = [a["category"] for a in acts]
    assert cats[-1] == "critical" and all(c == "critical" for c in cats[cats.index("critical"):])
    names = [a["actionName"] for a in acts]
    assert names.index("Back Up Phone Data") < max(i for i, n in enumerate(names) if "Factory" in n)


def test_no_siis_and_cache_miss_is_honest_fallback(eng):
    out = eng.troubleshoot("my smartwatch strap broke", None)
    assert out["response"] == {"contexts": []} and out["meta"]["fallback"] == "no_siis_context"


def test_cache_exact_semantic_and_determinism(eng):
    q, s = QUERIES[19], SIIS[19]["siis_response"]
    cold = eng.troubleshoot(q, s)
    hit = eng.troubleshoot(q, None)
    assert hit["meta"]["cache_hit"] and hit["meta"]["cache_match"] == "exact"
    assert hit["response"] == cold["response"] and hit["meta"]["cost_usd"] == 0.0
    para = eng.troubleshoot("x1 ultra display won't come on but the phone still rings, no damage", None)
    assert para["meta"]["cache_hit"] and para["response"] == cold["response"]
    neg = eng.troubleshoot("My Nexa X1 Ultra screen won't turn off even when I press the power button", None)
    assert not neg["meta"]["cache_hit"]


def test_facet_guard_blocks_polarity_and_screen_part_conflicts():
    a = frame("My phone screen won't turn on")
    b = frame("My phone screen won't turn off")
    assert not compatible(a, b)[0]
    assert not compatible(frame("Fold inner screen is black"), frame("Fold cover screen is black"))[0]


def test_calibration_threshold_bounds(eng):
    A = eng.embedder.encode(["screen black", "black display", "display is dark", "screen went black"])
    N = eng.embedder.encode(["battery drains fast"])
    tau, stats = calibrate(A, N)
    assert 0.66 <= tau <= 0.90 and stats["n_neg"] == 1


# ----------------------------------------------------------------------------- API contract
def test_api_contract(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("TAPTRACE_CACHE_PATH", str(tmp_path / "api_cache.sqlite"))
    import taptrace.api as api

    with TestClient(api.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        r = client.post("/v1/troubleshoot", json={"query": QUERIES[20 - 1], "siis_response": SIIS[19]["siis_response"]})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"query", "query_variations", "response", "meta"}
        contract.ContextDeeplinkResponse(**body["response"])
        assert not r.text.lstrip().startswith("```")
        r2 = client.post("/v1/troubleshoot", json={"query": QUERIES[19], "siis_response": SIIS[19]["siis_response"]["content"]})
        assert r2.status_code == 200
        assert client.post("/v1/troubleshoot", json={"query": ""}).status_code == 422
        assert client.post("/v1/troubleshoot", json={"query": "   "}).status_code == 400
        assert "hit_rate" in client.get("/v1/metrics").json()


# ----------------------------------------------------------------------------- unseen-domain regressions
# Found by a 30 Sep audit with hand-written Battery / Camera / Wi-Fi articles the engine was never tuned on.
def _doc(title, body):
    return {"title": title, "content": f"Smartphone Mobile {title} ( Smartphone Mobile): {body}"}


CAMERA_DOC = _doc("Camera app crashes or shows Camera failed", """# Camera Not Working
If the Camera app closes suddenly or shows a "Camera failed" message, please try the following steps.
## 1. Allow camera access
Go to Settings, tap Security and privacy, tap Privacy, and then tap Camera access. Make sure camera access is turned on.
## 2. Restart the phone in Safe mode
Press and hold the Side key, touch and hold Power off, and then tap Safe mode.
## 3. Reset the phone
If the problem continues, back up your data and perform a factory data reset: go to Settings, tap General management, tap Reset, and then tap Factory data reset.""")

WIFI_DOC = _doc("Wi-Fi keeps disconnecting", """# Wi-Fi Disconnects Frequently
If your phone keeps dropping its Wi-Fi connection, try these steps.
## 1. Turn Wi-Fi off and on again
Go to Settings, tap Connections, and then tap the switch next to Wi-Fi to turn it off. Wait a few seconds and turn it on again.
## 2. Reset network settings
Go to Settings, tap General management, tap Reset, and then tap Reset network settings.""")


def _actions(out):
    return [a for g in out["response"]["contexts"] for a in g["actions"]]


def test_unseen_factory_reset_is_critical_and_last(eng):
    out = eng.troubleshoot("Camera app keeps crashing and says camera failed", CAMERA_DOC, use_cache=False, write_cache=False)
    acts = _actions(out)
    reset = [a for a in acts if re.search(r"factory", " ".join(s for sg in a["stepGroups"] for s in sg["steps"]), re.I)]
    assert reset and all(a["category"] == "critical" for a in reset)
    assert acts[-1] is reset[-1]  # critical block ends with the most destructive action


def test_article_preamble_never_becomes_an_action(eng):
    for q, doc in (("Camera app keeps crashing and says camera failed", CAMERA_DOC), ("my wifi keeps disconnecting", WIFI_DOC)):
        steps = [s for a in _actions(eng.troubleshoot(q, doc, use_cache=False, write_cache=False)) for sg in a["stepGroups"] for s in sg["steps"]]
        assert not any(re.search(r"following steps|these steps|some steps", s, re.I) for s in steps), steps


def test_same_screen_followup_stays_one_action(eng):
    acts = _actions(eng.troubleshoot("my wifi keeps disconnecting", WIFI_DOC, use_cache=False, write_cache=False))
    wifi = [a for a in acts if any(re.search(r"\bWi-Fi\b", s) for sg in a["stepGroups"] for s in sg["steps"])
            and not any("network settings" in s for sg in a["stepGroups"] for s in sg["steps"])]
    assert len(wifi) == 1, [a["actionName"] for a in acts]  # off + "turn it on again" = one screen
    dl = wifi[0]["stepGroups"][0]["actionableDeeplink"]
    assert dl and dl["originalType"] == "onClickURL"  # off-then-on opens the screen; a fixed on/off probe would contradict
    assert any(a["category"] == "critical" and any("Reset network settings" in s for s in a["stepGroups"][0]["steps"]) for a in acts)


def test_action_name_never_contradicts_deeplink_polarity():
    from taptrace.plan import align_name_polarity

    assert align_name_polarity("Enable Adaptive Brightness", "off") == "Disable Adaptive Brightness"
    assert align_name_polarity("Turn On Power Saving", "off") == "Turn Off Power Saving"
    assert align_name_polarity("Disable Data Saver", "on") == "Enable Data Saver"
    assert align_name_polarity("Enable Touch Sensitivity", "grouped") == "Adjust Touch Sensitivity"
    assert align_name_polarity("Configure Wi-Fi", "open") == "Configure Wi-Fi"


def test_multi_action_sentence_becomes_atomic_steps():
    steps, _ = sentence_steps("Try forcing a restart by pressing and holding the Power and Volume down buttons for 20 seconds, "
                              "and then charging the device for at least one hour.")
    assert steps == ["Force a restart by pressing and holding the Power and Volume down buttons for 20 seconds.",
                     "Charge the device for at least one hour."]


def test_fallback_variations_never_invent_a_component(eng):
    out = eng.troubleshoot("my smartwatch strap broke", None)
    assert out["meta"]["fallback"] == "no_siis_context" and 8 <= len(out["query_variations"]) <= 10
    assert not any(re.search(r"display issue|unspecified", v, re.I) for v in out["query_variations"])


def test_llm_falls_back_to_second_model_when_primary_is_rate_limited():
    """Groq free tier: when gpt-oss-120b hits its daily cap, the same call is answered by gpt-oss-20b, costed at
    20b's own rate, and the primary is skipped until its Retry-After passes."""
    from types import SimpleNamespace
    from taptrace.llm import LLM

    class RateLimitError(Exception):
        response = SimpleNamespace(headers={"retry-after": "30"})

    calls = []

    def create(model, **kw):
        calls.append(model)
        if model == "primary":
            raise RateLimitError("tokens per day")
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000),
                               choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

    llm = LLM.__new__(LLM)
    llm.available, llm.provider, llm.model = True, "groq", "primary"
    llm.chain = [("primary", 0.15, 0.60), ("fallback", 0.075, 0.30)]
    llm._cooldown_until = {}
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    data, usage = llm.json_call("sys", "user")
    assert data == {"ok": True} and usage.ok and usage.model == "groq/fallback"
    assert abs(usage.cost_usd - (1000 * 0.075 + 1000 * 0.30) / 1e6) < 1e-12
    calls.clear()
    llm.json_call("sys", "user")
    assert calls == ["fallback"]  # primary skipped during its cool-down
