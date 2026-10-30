"""Engine tests. The key one reads the ORIGINAL Excel workbook's cached results and demands a match."""
from pathlib import Path

import pytest
from openpyxl import load_workbook

from src.defaults import DEMO_BUYER_ESTIMATES
from src.engine import BUCKETS, ESTIMATE_BUCKETS, FACTORS, compare, pv_multiplier, rank_by_horizon
from src.schema import BUYER_FIELDS, EvalSetup, SupplierInputs

WB = Path(__file__).resolve().parent.parent / "reference" / "India_Domestic_Supplier_TCO_Tool_Harshita.xlsx"
ROWS = {"price": 10, "gst": 11, "esc": 12, "orders": 15, "moq": 16, "lead": 17, "pay": 18, "safety": 19, "fr_inc": 21, "pk_inc": 22,
        "rfq": 24, "qual": 25, "tool": 26, "contract": 27, "freight": 29, "ins": 30, "handling": 31, "pack": 32, "levy": 33,
        "insp": 34, "admin": 35, "bank": 36, "rej": 38, "rejc": 39, "late": 40, "latec": 41, "mgmt": 42, "warr": 44, "warrc": 45,
        "disp": 46, "comp": 47, "dp": 49, "di": 50, "rp": 51, "ri": 52, "op": 53, "oi": 54}


def workbook_suppliers():
    si = load_workbook(WB, data_only=True)["Supplier Inputs"]
    out = []
    for col in (2, 3, 4):
        g = lambda k: si.cell(ROWS[k], col).value
        out.append(SupplierInputs(
            name=si.cell(5, col).value, quoted_unit_price=g("price"), price_includes_gst=g("gst") == "Yes",
            annual_price_escalation=g("esc"), orders_per_year=g("orders"), moq=g("moq"), lead_time_days=g("lead"),
            payment_terms_days=g("pay"), safety_stock_days=g("safety"), freight_included=g("fr_inc") == "Yes",
            packaging_included=g("pk_inc") == "Yes", rfq_cost=g("rfq"), qualification_cost=g("qual"), tooling_cost=g("tool"),
            contract_cost=g("contract"), freight_per_order=g("freight"), transit_insurance_pct=g("ins"),
            handling_per_order=g("handling"), packaging_per_unit=g("pack"), other_levies_pct=g("levy"),
            inspection_per_order=g("insp"), admin_per_order=g("admin"), bank_charges_per_year=g("bank"),
            rejection_rate=g("rej"), cost_per_rejected_unit=g("rejc"), late_rate=g("late"), cost_per_late_order=g("latec"),
            supplier_mgmt_per_year=g("mgmt"), warranty_rate=g("warr"), cost_per_claim=g("warrc"),
            disposal_per_unit=g("disp"), compliance_per_year=g("comp"), disruption_prob=g("dp"), disruption_impact=g("di"),
            regulatory_prob=g("rp"), regulatory_impact=g("ri"), other_risk_prob=g("op"), other_risk_impact=g("oi")))
    return out


def base(**kw):
    d = dict(name="X", quoted_unit_price=100.0, orders_per_year=12)
    d.update(kw)
    return SupplierInputs(**d)


@pytest.mark.skipif(not WB.exists(), reason="reference workbook not present")
class TestAgainstWorkbook:
    def test_totals_per_unit_and_rank_match_tco_summary(self):
        wb = load_workbook(WB, data_only=True)
        sm = wb["TCO Summary"]
        rows = {sm.cell(r, 1).value: r for r in range(1, 40) if sm.cell(r, 1).value}
        comp = compare(EvalSetup(), workbook_suppliers())
        for i, res in enumerate(comp.suppliers):
            col = 2 + i
            assert res.total == pytest.approx(sm.cell(rows["TOTAL TCO"], col).value, abs=1)
            assert res.per_unit == pytest.approx(sm.cell(rows["TCO per unit"], col).value, abs=1e-6)
            assert res.rank == sm.cell(rows["Rank by TCO per unit (1 = lowest)"], col).value
            assert res.hidden_cost_pct == pytest.approx(sm.cell(rows["Hidden cost above purchase price"], col).value, abs=1e-9)
            for b in BUCKETS:
                assert res.buckets[b] == pytest.approx(sm.cell(rows[b], col).value, abs=1)

    def test_cumulative_years_match_dashboard(self):
        d = load_workbook(WB, data_only=True)["Dashboard"]
        comp = compare(EvalSetup(), workbook_suppliers())
        for r in range(1, 40):
            label = d.cell(r, 1).value
            if isinstance(label, str) and label.startswith("Year "):
                y = int(label.split()[1])
                for i, res in enumerate(comp.suppliers):
                    assert res.cumulative[y - 1] == pytest.approx(d.cell(r, 2 + i).value, abs=1)

    def test_every_factor_matches_tco_calculation_sheet(self):
        ws = load_workbook(WB, data_only=True)["TCO Calculation"]
        comp = compare(EvalSetup(), workbook_suppliers())
        ids = {ws.cell(r, 1).value: r for r in range(4, 32)}
        for fid, _b, _n, _k in FACTORS:
            for i, res in enumerate(comp.suppliers):
                assert res.year1[fid] == pytest.approx(ws.cell(ids[fid], 6 + i).value, abs=0.01), fid
                assert res.horizon[fid] == pytest.approx(ws.cell(ids[fid], 9 + i).value, abs=0.01), fid

    def test_demo_buyer_estimates_equal_workbook_inputs(self):
        for lab, s in zip("ABC", workbook_suppliers()):
            for attr, _l, _u in BUYER_FIELDS:
                assert getattr(s, attr) == pytest.approx(DEMO_BUYER_ESTIMATES[lab][attr]), (lab, attr)


class TestEngineBehaviour:
    setup = EvalSetup()

    def total(self, s, setup=None, **kw):
        return compare(setup or self.setup, [s], **kw).suppliers[0]

    def test_gst_inclusive_price_is_stripped(self):
        incl = self.total(base(quoted_unit_price=118.0, price_includes_gst=True))
        excl = self.total(base(quoted_unit_price=100.0, price_includes_gst=False))
        assert incl.horizon["P1"] == pytest.approx(excl.horizon["P1"])

    def test_partial_itc_creates_non_recoverable_gst_cost(self):
        r = self.total(base(), EvalSetup(itc_recoverable=0.5))
        assert r.year1["T5"] == pytest.approx(100 * 120000 * 0.18 * 0.5)
        assert self.total(base()).year1["T5"] == 0

    def test_included_flags_zero_freight_and_packaging(self):
        s = base(freight_per_order=9000, packaging_per_unit=2, freight_included=True, packaging_included=True)
        r = self.total(s)
        assert r.year1["T1"] == 0 and r.year1["T4"] == 0
        r2 = self.total(base(freight_per_order=9000, packaging_per_unit=2))
        assert r2.year1["T1"] == 9000 * 12 and r2.year1["T4"] == 2 * 120000

    def test_credit_days_are_a_benefit_and_advance_is_a_cost(self):
        assert self.total(base(payment_terms_days=45)).year1["H5"] < 0
        assert self.total(base(payment_terms_days=-15)).year1["H5"] > 0
        assert self.total(base(payment_terms_days=0)).year1["H5"] == 0

    def test_horizon_one_equals_year_one_total(self):
        r = compare(self.setup, [base()], horizon=1).suppliers[0]
        assert r.total == pytest.approx(sum(r.year1.values()))

    def test_discount_rate_reduces_value_and_matches_formula(self):
        s = base(annual_price_escalation=0.04)
        und = self.total(s)
        disc = self.total(s, EvalSetup(discount_rate=0.10))
        assert disc.total < und.total
        assert disc.horizon["P1"] == pytest.approx(disc.year1["P1"] * pv_multiplier(0.04, 3, 0.10))

    def test_sensitivity_scales_only_q_r_s(self):
        s = base(rejection_rate=0.01, cost_per_rejected_unit=50, disruption_prob=0.1, disruption_impact=100000)
        r1 = self.total(s)
        r2 = self.total(s, qrs_multiplier=2.0)
        qrs = sum(r1.buckets[b] for b in ESTIMATE_BUCKETS)
        assert r2.total == pytest.approx(r1.total + qrs)
        assert r2.buckets["T Transaction (recurring)"] == pytest.approx(r1.buckets["T Transaction (recurring)"])

    def test_bucket_totals_sum_to_total(self):
        r = compare(self.setup, workbook_suppliers() if WB.exists() else [base()]).suppliers[0]
        assert sum(r.buckets.values()) == pytest.approx(r.total)

    def test_rank_by_horizon_flips_in_workbook_sample(self):
        if not WB.exists():
            pytest.skip("no workbook")
        rb = rank_by_horizon(self.setup, workbook_suppliers())
        assert rb[1][0] == "Supplier C Enterprises"
        assert rb[3][0] == "Supplier B Industries"
        assert rb[5][0] == "Supplier A Pvt Ltd"

    def test_close_call_flag(self):
        a, b = base(name="A", quoted_unit_price=100), base(name="B", quoted_unit_price=100.4)
        assert compare(self.setup, [a, b]).close_call
        c = base(name="C", quoted_unit_price=110)
        assert not compare(self.setup, [a, c]).close_call
