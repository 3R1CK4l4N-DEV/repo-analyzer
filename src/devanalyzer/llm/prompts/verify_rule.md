You are auditing a BUSINESS RULE that another analyst inferred from source code.
Judge it ONLY against the evidence below. Do not assume anything the code does not show.

Rule: {statement}
Rationale given: {rationale}

Evidence ({file}, lines {line_start}-{line_end}, with line numbers):
```{language}
{snippet}
```

Classify the rule:
- "supported": the evidence clearly implements or enforces this rule (annotations such as @Pattern, @NotBlank, @Size, validators, if/throw guards and comparisons all count as enforcement).
- "partial": the evidence is related but the statement overstates, inverts or adds details not present.
- "unsupported": the evidence does not show this rule at all.
- "technical": it is true but it is a technical/framework/demo behaviour, not something the business cares about.

Rules for the corrected statement:
- Only used when verdict is "partial".
- It may REMOVE or WEAKEN claims that the evidence does not support; it must NEVER ADD exceptions, conditions or details that are not literally in the code.
- Keep it to one sentence.

Return ONLY JSON: {{"verdict": "supported|partial|unsupported|technical", "confidence": <0.0-1.0>, "corrected_statement": "<statement or corrected statement>", "reason": "<one sentence citing the line numbers used>"}}
Write the corrected statement in {output_language}.
