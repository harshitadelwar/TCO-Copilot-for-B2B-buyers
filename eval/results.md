# Eval results

20 synthetic supplier quotes (10 clean, 10 traps) with hand-written ground truth. See `data/eval/eval_set.jsonl`.

## 1. Harness self-test (proves the scoring code works; NOT model performance)

| Extractor | Field acc. | Missing flagged | Hallucinated | Case pass | Trap pass |
|---|---|---|---|---|---|
| oracle | 100% | 100% | 0% | 100% | 100% |
| empty | 0% | 100% | 0% | 5% | 10% |

`oracle` returns the ground truth, so every metric must be 100%. `empty` flags every field as missing: it scores 100% on
'missing flagged' yet passes almost no cases. That is why **case pass rate**, not any single metric, is the headline number.

## 2. Live model results

**Not yet run.** No API key was available when this repo was built, so no model numbers are claimed here.

Run these two commands with your key, then `python -m eval.run_eval --write-md` to refresh this page:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m eval.run_eval --extractor llm --prompt v1
python -m eval.run_eval --extractor llm --prompt v2
```
