Extract the following fields from the supplier quote and return JSON only. Use null for anything not stated.

Fields: supplier_name, location, certification, price_unit, quoted_unit_price, price_includes_gst (true/false),
annual_price_escalation (fraction), moq, lead_time_days, payment_terms_days, freight_included (true/false),
freight_per_order, packaging_included (true/false), packaging_per_unit, tooling_cost.

Return a flat JSON object: {"field_name": value, ...}
