"""TCO Copilot - AI-assisted supplier comparison for B2B buyers (Streamlit pilot)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.defaults import DEMO_BUYER_ESTIMATES, MissingInputs, build_inputs
from src.engine import BUCKETS, FACTORS, compare, rank_by_horizon
from src.explain import build_facts, explain, format_inr
from src.extract import ExtractionError, _coerce, extract_quote
from src.llm import LLMClient
from src.schema import BUYER_FIELDS, FIELD_BY_NAME, QUOTE_FIELDS, EvalSetup, FieldResult, QuoteExtraction

ROOT = Path(__file__).parent
LABELS = ["A", "B", "C"]
EMOJI = {"stated": "\U0001F7E2 stated", "inferred": "\U0001F7E1 inferred", "missing": "\U0001F534 missing", "conflict": "\U0001F7E0 conflict"}

st.set_page_config(page_title="TCO Copilot", page_icon="\U0001F9EE", layout="wide")

try:  # Streamlit Community Cloud: put ANTHROPIC_API_KEY in the app's Secrets
    if "ANTHROPIC_API_KEY" in st.secrets:
        os.environ.setdefault("ANTHROPIC_API_KEY", st.secrets["ANTHROPIC_API_KEY"])
except Exception:
    pass


@st.cache_resource
def get_client() -> LLMClient:
    return LLMClient()


client = get_client()


def sample_text(lab: str) -> str:
    return (ROOT / "data" / "sample_quotes" / f"supplier_{lab.lower()}.txt").read_text(encoding="utf-8")


def reset_samples():
    for lab in LABELS:
        st.session_state[f"q_{lab}"] = sample_text(lab)
    st.session_state["extractions"] = {}


for lab in LABELS:
    st.session_state.setdefault(f"q_{lab}", sample_text(lab))
st.session_state.setdefault("extractions", {})
st.session_state.setdefault("narratives", {})
st.session_state.setdefault("buyer_df", pd.DataFrame(
    {f"Supplier {l}": [float(DEMO_BUYER_ESTIMATES[l][k]) for k, _, _ in BUYER_FIELDS] for l in LABELS},
    index=[f"{label} [{unit}]" for _, label, unit in BUYER_FIELDS]))

# ------------------------------------------------------------------ sidebar: evaluation setup
with st.sidebar:
    st.header("Evaluation setup")
    item = st.text_input("Item", "Mild steel fabricated bracket (sample)")
    unit = st.text_input("Unit of measure", "piece")
    demand = st.number_input("Annual demand (units/yr)", 1.0, value=120000.0, step=1000.0)
    horizon = st.slider("Horizon (years)", 1, 5, 3)
    gst = st.number_input("GST rate (%)", 0.0, 100.0, 18.0) / 100
    itc = st.number_input("GST input credit recoverable (%)", 0.0, 100.0, 100.0) / 100
    coc = st.number_input("Cost of capital (%/yr)", 0.0, 100.0, 11.0) / 100
    storage = st.number_input("Storage cost (% of stock value/yr)", 0.0, 100.0, 4.0) / 100
    obs = st.number_input("Obsolescence & insurance (% of stock/yr)", 0.0, 100.0, 2.0) / 100
    infl = st.number_input("General inflation (%/yr)", 0.0, 100.0, 5.0) / 100
    disc = st.number_input("Discount rate for NPV (%/yr)", 0.0, 100.0, 0.0) / 100
    setup = EvalSetup(item=item, unit=unit, annual_demand=demand, horizon_years=horizon, gst_rate=gst,
                      itc_recoverable=itc, cost_of_capital=coc, storage_pct=storage, obsolescence_pct=obs,
                      general_inflation=infl, discount_rate=disc)
    st.divider()
    if client.mock:
        st.warning("**Mock mode** (no API key). The 3 bundled sample quotes use hand-written fixtures and the verdict "
                   "is a deterministic template. Set `ANTHROPIC_API_KEY` for live extraction of your own quotes.")
        prompt_version = "v2"
    else:
        st.success(f"Live mode: {client.model}")
        prompt_version = st.selectbox("Extraction prompt", ["v2", "v1"], help="v1 = zero-shot, v2 = rules + few-shot")

st.title("TCO Copilot")
st.caption("Paste supplier quotes \u2192 AI extracts and flags gaps \u2192 deterministic engine computes Total Cost of Ownership "
           "\u2192 AI explains the ranking. The LLM never does arithmetic.")
tab1, tab2, tab3 = st.tabs(["1 \u00b7 Quotes \u2192 fields", "2 \u00b7 Buyer estimates", "3 \u00b7 TCO & verdict"])

# ------------------------------------------------------------------ tab 1: quotes -> extraction
with tab1:
    cols = st.columns(3)
    for lab, col in zip(LABELS, cols):
        col.text_area(f"Supplier {lab} quote", key=f"q_{lab}", height=250)
    b1, b2, _ = st.columns([1, 1, 4])
    b2.button("Reset to samples", on_click=reset_samples)
    if b1.button("Extract fields", type="primary"):
        st.session_state["extractions"] = {}
        for lab in LABELS:
            text = st.session_state[f"q_{lab}"].strip()
            if not text:
                continue
            with st.spinner(f"Extracting Supplier {lab}..."):
                try:
                    st.session_state["extractions"][lab] = extract_quote(client, text, item=item, unit=unit, version=prompt_version)
                except ExtractionError as e:
                    st.error(f"Supplier {lab}: {e}")

    for lab, out in st.session_state["extractions"].items():
        ext = out.extraction
        flagged = ext.flagged()
        with st.expander(f"Supplier {lab}: {len(ext.fields) - len(flagged)} fields found, {len(flagged)} flagged", expanded=True):
            rows = []
            for spec in QUOTE_FIELDS:
                fr = ext.fields[spec.name]
                warn = "  \u26A0 evidence not found in quote" if fr.evidence_verified is False else ""
                rows.append({"field": spec.name, "value": "" if fr.value is None else str(fr.value),
                             "status": EMOJI[fr.status], "evidence": fr.evidence + warn, "clarifying question": fr.question})
            edited = st.data_editor(pd.DataFrame(rows), key=f"ed_{lab}", hide_index=True, width="stretch",
                                    disabled=["field", "status", "evidence", "clarifying question"])
            st.session_state[f"edited_{lab}"] = edited
            meta = f"source: {out.source}"
            if out.source == "llm":
                meta += f" \u00b7 {out.input_tokens} in / {out.output_tokens} out tokens \u00b7 {out.latency_s:.1f}s \u00b7 attempts: {out.attempts}"
            st.caption(meta + ". Edit the `value` column to answer a question or fix a field.")


def apply_edits(lab: str) -> QuoteExtraction:
    orig = st.session_state["extractions"][lab].extraction
    df = st.session_state.get(f"edited_{lab}")
    if df is None:
        return orig
    fields = dict(orig.fields)
    for _, row in df.iterrows():
        name, text = row["field"], str(row["value"]).strip()
        old = fields[name]
        old_text = "" if old.value is None else str(old.value)
        kind = FIELD_BY_NAME[name].kind
        try:
            same = (text == old_text) or (text and old.value is not None and _coerce(kind, text) == _coerce(kind, old.value))
            if same:
                continue
            if text == "":
                fields[name] = FieldResult(status="missing", question=old.question)
            else:
                fields[name] = FieldResult(value=_coerce(kind, text), status="stated", evidence="edited by you")
        except ValueError:
            st.warning(f"Supplier {lab}: could not read '{text}' for {name}; kept the original.")
    return QuoteExtraction(fields=fields, notes=orig.notes)


# ------------------------------------------------------------------ tab 2: buyer estimates
with tab2:
    st.markdown("Only **you** know these (rejection history, delivery record, risk estimates, order plan). "
                "The AI never extracts or guesses them. Values below are the workbook's illustrative samples: replace them with your own.")
    st.session_state["buyer_df"] = st.data_editor(st.session_state["buyer_df"], key="buyer_editor", width="stretch", height=600)

# ------------------------------------------------------------------ tab 3: results
with tab3:
    if not st.session_state["extractions"]:
        st.info("Run **Extract fields** in tab 1 first.")
        st.stop()
    accept = st.checkbox("Proceed with default assumptions for blocking gaps (they will be listed below)", value=False)
    sups, assumptions, blocked, names = [], [], {}, set()
    bdf = st.session_state["buyer_df"]
    for lab in st.session_state["extractions"]:
        est = {k: float(bdf.loc[f"{label} [{u}]", f"Supplier {lab}"]) for k, label, u in BUYER_FIELDS}
        try:
            inp, a = build_inputs(apply_edits(lab), est, setup, label=f"Supplier {lab}", accept_defaults=accept)
        except MissingInputs as e:
            blocked[lab] = e
            continue
        if inp.name in names:
            inp = inp.model_copy(update={"name": f"{inp.name} ({lab})"})
        names.add(inp.name)
        sups.append(inp)
        assumptions += a
    for lab, e in blocked.items():
        st.warning(f"**Supplier {lab} cannot be costed yet.** Answer these in tab 1 (edit the value), or tick the box above:\n\n"
                   + "\n".join(f"- {q}" for q in e.questions.values()))
    if len(sups) < 2:
        st.info("Need at least two costable suppliers to compare.")
        st.stop()

    mult = st.select_slider("Sensitivity: multiply estimate-driven costs (quality, after-sales, risk)",
                            options=[0.5, 0.75, 1.0, 1.5, 2.0], value=1.0)
    comp = compare(setup, sups, qrs_multiplier=mult)
    best = min(comp.suppliers, key=lambda r: r.per_unit)
    m1, m2, m3 = st.columns(3)
    m1.metric(f"Lowest {horizon}-year TCO per unit", best.name, f"\u20b9{best.per_unit:,.2f}", delta_color="off")
    m2.metric("Gap to runner-up", f"{comp.top_two_gap_pct:.2f}%")
    m3.metric("Estimate multiplier", f"{mult}x")
    if comp.close_call:
        st.warning("**Close call:** the top two are within 1% on cost. Decide on quality record, capacity and reliability.")

    table = pd.DataFrame([{"Supplier": r.name, "Rank": r.rank, "Total TCO": format_inr(r.total), "TCO / unit (\u20b9)": round(r.per_unit, 2),
                           "Price / unit, net of GST (\u20b9)": round(r.base_price_per_unit, 2),
                           "Hidden cost over price": f"{r.hidden_cost_pct * 100:.2f}%"} for r in sorted(comp.suppliers, key=lambda r: r.rank)])
    st.dataframe(table, hide_index=True, width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Hidden cost by bucket (excluding purchase price), \u20b9**")
        st.bar_chart(pd.DataFrame({r.name: {b: r.buckets[b] for b in BUCKETS[1:]} for r in comp.suppliers}), stack=False)
    with c2:
        st.markdown("**Cumulative TCO above the cheapest supplier that year, \u20b9**")
        cum = pd.DataFrame({r.name: r.cumulative for r in comp.suppliers}, index=[f"Year {y}" for y in range(1, 6)])
        st.line_chart(cum.sub(cum.min(axis=1), axis=0))

    st.markdown("**Does the winner change with the horizon?**")
    rbh = rank_by_horizon(setup, sups, mult)
    st.dataframe(pd.DataFrame([{"Horizon": f"{h} year{'s' if h > 1 else ''}", **{f"#{i + 1}": n for i, n in enumerate(names_)}}
                               for h, names_ in rbh.items()]), hide_index=True, width="stretch")

    st.markdown("### AI verdict")
    facts = build_facts(comp, sups, assumptions)
    key = json.dumps(facts, sort_keys=True)
    if st.button("Generate verdict"):
        with st.spinner("Writing verdict..."):
            st.session_state["narratives"][key] = explain(client, facts)
    nar = st.session_state["narratives"].get(key)
    if nar:
        st.markdown(nar.text)
        st.caption(f"Source: {nar.source}" + (f" \u00b7 {nar.input_tokens} in / {nar.output_tokens} out tokens" if nar.source == "llm" else "")
                   + (f" \u00b7 {nar.note}" if nar.note else "") + " \u00b7 Every number in the verdict is checked against the engine output.")

    st.markdown("### Assumptions (inputs that did not come from a stated quote)")
    st.dataframe(pd.DataFrame(assumptions) if assumptions else pd.DataFrame([{"note": "None: every engine input came from a stated quote field."}]),
                 hide_index=True, width="stretch")
    with st.expander("Audit trail: every cost factor, horizon value (\u20b9)"):
        st.dataframe(pd.DataFrame([{"ID": fid, "Bucket": b, "Factor": n, **{r.name: round(r.horizon[fid]) for r in comp.suppliers}}
                                   for fid, b, n, _ in FACTORS]), hide_index=True, width="stretch")
