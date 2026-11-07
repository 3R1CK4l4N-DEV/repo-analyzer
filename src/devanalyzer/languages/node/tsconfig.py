"""Import aliases from tsconfig.json / jsconfig.json (`compilerOptions.baseUrl` and `paths`).

An alias is (import prefix, project-relative directory): `"@/*": ["./*"]` -> ("@/", ""),
`"@app/*": ["src/app/*"]` -> ("@app/", "src/app/"). `baseUrl` alone becomes the catch-all ("", "<baseUrl>/").
"""

from __future__ import annotations

import json
import posixpath
import re

from devanalyzer.core.models import SourceFile

CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")


def _strip_comments(text: str) -> str:
    """Remove `//` and `/* */` comments outside string literals (`"@/*"` and `"**/*.ts"` are not comments)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" and end + 1 < n else 1
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i == -1 else i
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _loads_tolerant(text: str) -> dict:
    """tsconfig allows comments and trailing commas, which `json` rejects."""
    text = re.sub(r",\s*([}\]])", r"\1", _strip_comments(text))
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def path_aliases(files: list[SourceFile]) -> list[tuple[str, str]]:
    """Aliases of the top-most tsconfig/jsconfig (the one with the fewest path segments); longest prefixes first."""
    configs = [f for f in files if f.name in CONFIG_NAMES and "node_modules" not in f.rel_path]
    if not configs:
        return []
    config = min(configs, key=lambda f: (f.rel_path.count("/"), f.name != "tsconfig.json"))
    options = _loads_tolerant(config.content).get("compilerOptions") or {}
    config_dir = posixpath.dirname(config.rel_path)
    base_url = options.get("baseUrl")
    # Without `baseUrl`, `paths` are relative to the tsconfig and bare imports are packages, never project files.
    base_dir = posixpath.normpath(posixpath.join(config_dir, str(base_url) if isinstance(base_url, str) and base_url else "."))
    base_dir = "" if base_dir == "." else base_dir
    aliases: list[tuple[str, str]] = []
    for pattern, targets in (options.get("paths") or {}).items():
        if not isinstance(targets, list) or not targets or not isinstance(targets[0], str):
            continue
        target = posixpath.normpath(posixpath.join(base_dir, targets[0].removesuffix("*")))
        target = "" if target == "." else target.rstrip("/") + "/"
        prefix = pattern.removesuffix("*")
        aliases.append((prefix, target))
    if isinstance(base_url, str) and base_url:
        aliases.append(("", base_dir + "/" if base_dir else ""))
    return sorted(aliases, key=lambda a: -len(a[0]))
