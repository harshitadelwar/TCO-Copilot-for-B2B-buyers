# TCO Copilot: AI-assisted supplier comparison for B2B buyers

> Paste messy supplier quotes (email, WhatsApp, Hinglish). The AI extracts the cost-relevant fields and **flags what is missing instead of guessing**. A deterministic engine computes true Total Cost of Ownership (TCO). The AI then explains who wins, how close the call is, and what to confirm before ordering.

**Pilot project** built as a portfolio piece for an AI Assistant Product Manager application (B2B marketplace context).
**Live demo:** _add your Streamlit link here after deploying (steps below)_ · **Screenshot:** _add `docs/screenshot.png` after your first run_

##Application Preview
# TCO Copilot

A powerful supply chain and procurement decision-making platform that blends intelligent supplier quote extraction with a deterministic Total Cost of Ownership (TCO) calculation engine.

## Application Preview

### 1. Supplier Quote Extraction
![Quote Extraction](docs/screenshot-1-quotes.png)

### 2. Buyer Estimates
![Buyer Estimates](docs/screenshot-2-buyer-estimates.png)

### 3. TCO Comparison and Verdict
![TCO Verdict](docs/screenshot-3-tco-verdict.png)

---

## Overview

Evaluating procurement options manually often leaves out hidden logistics, quality risks, and carrying costs. **TCO Copilot** streamlines this workflow by combining:
* **AI Extraction:** Intelligently parses unstructured text quotes from multiple vendors into standardized fields while flagging missing details and gaps.
* **Deterministic TCO Engine:** Computes exact Total Cost of Ownership without letting the LLM handle critical arithmetic, ensuring mathematical precision.
* **Comprehensive Verdicts:** Analyzes supplier rankings, gap analysis, cost buckets, and horizon sensitivities to deliver actionable procurement recommendations.

## Architecture

The application is structured into three primary processing phases:
1. **Quote Extraction Layer (LLM Parsing):** Reads unstructured supplier quotes and normalizes them into key fields (pricing, terms, lead times, MOQ, freight, packaging).
2. **Buyer Parameters & Estimation Layer:** Accepts internal organizational inputs (demand, financial interest rates, risk metrics, inspection costs, and rejection rates) that are never shared with suppliers.
3. **Deterministic Calculation & Sensitivity Engine:** Runs standard financial logic to evaluate Total Cost of Ownership across customizable project horizons (1–5 years) and generates detailed breakdowns of hidden costs.

## Features

* **Multi-Supplier Quote Comparison:** Side-by-side breakdown of Supplier A, B, and C with stated vs. missing data flags.
* **Customizable Buyer Parameters:** Adjust annual demand, GST, cost of capital, storage costs, and horizon timelines dynamically.
* **Hidden Cost Analysis:** Breaks down expenses beyond the basic unit price—including inbound freight, packaging, safety stock carrying costs, warranty claims, and transaction fees.
* **Horizon & Sensitivity Modeling:** Visualizes how winning suppliers shift across 1-to-5-year timelines and handles sensitivity adjustments.



## Status (read this first)

| Piece | Status |
|---|---|
| TCO engine (Python port of the Excel model) | Done. Matches the original workbook to the rupee: totals, per-unit, ranks, all 28 factors, all 5 cumulative years |
| Extraction pipeline (prompts v1/v2, validation, evidence check, guards) | Done and unit-tested with a fake client |
| Explanation layer with number-grounding check | Done and unit-tested |
| Streamlit app | Done. Runs in **mock mode** with no API key (3 bundled sample quotes) |
| 20-case eval set + scoring harness | Done. Harness self-tested (see `eval/results.md`) |
| **Live-model eval numbers** | **Not run yet.** Needs an API key. Nothing is claimed until you run it (2 commands below) |

## 1. Problem

A dealer buying the same item from three suppliers usually compares **quoted price**. The real cost also includes freight, non-recoverable GST, payment terms, safety stock, rejections, late deliveries, warranty and disruption risk. In the sample data, Supplier C has the lowest Year-1 cost but the **highest** 3-year cost, and the top two suppliers differ by just 0.06%. Price alone hides all of that.

Two things block adoption: (1) the cost model needs clean structured inputs, but quotes arrive as free text; (2) a bare ranking says nothing about how fragile it is.

## 2. Why this design

```
Quote text ─► [LLM: extract + flag gaps] ─► validated JSON ─► [Python: TCO math] ─► numbers ─► [LLM: explain] ─► verdict
                        │                          │                                              │
        status + evidence per field     deterministic, tested vs Excel               every number checked against engine output
```

- **The LLM never does arithmetic.** It reads and explains; code computes. This removes the most dangerous failure mode: a confident, wrong number.
- **Missing is a valid answer.** Every field returns `stated | inferred | missing | conflict`, the exact evidence span, and a clarifying question. Defaults are applied only if the user opts in, and each one is listed in an *Assumptions* panel.
- **Evidence is verified.** The quoted evidence must be a verbatim substring of the quote; otherwise the field is marked with a warning.
- **Deterministic guards on top of the prompt.** Example: if a price is quoted per kg but the buyer compares per piece, the code forces `conflict` regardless of what the model said.
- **The verdict is grounded.** Every number in the narrative must appear in the engine output. If not, the model gets one retry, then a deterministic template is shown instead.
- **Buyer-side numbers are never extracted.** Rejection history, delivery record and risk estimates are not in a quote, so the AI does not invent them. The buyer supplies them (tab 2).
- **Unit normalisation is allowed, price conversion is not.** "5 hazaar" becomes 5000 and "2 weeks" becomes 14 days, but Rs 68/kg is never converted to a per-piece price.

## 3. How to run

```bash
git clone https://github.com/harshitadelwar/TCO-Copilot-for-B2B-buyers.git
cd TCO-Copilot-for-B2B-buyers
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m pytest -q          # 40 tests, no API key needed
streamlit run app.py         # mock mode: works offline with the 3 sample quotes
```

**Live mode** (extract from your own quotes, LLM-written verdict):

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
streamlit run app.py
```

Choose the model with `TCO_MODEL` (default `claude-sonnet-5-5`; `claude-haiku-5-5` is cheaper). The app header shows which mode you are in.

**Deploy a public demo (free):** push this repo, go to share.streamlit.io, create an app from `app.py`, and under *Advanced settings → Secrets* add `ANTHROPIC_API_KEY = "sk-ant-..."`. Set a spend limit on that key, since the link is public.

## 4. Using the app

1. **Quotes → fields.** Paste up to three quotes (samples preloaded) and click *Extract fields*. Edit any value to answer a clarifying question.
2. **Buyer estimates.** Your own rejection, delay and risk assumptions (demo values are the workbook's illustrative samples).
3. **TCO & verdict.** Ranking, hidden cost by bucket, cumulative-cost crossover, *does the winner change with the horizon?*, a sensitivity slider on the estimate-driven costs, the AI verdict, an assumptions list, and a full per-factor audit trail.

## 5. Evaluation

20 synthetic quotes (10 clean in varied formats incl. Hinglish and WhatsApp style, 10 traps) with hand-written ground truth in `data/eval/eval_set.jsonl`.

**Traps:** GST ambiguity ("all-in"), freight buried mid-sentence, advance payment, missing MOQ, conflicting prices, packaging outside the price, "Net 45", per-kg vs per-piece, irrelevant chatter, and an almost-empty message.

**Metrics:** field accuracy (exact for flags, within 1% for numbers), missing fields flagged vs hallucinated, case pass rate (headline), evidence-verified rate, rank agreement on 5 supplier-trio scenarios, tokens and latency. Compares prompt **v1** (zero-shot) with **v2** (rules + few-shot; few-shot examples are checked by a test not to overlap the eval set).

```bash
python -m eval.run_eval --extractor llm --prompt v1
python -m eval.run_eval --extractor llm --prompt v2
python -m eval.run_eval --write-md        # refreshes eval/results.md
```

Results page: [`eval/results.md`](eval/results.md). Per-case rows are saved in `eval/results/*.json`, so you can read exactly which cases failed and fix the prompt. Then fill in the *what failed / what was fixed* section.

## 6. Scope

**In:** text quotes · 3 suppliers · 1-5 year horizon · GST/ITC handling · discounting · sensitivity · edit-and-confirm extraction.
**Out, on purpose:** PDF/OCR · volume-discount slabs · live marketplace data · authentication · database · exporting back into the Excel file (the original workbook has charts that a Python rewrite would destroy).

## 7. Limitations (be honest about these in interviews)

- **Sample numbers are illustrative.** Quality, delivery and risk costs are expected values (probability x impact) from estimates, not measured data.
- **The eval set is synthetic** and written by the project author. It tests the design, not real-world distribution. Real quotes would be messier.
- The model structure is adapted from the *Reshoring Initiative TCO Estimator*'s public description (cost factors → categories → TCO per source → multi-year view) and the course template this project started from. The India-specific treatment (GST/ITC, credit days, MSME note), the Python engine, and the AI layer are this project's additions.
- No capacity constraints, volume slabs or changing demand. Non-cost criteria (quality systems, capacity, strategic fit) need human judgement.
- Mock mode uses hand-written fixtures for the 3 sample quotes. It demonstrates the flow; it is **not** model output.

## 8. If this were shipped inside a marketplace

- Pre-fill supplier responses from structured enquiries instead of free text, and keep this extraction for off-platform quotes.
- Use platform-side delivery and rejection history to fill the buyer-estimate table that today needs manual input.
- Look up GST rate from the item's HSN code (a small RAG over the GST schedule) instead of a manual field.
- Log which clarifying questions buyers answer, and which verdicts lead to orders, to improve prompts with real data.
- Guardrails to keep: LLM never computes, grounded numbers, opt-in defaults, human confirmation before any order.

## Repo map

```
app.py                  Streamlit UI
src/schema.py           field specs and engine input models
src/engine.py           deterministic TCO engine (port of the workbook)
src/extract.py          LLM extraction, validation, evidence check, guards
src/defaults.py         buyer-side demo estimates, quote -> engine inputs, "flag don't guess" logic
src/explain.py          explanation + number-grounding check + template fallback
src/evaluate.py         scoring functions
prompts/                extraction v1/v2, few-shot examples, explanation prompt
data/                   sample quotes, mock fixtures, eval set
eval/                   eval runner and results
tests/                  40 tests (engine vs original Excel, extraction, grounding, eval harness)
reference/              the original Excel workbook the engine is verified against
```

## For the application form

**Project name:** TCO Copilot: AI-assisted supplier comparison for B2B buyers
**Description:** Turns messy supplier quotes (email/WhatsApp/Hinglish) into structured inputs, flags missing or ambiguous terms instead of guessing, computes true 3-year total cost of ownership with a tested deterministic engine, and explains the ranking, including close calls and horizon-dependent winners. Every number the AI writes is verified against the engine output.
**AI/ML used:** LLM structured extraction (Claude API, JSON schema, few-shot prompting, evidence verification), grounded LLM explanation, prompt A/B evaluation on a 20-case trap set, deterministic guardrails; Python, Pydantic, Streamlit.
**Link:** https://github.com/harshitadelwar/TCO-Copilot-for-B2B-buyers
