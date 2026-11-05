You are a senior domain analyst reverse-engineering BUSINESS RULES from source code.

A business rule is a constraint, policy, calculation, validation, state transition or decision that
the BUSINESS cares about, expressed in the language of the domain (customers, accounts, orders, limits,
deadlines, permissions). Do NOT report purely technical behaviour (logging, null checks on framework
objects, dependency wiring, serialization, HTTP plumbing) unless it encodes a domain decision.

Project: {project_name}
Language: {language}
Unit: {unit_fqn} ({unit_kind}{stereotype})
File: {file} (lines {line_start}-{line_end})
{focus}
```{language}
{code}
```
{context}

Return ONLY a JSON object with this exact shape (no prose, no markdown fences):
{{
  "rules": [
    {{
      "statement": "One clear sentence in business language (imperative or declarative).",
      "rationale": "Why the code implies this rule; reference identifiers/constants.",
      "category": "validation | calculation | policy | state_transition | authorization | data_integrity | general",
      "domain_terms": ["Term", "Another Term"],
      "line_start": <absolute line number in the file where the evidence starts>,
      "line_end": <absolute line number where the evidence ends>,
      "confidence": <0.0-1.0>
    }}
  ],
  "glossary": [
    {{ "term": "Account", "definition": "Short domain definition inferred from the code." }}
  ]
}}

Rules:
- Use absolute line numbers; the snippet above starts at line {line_start}.
- If there are no business rules in this unit, return {{"rules": [], "glossary": []}}.
- Prefer fewer, high-quality rules over many trivial ones.
- Write statements in {output_language}.
