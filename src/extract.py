"""Quote -> validated structured fields.

Pipeline: LLM (JSON) -> parse -> coerce types -> verify evidence against the quote -> deterministic guards.
Every field carries status (stated | inferred | missing | conflict), the exact evidence span, and a
clarifying question when the data is missing or contradictory. Missing is a valid answer.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .llm import LLMClient, LLMError
from .schema import FIELD_BY_NAME, QUOTE_FIELDS, FieldResult, QuoteExtraction

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
FIXTURES = ROOT / "data" / "fixtures" / "extraction_fixtures.json"

UNIT_SYNONYMS = {"piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "nos": "piece", "no": "piece",
                 "number": "piece", "unit": "piece", "each": "piece", "kg": "kg", "kgs": "kg", "kilogram": "kg",
                 "tonne": "tonne", "ton": "tonne", "mt": "tonne", "metre": "metre", "meter": "metre", "mtr": "metre",
                 "set": "set", "litre": "litre", "liter": "litre"}


class ExtractionError(RuntimeError):
    pass


@dataclass
class ExtractionOutcome:
    extraction: QuoteExtraction
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    attempts: int = 0
    source: str = "llm"                  # llm | fixture
    raw: str = ""
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- helpers
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u20b9]+", " ", s.lower()).strip()


def quote_key(quote: str) -> str:
    return hashlib.sha256(_norm(quote).encode()).hexdigest()[:16]


def normalise_unit(u: Any) -> str | None:
    if not isinstance(u, str):
        return None
    return UNIT_SYNONYMS.get(u.strip().lower().rstrip("."))


def _coerce(kind: str, v: Any) -> Any:
    if v is None or v == "":
        return None
    if kind == "bool":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in ("true", "yes", "y"):
            return True
        if isinstance(v, str) and v.strip().lower() in ("false", "no", "n"):
            return False
        raise ValueError(f"not a boolean: {v!r}")
    if kind in ("num", "frac"):
        if isinstance(v, bool):
            raise ValueError(f"not a number: {v!r}")
        if isinstance(v, str):
            v = float(re.sub(r"[^\d.\-]", "", v.replace(",", "")) or "nan")
        v = float(v)
        if v != v:
            raise ValueError("not a number")
        if kind == "frac" and v > 1:
            v = v / 100.0            # model wrote 5 instead of 0.05
        return v
    return str(v).strip()


def normalise_extraction(data: dict, quote: str, buyer_unit: str = "piece") -> QuoteExtraction:
    """Accepts the nested v2 format or a flat {field: value} object (v1). Raises ValueError on bad structure."""
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    raw_fields = data.get("fields") if isinstance(data.get("fields"), dict) else data
    nq = _norm(quote)
    out: dict[str, FieldResult] = {}
    for spec in QUOTE_FIELDS:
        item = raw_fields.get(spec.name)
        if isinstance(item, dict) and ("status" in item or "value" in item):
            value, status = item.get("value"), item.get("status") or ("stated" if item.get("value") is not None else "missing")
            evidence, question = item.get("evidence") or "", item.get("question") or ""
        else:                                    # flat format
            value, status, evidence, question = item, ("stated" if item is not None else "missing"), "", ""
        if status not in ("stated", "inferred", "missing", "conflict"):
            raise ValueError(f"{spec.name}: invalid status {status!r}")
        value = _coerce(spec.kind, value)
        if status in ("stated", "inferred") and value is None:
            status = "missing"
        if status in ("missing", "conflict"):
            value = None
        verified = None
        if status in ("stated", "inferred") and evidence:
            verified = _norm(str(evidence)) in nq
        out[spec.name] = FieldResult(value=value, status=status, evidence=str(evidence), question=str(question),
                                     evidence_verified=verified)
    ext = QuoteExtraction(fields=out, notes=str(data.get("notes", "") or ""))
    return apply_guards(ext, buyer_unit)


def apply_guards(ext: QuoteExtraction, buyer_unit: str) -> QuoteExtraction:
    """Deterministic safety nets that do not depend on the model following the prompt."""
    pu = ext.fields["price_unit"]
    got, want = normalise_unit(pu.value), normalise_unit(buyer_unit)
    price = ext.fields["quoted_unit_price"]
    if got and want and got != want and price.status in ("stated", "inferred"):
        ext.fields["quoted_unit_price"] = FieldResult(
            value=None, status="conflict", evidence=price.evidence, evidence_verified=price.evidence_verified,
            question=f"Price is quoted per {got} but you compare per {want}. What is the price per {want}?")
    return ext


def parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ValueError("no JSON object found in model output")
    return json.loads(t[i:j + 1])


# ----------------------------------------------------------------------------- prompts
def load_system_prompt(version: str) -> str:
    return (PROMPTS / f"extract_{version}.md").read_text(encoding="utf-8")


def _user_message(quote: str, item: str, unit: str) -> str:
    return (f"Buyer context: item = {item or 'n/a'}; buyer's unit of measure = {unit}.\n\n"
            f"SUPPLIER QUOTE (verbatim):\n<<<\n{quote.strip()}\n>>>\n\nReturn the JSON object only.")


def build_messages(quote: str, item: str, unit: str, version: str) -> list[dict]:
    msgs: list[dict] = []
    if version == "v2":
        for ex in json.loads((PROMPTS / "fewshot.json").read_text(encoding="utf-8")):
            msgs.append({"role": "user", "content": _user_message(ex["quote"], item, ex.get("buyer_unit", unit))})
            msgs.append({"role": "assistant", "content": json.dumps(ex["output"], ensure_ascii=False)})
    msgs.append({"role": "user", "content": _user_message(quote, item, unit)})
    return msgs


# ----------------------------------------------------------------------------- main entry
def _load_fixtures() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8")) if FIXTURES.exists() else {}


def extract_quote(client: LLMClient, quote: str, *, item: str = "", unit: str = "piece",
                  version: str = "v2") -> ExtractionOutcome:
    quote = quote.strip()
    if not quote:
        raise ExtractionError("Empty quote.")
    if client.mock:
        fx = _load_fixtures().get(quote_key(quote))
        if fx is None:
            raise ExtractionError("Mock mode only knows the 3 bundled sample quotes. "
                                  "Set ANTHROPIC_API_KEY to extract from your own text.")
        return ExtractionOutcome(extraction=normalise_extraction(fx, quote, unit), source="fixture", attempts=0)

    system = load_system_prompt(version)
    messages = build_messages(quote, item, unit, version)
    tin = tout = 0
    lat = 0.0
    last_err = ""
    for attempt in (1, 2):                       # one retry on invalid output
        try:
            res = client.complete(system, messages, max_tokens=1800, temperature=0.0)
        except LLMError as e:
            raise ExtractionError(str(e)) from e
        tin, tout, lat = tin + res.input_tokens, tout + res.output_tokens, lat + res.latency_s
        try:
            ext = normalise_extraction(parse_json(res.text), quote, unit)
            return ExtractionOutcome(extraction=ext, input_tokens=tin, output_tokens=tout, latency_s=lat,
                                     attempts=attempt, raw=res.text)
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            last_err = str(e)
            messages = messages + [{"role": "assistant", "content": res.text},
                                   {"role": "user", "content": f"That output was invalid ({last_err}). "
                                    "Return the corrected JSON object only."}]
    raise ExtractionError(f"Model output was invalid after a retry: {last_err}")


def empty_extraction(reason: str = "") -> QuoteExtraction:
    return QuoteExtraction(fields={s.name: FieldResult(status="missing", question=reason) for s in QUOTE_FIELDS})
