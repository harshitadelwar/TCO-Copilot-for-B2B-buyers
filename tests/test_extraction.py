import json
from pathlib import Path

import pytest

from src.defaults import DEMO_BUYER_ESTIMATES, MissingInputs, build_inputs
from src.engine import compare
from src.evaluate import load_cases
from src.explain import build_facts, explain, format_inr, template_narrative, ungrounded_numbers
from src.extract import (ExtractionError, build_messages, extract_quote, normalise_extraction, parse_json, quote_key)
from src.llm import LLMClient, LLMResult
from src.schema import QUOTE_FIELDS, EvalSetup

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {l: (ROOT / "data" / "sample_quotes" / f"supplier_{l.lower()}.txt").read_text() for l in "abc"}


class FakeClient:
    """Stands in for the live API: returns queued texts."""
    mock, model = False, "fake"

    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def complete(self, system, messages, max_tokens=0, temperature=0.0):
        self.calls.append(messages)
        return LLMResult(text=self.texts.pop(0), input_tokens=10, output_tokens=5, latency_s=0.1, model="fake")


def valid_payload(**over):
    fields = {s.name: {"value": None, "status": "missing", "evidence": "", "question": "q?"} for s in QUOTE_FIELDS}
    fields.update(over)
    return {"fields": fields}


def test_parse_json_handles_fences_and_chatter():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_all_fields_present_and_missing_has_null_value():
    ext = normalise_extraction({"fields": {}}, "text")
    assert set(ext.fields) == {s.name for s in QUOTE_FIELDS}
    assert all(f.status == "missing" and f.value is None for f in ext.fields.values())


def test_coercion_and_percent_fix():
    q = "Rs 1,50,000 tooling; escalation 5 percent"
    ext = normalise_extraction(valid_payload(
        tooling_cost={"value": "1,50,000", "status": "stated", "evidence": "Rs 1,50,000 tooling"},
        annual_price_escalation={"value": 5, "status": "stated", "evidence": "escalation 5 percent"},
        price_includes_gst={"value": "No", "status": "stated", "evidence": ""}), q)
    assert ext.fields["tooling_cost"].value == 150000
    assert ext.fields["annual_price_escalation"].value == pytest.approx(0.05)
    assert ext.fields["price_includes_gst"].value is False


def test_evidence_must_exist_in_quote():
    ext = normalise_extraction(valid_payload(
        quoted_unit_price={"value": 99, "status": "stated", "evidence": "Rs 99 per piece"}), "Rate is Rs 142 per piece")
    assert ext.fields["quoted_unit_price"].evidence_verified is False
    ok = normalise_extraction(valid_payload(
        quoted_unit_price={"value": 142, "status": "stated", "evidence": "Rs 142 per piece"}), "Rate is Rs 142 per piece")
    assert ok.fields["quoted_unit_price"].evidence_verified is True


def test_flat_v1_format_is_accepted():
    ext = normalise_extraction({"quoted_unit_price": 142, "price_includes_gst": False, "moq": None}, "x")
    assert ext.fields["quoted_unit_price"].status == "stated" and ext.fields["moq"].status == "missing"


def test_unit_mismatch_guard_forces_conflict_even_if_model_says_stated():
    ext = normalise_extraction(valid_payload(
        price_unit={"value": "kg", "status": "stated", "evidence": "per kg"},
        quoted_unit_price={"value": 68, "status": "stated", "evidence": "Rs 68 per kg"}), "Rs 68 per kg", buyer_unit="piece")
    f = ext.fields["quoted_unit_price"]
    assert f.status == "conflict" and f.value is None and "per piece" in f.question
    same = normalise_extraction(valid_payload(
        price_unit={"value": "pcs", "status": "stated", "evidence": ""},
        quoted_unit_price={"value": 68, "status": "stated", "evidence": ""}), "x", buyer_unit="piece")
    assert same.fields["quoted_unit_price"].status == "stated"


def test_retry_once_on_invalid_json_then_succeeds():
    good = json.dumps(valid_payload(quoted_unit_price={"value": 142, "status": "stated", "evidence": "142"}))
    c = FakeClient("not json at all", good)
    out = extract_quote(c, "rate 142", unit="piece")
    assert out.attempts == 2 and out.extraction.fields["quoted_unit_price"].value == 142
    assert out.input_tokens == 20 and "invalid" in c.calls[1][-1]["content"]


def test_gives_up_after_one_retry():
    with pytest.raises(ExtractionError):
        extract_quote(FakeClient("bad", "still bad"), "rate 142")


def test_v2_prompt_includes_fewshot_v1_does_not():
    assert len(build_messages("q", "item", "piece", "v2")) == 9      # 4 examples x 2 + the real quote
    assert len(build_messages("q", "item", "piece", "v1")) == 1


def test_fewshot_examples_are_valid_and_do_not_leak_eval_quotes():
    shots = json.loads((ROOT / "prompts" / "fewshot.json").read_text())
    eval_keys = {quote_key(c["quote"]) for c in load_cases()}
    for ex in shots:
        ext = normalise_extraction(ex["output"], ex["quote"], ex["buyer_unit"])
        assert all(f.evidence_verified is not False for f in ext.fields.values()), "few-shot evidence must be verbatim"
        assert quote_key(ex["quote"]) not in eval_keys


def test_eval_ground_truth_is_internally_consistent():
    for c in load_cases():
        assert not set(c["expected"]) & set(c["expected_missing"]), c["id"]


# ------------------------------------------------------------------ build_inputs: flag, don't guess
def _extraction(**over):
    base = valid_payload(
        quoted_unit_price={"value": 100, "status": "stated", "evidence": "100"},
        price_includes_gst={"value": False, "status": "stated", "evidence": ""},
        payment_terms_days={"value": 30, "status": "stated", "evidence": ""},
        freight_included={"value": True, "status": "stated", "evidence": ""},
        packaging_included={"value": True, "status": "stated", "evidence": ""})
    base["fields"].update(over)
    return normalise_extraction(base, "100")


def test_missing_price_can_never_be_defaulted():
    ext = normalise_extraction({"fields": {}}, "x")
    with pytest.raises(MissingInputs):
        build_inputs(ext, DEMO_BUYER_ESTIMATES["A"], EvalSetup(), accept_defaults=True)


def test_blocking_gap_raises_with_clarifying_question_unless_defaults_accepted():
    ext = _extraction(price_includes_gst={"value": None, "status": "missing", "evidence": "", "question": "Is GST included?"})
    with pytest.raises(MissingInputs) as e:
        build_inputs(ext, DEMO_BUYER_ESTIMATES["A"], EvalSetup())
    assert "price_includes_gst" in e.value.fields and e.value.questions["price_includes_gst"] == "Is GST included?"
    inp, assumptions = build_inputs(ext, DEMO_BUYER_ESTIMATES["A"], EvalSetup(), accept_defaults=True)
    assert any(a["field"] == "price_includes_gst" and a["source"] == "default" for a in assumptions)


def test_freight_amount_not_needed_when_freight_included_but_needed_when_not():
    ok, _ = build_inputs(_extraction(), DEMO_BUYER_ESTIMATES["A"], EvalSetup())
    assert ok.freight_per_order == 0
    ext = _extraction(freight_included={"value": False, "status": "stated", "evidence": ""})
    with pytest.raises(MissingInputs) as e:
        build_inputs(ext, DEMO_BUYER_ESTIMATES["A"], EvalSetup())
    assert "freight_per_order" in e.value.fields


def test_soft_defaults_apply_but_are_always_listed():
    inp, assumptions = build_inputs(_extraction(), DEMO_BUYER_ESTIMATES["A"], EvalSetup())
    fields = {a["field"] for a in assumptions}
    assert {"tooling_cost", "annual_price_escalation"} <= fields


# ------------------------------------------------------------------ end to end (mock fixtures)
def test_mock_pipeline_reproduces_workbook_totals():
    client, setup = LLMClient(mock=True), EvalSetup()
    sups = []
    for lab in "ABC":
        out = extract_quote(client, SAMPLES[lab.lower()], unit="piece")
        assert out.source == "fixture"
        assert all(f.evidence_verified is not False for f in out.extraction.fields.values())
        inp, _ = build_inputs(out.extraction, DEMO_BUYER_ESTIMATES[lab], setup, label=f"Supplier {lab}")
        sups.append(inp)
    comp = compare(setup, sups)
    assert [r.total for r in comp.suppliers] == pytest.approx([54176767.487014, 54144419.529452, 55413044.859415], abs=1)
    assert comp.ranking[0].startswith("Supplier B")


def test_mock_mode_refuses_unknown_quotes_instead_of_inventing():
    with pytest.raises(ExtractionError):
        extract_quote(LLMClient(mock=True), "some new quote Rs 150 per piece")


# ------------------------------------------------------------------ explanation grounding
def _facts():
    client, setup = LLMClient(mock=True), EvalSetup()
    sups, ass = [], []
    for lab in "ABC":
        out = extract_quote(client, SAMPLES[lab.lower()])
        inp, a = build_inputs(out.extraction, DEMO_BUYER_ESTIMATES[lab], setup, label=f"Supplier {lab}")
        sups.append(inp)
        ass += a
    return build_facts(compare(setup, sups), sups, ass)


def test_format_inr_uses_indian_grouping():
    assert format_inr(54144419.53) == "\u20b95,41,44,420"
    assert format_inr(1234) == "\u20b91,234" and format_inr(-221000) == "-\u20b92,21,000"


def test_template_narrative_is_fully_grounded_and_flags_close_call():
    f = _facts()
    text = template_narrative(f)
    assert ungrounded_numbers(text, f) == []
    assert f["close_call"] and "effectively a tie" in text


def test_grounding_check_catches_invented_numbers():
    f = _facts()
    assert "12.3" in ungrounded_numbers("Supplier B is 12.3% cheaper", f)
    assert ungrounded_numbers("Supplier B is 0.06% closer, total \u20b95,41,44,420", f) == []


def test_llm_narrative_with_invented_number_is_retried_then_replaced_by_template():
    f = _facts()
    bad = "**Verdict** B saves \u20b99,99,99,999 overall."
    res = explain(FakeClient(bad, bad), f)
    assert res.source == "template" and res.ungrounded and "9,99,99,999" in res.ungrounded[0]
    good = template_narrative(f)
    res2 = explain(FakeClient(bad, good), f)
    assert res2.source == "llm" and res2.attempts == 2
