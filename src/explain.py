"""Explanation layer: the LLM explains numbers it is GIVEN; it never computes them.

Safety net: every number in the narrative is checked against the engine's facts. If one is not grounded
the model gets one chance to fix it, then we fall back to a deterministic template.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .engine import Comparison, compare, rank_by_horizon, top_cost_drivers
from .llm import LLMClient, LLMError
from .schema import SupplierInputs

PROMPT = (__import__("pathlib").Path(__file__).resolve().parent.parent / "prompts" / "explain.md")


def format_inr(x: float, decimals: int = 0) -> str:
    neg, x = x < 0, abs(x)
    whole, _, frac = f"{x:.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return f"{'-' if neg else ''}\u20b9{whole}{'.' + frac if frac else ''}"


# ----------------------------------------------------------------------------- facts
def build_facts(comp: Comparison, suppliers: list[SupplierInputs], assumptions: list[dict]) -> dict:
    setup = comp.setup
    sens = {}
    for m in (0.5, 2.0):
        sens[f"estimates_x{m}"] = compare(setup, suppliers, comp.horizon_years, m).ranking
    return {
        "horizon_years": comp.horizon_years,
        "item": setup.item,
        "annual_volume": setup.annual_demand,
        "suppliers": [{
            "name": s.name, "rank": s.rank,
            "total_tco": round(s.total, 2), "total_tco_fmt": format_inr(s.total),
            "tco_per_unit": round(s.per_unit, 2),
            "base_price_per_unit": round(s.base_price_per_unit, 2),
            "hidden_cost_pct_over_price": round(s.hidden_cost_pct * 100, 2),
            "top_cost_drivers": [{"factor": n, "amount_fmt": format_inr(v)} for n, v in top_cost_drivers(s)],
        } for s in sorted(comp.suppliers, key=lambda r: r.rank)],
        "top_two_gap_pct": round(comp.top_two_gap_pct, 2),
        "close_call": comp.close_call,
        "ranking_best_first": comp.ranking,
        "rank_by_horizon": {str(h): names for h, names in rank_by_horizon(setup, suppliers, comp.qrs_multiplier).items()},
        "sensitivity_ranking_best_first": sens,
        "assumptions": [f"{a['supplier']}: {a['field']} = {a['value']} ({a['source']}: {a['reason']})" for a in assumptions],
    }


def _numeric_leaves(obj) -> list[float]:
    out = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _numeric_leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _numeric_leaves(v)
    elif isinstance(obj, str):
        out += [float(m.replace(",", "")) for m in _NUM.findall(obj)]
    return out


_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def ungrounded_numbers(text: str, facts: dict) -> list[str]:
    """Numbers in `text` that cannot be traced to `facts`. Small integers (0-10: ranks, years) are ignored."""
    allowed = _numeric_leaves(facts)
    bad = []
    for m in _NUM.findall(text):
        raw = m.rstrip(",").replace(",", "")
        if not raw:
            continue
        n = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if decimals == 0 and 0 <= n <= 10:
            continue
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(n - a) <= tol for a in allowed):
            bad.append(m)
    return bad


# ----------------------------------------------------------------------------- deterministic fallback
def template_narrative(facts: dict) -> str:
    sup = facts["suppliers"]
    best, last = sup[0], sup[-1]
    h = facts["horizon_years"]
    lines = [f"**Verdict**\n{best['name']} has the lowest {h}-year TCO per unit at \u20b9{best['tco_per_unit']} "
             f"(total {best['total_tco_fmt']})."]
    if facts["close_call"] and len(sup) > 1:
        lines[0] += (f" The gap to {sup[1]['name']} is only {facts['top_two_gap_pct']}%, so this is effectively a tie on cost; "
                     "decide on quality record, capacity and reliability.")
    elif len(sup) > 1:
        lines[0] += f" The next best, {sup[1]['name']}, is {facts['top_two_gap_pct']}% higher per unit."
    why = ["**Why**"]
    for s in (best, last) if best is not last else (best,):
        drivers = ", ".join(f"{d['factor']} ({d['amount_fmt']})" for d in s["top_cost_drivers"]) or "none material"
        why.append(f"- {s['name']}: hidden cost is {s['hidden_cost_pct_over_price']}% above its purchase price; top drivers: {drivers}.")
    rh = facts["rank_by_horizon"]
    winners = {h_: names[0] for h_, names in rh.items()}
    change = ("The best supplier changes with the horizon: " + "; ".join(f"{k}-year: {v}" for k, v in winners.items()) + "."
              if len(set(winners.values())) > 1 else "The best supplier is the same for every horizon from 1 to 5 years.")
    sens = facts["sensitivity_ranking_best_first"]
    sens_line = "; ".join(f"{k.replace('estimates_x', 'estimates x')}: {v[0]} first" for k, v in sens.items())
    assume = facts["assumptions"]
    confirm = ["**Confirm before deciding**"] + ([f"- {a}" for a in assume] if assume else
                                                  ["- No quote fields were assumed; check the buyer-side estimates (rejections, delays, risk)."])
    return "\n\n".join(["\n".join(lines), "\n".join(why), "**What could change this**\n" + change + " Sensitivity: " + sens_line + ".",
                        "\n".join(confirm)])


# ----------------------------------------------------------------------------- pipeline
@dataclass
class Explanation:
    text: str
    source: str                       # "llm" | "template"
    grounded: bool = True
    ungrounded: list[str] = field(default_factory=list)
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    note: str = ""


def explain(client: LLMClient, facts: dict) -> Explanation:
    if client.mock:
        return Explanation(text=template_narrative(facts), source="template", note="Mock mode: deterministic template, no LLM call.")
    system = PROMPT.read_text(encoding="utf-8")
    user = "FACTS:\n" + json.dumps(facts, ensure_ascii=False, indent=1)
    messages = [{"role": "user", "content": user}]
    tin = tout = 0
    bad: list[str] = []
    for attempt in (1, 2):
        try:
            res = client.complete(system, messages, max_tokens=700, temperature=0.0)
        except LLMError as e:
            return Explanation(text=template_narrative(facts), source="template", grounded=True,
                               note=f"LLM unavailable ({e}); showing deterministic template.")
        tin, tout = tin + res.input_tokens, tout + res.output_tokens
        bad = ungrounded_numbers(res.text, facts)
        if not bad:
            return Explanation(text=res.text.strip(), source="llm", attempts=attempt, input_tokens=tin, output_tokens=tout)
        messages = messages + [{"role": "assistant", "content": res.text},
                               {"role": "user", "content": f"These numbers are not in FACTS: {', '.join(bad)}. "
                                "Rewrite the note using only numbers from FACTS."}]
    return Explanation(text=template_narrative(facts), source="template", grounded=True, ungrounded=bad, attempts=2,
                       input_tokens=tin, output_tokens=tout,
                       note=f"LLM wrote numbers not in the engine output ({', '.join(bad)}); showing deterministic template instead.")
