"""Source masking for static heuristics, preserving line numbers and string literals."""

from __future__ import annotations

import re

from tree_sitter_language_pack import get_parser

from devanalyzer.core.models import Language


def without_comments(content: str, language: Language | None, extension: str = "") -> str:
    """Mask syntax-tree comments rather than deleting URL fragments or quoted comment markers."""
    grammar = "tsx" if extension == ".tsx" else language.value if language else {".yml": "yaml", ".yaml": "yaml", ".json": "json"}.get(extension)
    if grammar is None:
        # Java properties comments start at the beginning of a logical line.
        return re.sub(r"(?m)^[ \t]*[#!][^\r\n]*", lambda m: " " * len(m.group()), content) if extension == ".properties" else content
    raw = content.encode("utf-8")
    tree = get_parser(grammar).parse(raw)
    masked = bytearray(raw)
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        comment = node.type in ("comment", "line_comment", "block_comment", "multiline_comment")
        # A standalone Python string is documentation, not a producer/consumer expression.
        docstring = language == Language.PYTHON and (
            (node.type in ("string", "concatenated_string") and node.parent is not None and node.parent.type in ("module", "block"))
            or (node.type == "expression_statement" and node.named_children and all(c.type in ("string", "concatenated_string") for c in node.named_children))
        )
        if comment or docstring:
            masked[node.start_byte : node.end_byte] = re.sub(rb"[^\r\n]", b" ", raw[node.start_byte : node.end_byte])
        else:
            stack.extend(node.children)
    return masked.decode("utf-8")
