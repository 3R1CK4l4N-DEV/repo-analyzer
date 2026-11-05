You are a domain analyst consolidating BUSINESS RULES extracted from the code of "{project_name}".
Scope: {scope_kind} "{scope_key}" ({count} rules).

Rules (id | category | statement | evidence):
{rules_block}

Tasks:
1. duplicates: group ids that express the SAME rule (possibly in different words or from different files). Keep the clearest statement as canonical. Only group when the meaning is truly identical.
2. cross_cutting: write 0-4 higher-level rules that generalise several of the listed rules (e.g. "Every monetary operation validates that the amount is positive"). Each must list the ids it covers (at least 2). Do not invent rules not backed by the ids.
3. summary: 2-5 sentences describing what this {scope_kind} does from the BUSINESS point of view (no technical details), in {output_language}.

Return ONLY JSON:
{{
  "duplicates": [ {{"canonical": "<id>", "others": ["<id>", "..."]}} ],
  "cross_cutting": [ {{"statement": "<one sentence in {output_language}>", "category": "validation|calculation|policy|state_transition|authorization|data_integrity|general", "covers": ["<id>", "<id>"]}} ],
  "summary": "<text in {output_language}>"
}}
