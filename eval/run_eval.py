"""Run the extraction eval.

  python -m eval.run_eval --extractor oracle            # harness self-test (no API key)
  python -m eval.run_eval --extractor empty             # harness self-test (no API key)
  python -m eval.run_eval --extractor llm --prompt v1   # live model, zero-shot   (needs ANTHROPIC_API_KEY)
  python -m eval.run_eval --extractor llm --prompt v2   # live model, rules + few-shot
  python -m eval.run_eval --write-md                    # rebuild eval/results.md from saved results

Cost is reported only if you set TCO_PRICE_IN and TCO_PRICE_OUT (INR or USD per million tokens, your call).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evaluate import (aggregate, empty_extractor, load_cases, oracle_extractor,  # noqa: E402
                          scenario_agreement, score_case)
from src.extract import ExtractionError, empty_extraction, extract_quote  # noqa: E402
from src.llm import LLMClient  # noqa: E402

RESULTS = ROOT / "eval" / "results"


def run(extractor: str, prompt: str, limit: int | None) -> dict:
    cases = load_cases()[: limit or None]
    client = None
    if extractor == "llm":
        client = LLMClient()
        if client.mock:
            sys.exit("ANTHROPIC_API_KEY is not set (or TCO_MOCK=1). Live eval needs a key; see README.")
    rows, extracted, errors = [], {}, []
    tin = tout = 0
    lat = []
    for c in cases:
        t0 = time.perf_counter()
        if extractor == "oracle":
            ext = oracle_extractor(c)
        elif extractor == "empty":
            ext = empty_extractor(c)
        else:
            try:
                out = extract_quote(client, c["quote"], item="Mild steel fabricated bracket", unit=c["buyer_unit"], version=prompt)
                ext = out.extraction
                tin, tout = tin + out.input_tokens, tout + out.output_tokens
                lat.append(out.latency_s)
            except ExtractionError as e:
                errors.append({"id": c["id"], "error": str(e)})
                ext = empty_extraction("extraction failed")
        extracted[c["id"]] = ext
        rows.append(score_case(c, ext))
        print(f"  {c['id']:4} {'PASS' if rows[-1]['pass'] else 'FAIL'}  fields {rows[-1]['fields_correct']}/{rows[-1]['fields_total']}"
              f"  flagged {rows[-1]['missing_flagged']}/{rows[-1]['missing_total']}  halluc {rows[-1]['hallucinated']}", flush=True)
    sc = json.loads((ROOT / "data" / "eval" / "scenarios.json").read_text())["scenarios"]
    by_id = {c["id"]: c for c in load_cases()}
    scen = scenario_agreement(by_id, extracted, [s for s in sc if all(i in extracted for i in s)]) if len(cases) == len(by_id) else None
    result = {"extractor": extractor, "prompt": prompt if extractor == "llm" else None,
              "model": client.model if client else None, "limit": limit, "metrics": aggregate(rows), "scenarios": scen,
              "per_case": rows, "errors": errors,
              "usage": {"input_tokens": tin, "output_tokens": tout, "avg_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
                        "runs": len(lat)}}
    p_in, p_out = os.getenv("TCO_PRICE_IN"), os.getenv("TCO_PRICE_OUT")
    if client and p_in and p_out:
        result["usage"]["cost"] = round(tin / 1e6 * float(p_in) + tout / 1e6 * float(p_out), 4)
    return result


def write_md() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    files = {p.stem: json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))}
    pct = lambda v: "n/a" if v is None else f"{v * 100:.0f}%"
    out = ["# Eval results", "",
           "20 synthetic supplier quotes (10 clean, 10 traps) with hand-written ground truth. See `data/eval/eval_set.jsonl`.",
           "", "## 1. Harness self-test (proves the scoring code works; NOT model performance)", "",
           "| Extractor | Field acc. | Missing flagged | Hallucinated | Case pass | Trap pass |", "|---|---|---|---|---|---|"]
    for name in ("oracle", "empty"):
        if name in files:
            m = files[name]["metrics"]
            out.append(f"| {name} | {pct(m['field_accuracy'])} | {pct(m['missing_flag_rate'])} | {pct(m['hallucination_rate'])} | {pct(m['case_pass_rate'])} | {pct(m['trap_pass_rate'])} |")
    out += ["", "`oracle` returns the ground truth, so every metric must be 100%. `empty` flags every field as missing: it scores 100% on",
            "'missing flagged' yet passes almost no cases. That is why **case pass rate**, not any single metric, is the headline number.", "",
            "## 2. Live model results", ""]
    live = {k: v for k, v in files.items() if k.startswith("llm_")}
    if not live:
        out += ["**Not yet run.** No API key was available when this repo was built, so no model numbers are claimed here.",
                "", "Run these two commands with your key, then `python -m eval.run_eval --write-md` to refresh this page:", "",
                "```bash", "export ANTHROPIC_API_KEY=sk-ant-...", "python -m eval.run_eval --extractor llm --prompt v1", "python -m eval.run_eval --extractor llm --prompt v2", "```"]
    else:
        out += ["| Prompt | Model | Field acc. | Missing flagged | Hallucinated | Evidence verified | Case pass | Clean pass | Trap pass | Rank agreement | Tokens in/out | Avg latency |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, r in live.items():
            m, u, s = r["metrics"], r["usage"], r["scenarios"] or {}
            out.append(f"| {r['prompt']} | {r['model']} | {pct(m['field_accuracy'])} | {pct(m['missing_flag_rate'])} | {pct(m['hallucination_rate'])} | "
                       f"{pct(m['evidence_verified_rate'])} | {pct(m['case_pass_rate'])} | {pct(m['clean_pass_rate'])} | {pct(m['trap_pass_rate'])} | "
                       f"{pct(s.get('full_ranking_agreement'))} | {u['input_tokens']}/{u['output_tokens']} | {u['avg_latency_s']}s |")
        out += ["", "### What failed and what was fixed", "", "_Fill this in after reading `eval/results/*.json` (per-case rows list every failing case)._"]
    (ROOT / "eval" / "results.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("wrote eval/results.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", choices=["oracle", "empty", "llm"])
    ap.add_argument("--prompt", choices=["v1", "v2"], default="v2")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--write-md", action="store_true")
    a = ap.parse_args()
    if a.extractor:
        res = run(a.extractor, a.prompt, a.limit)
        RESULTS.mkdir(parents=True, exist_ok=True)
        name = a.extractor if a.extractor != "llm" else f"llm_{a.prompt}"
        (RESULTS / f"{name}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(json.dumps(res["metrics"], indent=1))
        if res["scenarios"]:
            print("scenario agreement:", {k: v for k, v in res["scenarios"].items() if k != "detail"})
    if a.write_md or a.extractor:
        write_md()
