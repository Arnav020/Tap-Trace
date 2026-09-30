"""Plan composer: grounded units -> schema-valid Goal(s).

* Deeplinks are copied from catalog objects verbatim (uri, description, message, originalType), exactly
  like data/sample_output.json does; validation probes are copied verbatim from the catalog row.
* category is computed by rules (never guessed by an LLM); manual => no deeplink (PDF 4.1).
* Sequencing = stable sort by a disruption cost: auto settings -> manual checks -> escalation ->
  critical (restart < force restart < safe mode < update < factory reset). PDF: critical always last.
* score is COMPUTED: 0.45 * knowledge-fit + 0.35 * grounding + 0.20 * mapping confidence.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .catalog import DUMMY_URI, Catalog, Row
from .fields import (action_name, clean_step, description_for, dummy_texts, fit_description, goal_text,
                     topic_for, valid_step, validate_goal_obj)
from .resolver import Resolution, Resolver
from .text import sentence_case, title_case

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("contract", ROOT / "data" / "schema.py")
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)  # the organisers' schema.py, byte-for-byte unchanged

CRIT_COST = [(re.compile(r"factory (data )?reset|delete all", re.I), 6.0), (re.compile(r"software update|firmware", re.I), 5.0),
             (re.compile(r"safe mode", re.I), 4.5), (re.compile(r"force|volume down", re.I), 4.1),
             (re.compile(r"restart|reboot", re.I), 4.0)]


@dataclass
class BuiltAction:
    uid: str
    name: str
    description: str
    category: str
    step_groups: List[dict]
    cost: float
    order: int
    mapping_conf: float
    trace: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"actionName": self.name, "description": self.description, "stepGroups": self.step_groups,
                "category": self.category}


def deeplink_obj(row: Row) -> dict:
    return {"deeplink": row.uri, "description": row.description, "message": row.message,
            "originalType": row.original_type}


def validation_obj(row: Row) -> Optional[dict]:
    v = row.validation
    if not v or not v.get("deeplink"):
        return None
    out = {"deeplink": v["deeplink"], "key": v["key"]}
    for k in ("resultType", "condition", "value"):
        if v.get(k) is not None:
            out[k] = v[k]
    return out


def _cost(category: str, text: str, escalation: bool, order: int) -> float:
    if category == "auto":
        return 1.0
    if category == "manual":
        return 3.0 if escalation else 2.0
    for rx, c in CRIT_COST:
        if rx.search(text):
            return c
    return 4.2


class Composer:
    def __init__(self, catalog: Catalog, resolver: Resolver):
        self.cat = catalog
        self.res = resolver

    def resolve_units(self, units) -> Dict[str, Resolution]:
        out = {}
        for u in units:
            if u.kind == "nav":
                out[u.uid] = self.res.resolve_chain(u.targets, u.text, u.polarity, u.category == "critical")
            elif re.match(r"^back up\b", u.steps[0], re.I):
                r = self.res.resolve_latent(u.steps[0])
                if r is not None:
                    out[u.uid] = r
        return out

    def build_action(self, u, res: Optional[Resolution], order: int, llm_a: Optional[dict]) -> Optional[BuiltAction]:
        drops = set((llm_a or {}).get("drop_steps") or []) if u.kind != "nav" else set()
        steps = [clean_step(s) for i, s in enumerate(u.steps) if i not in drops]
        steps = [s for s in steps if valid_step(s)]
        if not steps:
            return None
        category = u.category
        if res is not None and res.method == "latent-label":
            category = "auto"
        groups: List[dict] = []
        trace = {"unit": u.uid, "spans": u.spans, "kind": u.kind, "resolution": None}
        conf = 1.0
        label = ""
        variant = ""
        grouped = bool(u.__dict__.get("groups"))
        if category == "manual" or res is None:
            groups.append({"steps": steps, "actionableDeeplink": None, "validationDeeplink": None})
            if category == "auto":  # nav chain without resolution cannot happen; defensive
                category = "manual"
        else:
            conf = res.confidence
            trace["resolution"] = {"method": res.method, "uri": res.uri, "confidence": res.confidence,
                                   "reason": res.reason, "candidates": res.candidates}
            if res.is_dummy:
                desc, msg = dummy_texts(res.screen_name)
                dl = {"deeplink": DUMMY_URI, "description": desc, "message": msg, "originalType": "placeholder"}
                groups.append({"steps": steps, "actionableDeeplink": dl, "validationDeeplink": None})
                label = res.screen_name
            elif grouped and res.row is not None:
                label = res.row.label
                for g in u.__dict__["groups"]:
                    row = self.cat.pick_variant(res.row, g["polarity"])
                    lead = []
                    if g.get("condition"):
                        verb = {"on": "turn on", "off": "turn off"}.get(g["polarity"], "adjust")
                        lead = [clean_step(f"{g['condition']}, {verb} {row.label}")]
                    gsteps = [clean_step(s) for s in lead + g["steps"]]
                    groups.append({"steps": [s for s in gsteps if valid_step(s)], "actionableDeeplink": deeplink_obj(row),
                                   "validationDeeplink": validation_obj(row)})
                variant = "grouped"
            else:
                row = res.row
                label, variant = row.label, row.variant
                groups.append({"steps": steps, "actionableDeeplink": deeplink_obj(row),
                               "validationDeeplink": validation_obj(row)})
        heading = "" if u.__dict__.get("backup") else u.heading
        text = " ".join(steps) + " " + heading
        name = (llm_a or {}).get("name") or action_name(u.kind, category, heading, text, label=label, variant=variant,
                                                        grouped=grouped, is_dummy=bool(res and res.is_dummy),
                                                        escalation=u.__dict__.get("escalation", False))
        name = align_name_polarity(name, variant)
        # escalation units inherit a section heading that describes the PREVIOUS remedy -> describe the steps only
        desc_text = " ".join(steps) if u.__dict__.get("escalation") else text
        desc, dsrc = fit_description((llm_a or {}).get("description"), desc_text)
        trace["description_source"] = dsrc
        return BuiltAction(u.uid, name, desc, category, groups, _cost(category, text, u.__dict__.get("escalation", False), order),
                           order, conf, trace)

    def compose(self, units, resolutions, cf, doc, fit_score: float, struct: Optional[dict]) -> List[dict]:
        llm_actions = (struct or {}).get("actions", {})
        goals_spec = [dict(g) for g in (struct or {}).get("goals", []) if g["units"]] or [{"units": [u.uid for u in units]}]
        by_uid = {u.uid: u for u in units}
        # The knowledge article is the authority on WHAT helps; the LLM may only prune physical/manual
        # candidates it judges off-topic. Candidates it forgot to mention are kept (first goal).
        mentioned = {uid for g in goals_spec for uid in g["units"]}
        goals_spec[0]["units"] = list(goals_spec[0]["units"]) + [u.uid for u in units if u.uid not in mentioned]
        goals = []
        used = set()
        for gs in goals_spec:
            built: List[BuiltAction] = []
            for order, uid in enumerate(gs["units"]):
                if uid in used or uid not in by_uid:
                    continue
                la = llm_actions.get(uid)
                u0 = by_uid[uid]
                protected = (u0.kind == "nav" or u0.category == "critical" or u0.__dict__.get("backup")
                             or u0.__dict__.get("escalation"))  # "contact support if it persists" always applies
                if la is not None and not la.get("keep", True) and not protected:
                    continue
                used.add(uid)
                a = self.build_action(by_uid[uid], resolutions.get(uid), order, la)
                if a is not None:
                    built.append(a)
            if not built:
                continue
            built = _merge_same_name(built)
            built.sort(key=lambda a: (a.cost, a.order))
            topic, title = topic_for(cf, doc.title_frame.contexts)
            if gs.get("topic"):
                topic = gs["topic"]
                if 2 <= len(topic.split()) <= 3:  # keep title consistent with the LLM's (validated) topic
                    title = sentence_case(topic)
            title = gs.get("title") or title
            mapping = [a.mapping_conf for a in built if a.category != "manual"] or [1.0]
            score = round(min(0.99, 0.45 * fit_score + 0.35 * 1.0 + 0.20 * (sum(mapping) / len(mapping))), 2)
            goal = {"goal": goal_text(topic, bool(gs.get("config_request", cf.config))), "title": title,
                    "actions": [a.to_dict() for a in built], "score": float(score)}
            goal["_trace"] = [a.trace for a in built]
            goals.append(goal)
        return goals


_NAME_ON = re.compile(r"^(Enable|Activate|Turn On|Switch On)\b")
_NAME_OFF = re.compile(r"^(Disable|Deactivate|Turn Off|Switch Off)\b")
_FLIP = {"Enable": "Disable", "Activate": "Deactivate", "Turn On": "Turn Off", "Switch On": "Switch Off"}


def align_name_polarity(name: str, variant: str) -> str:
    """The LLM names actions, but the deeplink variant is decided by code from the SIIS text. Never let the name
    contradict the tap ("Enable Adaptive Brightness" on the disable deeplink)."""
    on, off = _NAME_ON.match(name), _NAME_OFF.match(name)
    if variant == "off" and on:
        return _FLIP[on.group(1)] + name[on.end():]
    if variant == "on" and off:
        back = {v: k for k, v in _FLIP.items()}
        return back[off.group(1)] + name[off.end():]
    if variant == "grouped" and (on or off):  # one screen, an ON option and an OFF option
        return "Adjust" + name[(on or off).end():]
    return name


def _merge_same_name(actions: List[BuiltAction]) -> List[BuiltAction]:
    out: Dict[str, BuiltAction] = {}
    for a in actions:
        if a.name in out and a.category == out[a.name].category and not a.step_groups[0].get("actionableDeeplink") \
                and not out[a.name].step_groups[0].get("actionableDeeplink"):
            tgt = out[a.name].step_groups[0]["steps"]
            tgt += [s for s in a.step_groups[0]["steps"] if s not in tgt]
            continue
        if a.name in out:
            a.name = title_case(f"{a.name} Again") if a.name + " Again" not in out else a.name + " II"
        out[a.name] = a
    return list(out.values())


def to_contract(goals: List[dict]) -> dict:
    """Validate with the organisers' pydantic models; returns the JSON-ready ContextDeeplinkResponse."""
    public = [{k: v for k, v in g.items() if not k.startswith("_")} for g in goals]
    model = contract.ContextDeeplinkResponse(contexts=public)
    data = model.model_dump(mode="json", exclude_none=True)
    for g in data["contexts"]:
        for a in g["actions"]:
            for sg in a["stepGroups"]:
                sg.setdefault("actionableDeeplink", None)
                sg.setdefault("validationDeeplink", None)
    return data


def rule_errors(goals: List[dict]) -> List[str]:
    errs = []
    for g in goals:
        errs += validate_goal_obj({k: v for k, v in g.items() if not k.startswith("_")})
    return errs
