"""Scoring for the extraction eval. Pure functions, no LLM calls, so the harness itself is unit-tested."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .defaults import GENERIC_BUYER_ESTIMATES, MissingInputs, build_inputs
from .engine import compare
from .extract import _norm, empty_extraction
from .schema import FIELD_BY_NAME, EvalSetup, FieldResult, QuoteExtraction

ROOT = Path(__file__).resolve().parent.parent
HEADLINE_FLAGS = ("missing", "conflict")


def load_cases(path: Path | None = None) -> list[dict]:
    path = path or ROOT / "data" / "eval" / "eval_set.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def values_match(kind: str, got: Any, want: Any) -> bool:
    if got is None:
        return False
    if kind == "bool":
        return got is want
    if kind in ("num", "frac"):
        try:
            g, w = float(got), float(want)
        except (TypeError, ValueError):
            return False
        return abs(g - w) <= 1e-9 if w == 0 else abs(g - w) <= 0.01 * abs(w)
    g, w = _norm(str(got)), _norm(str(want))
    return bool(g) and (w in g or g in w)


def score_case(case: dict, ext: QuoteExtraction) -> dict:
    exp, miss = case["expected"], case["expected_missing"]
    correct = sum(1 for f, want in exp.items()
                  if ext.fields[f].status in ("stated", "inferred") and values_match(FIELD_BY_NAME[f].kind, ext.fields[f].value, want))
    flagged = sum(1 for f in miss if ext.fields[f].status in HEADLINE_FLAGS)
    halluc = sum(1 for f in miss if ext.fields[f].status in ("stated", "inferred") and ext.fields[f].value is not None)
    ev = [fr.evidence_verified for fr in ext.fields.values() if fr.status in ("stated", "inferred") and fr.evidence]
    return {"id": case["id"], "tags": case["tags"], "fields_total": len(exp), "fields_correct": correct,
            "missing_total": len(miss), "missing_flagged": flagged, "hallucinated": halluc,
            "evidence_total": len(ev), "evidence_verified": sum(1 for v in ev if v),
            "pass": correct == len(exp) and flagged == len(miss)}


def aggregate(rows: list[dict]) -> dict:
    def ratio(a, b):
        return round(a / b, 4) if b else None
    s = lambda k, sub=rows: sum(r[k] for r in sub)
    traps = [r for r in rows if "trap" in r["tags"]]
    clean = [r for r in rows if "clean" in r["tags"]]
    return {
        "cases": len(rows),
        "field_accuracy": ratio(s("fields_correct"), s("fields_total")),
        "missing_flag_rate": ratio(s("missing_flagged"), s("missing_total")),
        "hallucination_rate": ratio(s("hallucinated"), s("missing_total")),
        "evidence_verified_rate": ratio(s("evidence_verified"), s("evidence_total")),
        "case_pass_rate": ratio(sum(r["pass"] for r in rows), len(rows)),
        "clean_pass_rate": ratio(sum(r["pass"] for r in clean), len(clean)),
        "trap_pass_rate": ratio(sum(r["pass"] for r in traps), len(traps)),
    }


# ----------------------------------------------------------------------------- extractors (for self-test)
def oracle_extractor(case: dict) -> QuoteExtraction:
    """Returns the ground truth. Used ONLY to prove the scoring code is correct."""
    ext = empty_extraction()
    for f, v in case["expected"].items():
        ext.fields[f] = FieldResult(value=v, status="stated")
    for f in case["expected_missing"]:
        ext.fields[f] = FieldResult(status="missing", question="?")
    return ext


def empty_extractor(case: dict) -> QuoteExtraction:
    """Flags everything missing. Shows why 'missing_flag_rate' alone can be gamed."""
    return empty_extraction()


# ----------------------------------------------------------------------------- scenario rank agreement
def _scenario_inputs(ext: QuoteExtraction, label: str, setup: EvalSetup):
    try:
        inp, _ = build_inputs(ext, GENERIC_BUYER_ESTIMATES, setup, label=label, accept_defaults=True)
        return inp
    except MissingInputs:
        return None


def scenario_agreement(cases_by_id: dict[str, dict], extracted: dict[str, QuoteExtraction],
                       scenarios: list[list[str]], setup: EvalSetup | None = None) -> dict:
    setup = setup or EvalSetup()
    full_match = top_match = 0
    detail = []
    for sc in scenarios:
        truth, got = [], []
        for cid in sc:
            gt = oracle_extractor(cases_by_id[cid])
            truth.append(_scenario_inputs(gt, cid, setup))
            got.append(_scenario_inputs(extracted[cid], cid, setup))
        if any(x is None for x in got) or any(x is None for x in truth):
            detail.append({"scenario": sc, "agree": False, "reason": "price missing in extraction"})
            continue
        r_t, r_g = compare(setup, truth).ranking, compare(setup, got).ranking
        full_match += r_t == r_g
        top_match += r_t[0] == r_g[0]
        detail.append({"scenario": sc, "truth": r_t, "got": r_g, "agree": r_t == r_g})
    n = len(scenarios)
    return {"full_ranking_agreement": round(full_match / n, 4), "top1_agreement": round(top_match / n, 4), "detail": detail}
