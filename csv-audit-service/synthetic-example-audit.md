# CSV quality audit

5 data rows; 4 columns.

## Findings

- {"line": 2, "column": "name", "type": "outer_whitespace"}
- {"line": 3, "column": "category", "type": "blank_cell"}
- {"line": 4, "type": "duplicate_normalized_row", "first_line": 2, "action": "retained"}
- {"line": 5, "column": "name", "type": "spreadsheet_formula_like", "action": "flag only; do not open untrusted CSV in spreadsheet"}
- {"line": 6, "type": "row_width", "expected": 4, "actual": 3}

## Limits

Outer whitespace trimmed only; all data rows and values otherwise retained. Width errors and formula-like cells remain flagged. No schema, type, missing-value or duplicate decisions invented.
This is structural quality evidence, not business validation. Customer-sensitive or regulated data is excluded from this introductory service.
