from src.evaluate import (aggregate, empty_extractor, load_cases, oracle_extractor, scenario_agreement, score_case, values_match)
from src.schema import FieldResult


def test_oracle_scores_100_percent():
    cases = load_cases()
    m = aggregate([score_case(c, oracle_extractor(c)) for c in cases])
    assert m["case_pass_rate"] == 1.0 and m["field_accuracy"] == 1.0 and m["hallucination_rate"] == 0.0


def test_empty_extractor_flags_everything_but_still_fails_cases():
    cases = load_cases()
    m = aggregate([score_case(c, empty_extractor(c)) for c in cases])
    assert m["missing_flag_rate"] == 1.0 and m["field_accuracy"] == 0.0 and m["case_pass_rate"] <= 0.1


def test_hallucinating_a_missing_field_is_detected():
    case = next(c for c in load_cases() if c["id"] == "t04")          # MOQ is not in the quote
    ext = oracle_extractor(case)
    ext.fields["moq"] = FieldResult(value=5000, status="inferred")
    row = score_case(case, ext)
    assert row["hallucinated"] == 1 and row["missing_flagged"] == 0 and not row["pass"]


def test_value_matching_rules():
    assert values_match("num", 151.4, 151) and not values_match("num", 160, 151)
    assert values_match("bool", False, False) and not values_match("bool", None, False)
    assert values_match("str", "ISO 9001:2015, IATF 16949", "iso 9001") and not values_match("str", None, "x")
    assert values_match("num", 0, 0) and not values_match("num", 5, 0)


def test_scenario_agreement_is_perfect_for_oracle():
    import json
    from src.evaluate import ROOT
    cases = {c["id"]: c for c in load_cases()}
    sc = json.loads((ROOT / "data" / "eval" / "scenarios.json").read_text())["scenarios"]
    res = scenario_agreement(cases, {i: oracle_extractor(c) for i, c in cases.items()}, sc)
    assert res["full_ranking_agreement"] == 1.0
