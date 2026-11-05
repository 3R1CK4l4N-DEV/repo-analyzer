from __future__ import annotations

import fnmatch
from pathlib import Path

from devanalyzer.config import Settings, get_settings
from devanalyzer.core.models import EXTENSION_LANGUAGE, SourceFile

BINARY_SNIFF_BYTES = 1024


class GitIgnore:
    """Minimal .gitignore matcher: supports simple globs, directory rules and negations."""

    def __init__(self, patterns: list[str]):
        self.rules: list[tuple[str, bool, bool]] = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.strip("/")
            self.rules.append((line, dir_only, negate))

    @classmethod
    def from_dir(cls, root: Path) -> GitIgnore:
        f = root / ".gitignore"
        if not f.is_file():
            return cls([])
        return cls(f.read_text(encoding="utf-8", errors="replace").splitlines())

    def ignored(self, rel_path: str, is_dir: bool) -> bool:
        rel = rel_path.replace("\\", "/")
        parts = rel.split("/")
        result = False
        for pattern, dir_only, negate in self.rules:
            if dir_only and not is_dir and not any(fnmatch.fnmatch(p, pattern) for p in parts[:-1]):
                continue
            matched = (
                fnmatch.fnmatch(rel, pattern)
                or fnmatch.fnmatch(parts[-1], pattern)
                or any(fnmatch.fnmatch(p, pattern) for p in parts)
                or ("/" in pattern and fnmatch.fnmatch(rel, pattern + "/*"))
            )
            if matched:
                result = not negate
        return result


class ProjectReader:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def read(self, path: str | Path) -> list[SourceFile]:
        root = Path(path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Path {root} not found")
        if root.is_file():
            return [f for f in [self._to_source_file(root, root.parent)] if f]

        gitignore = GitIgnore.from_dir(root)
        max_bytes = self.settings.max_file_size_kb * 1024
        files: list[SourceFile] = []
        for file in self._walk(root, root, gitignore):
            if file.suffix.lower() not in self.settings.supported_extensions:
                continue
            if file.stat().st_size > max_bytes:
                continue
            sf = self._to_source_file(file, root)
            if sf is not None:
                files.append(sf)
        files.sort(key=lambda f: f.rel_path)
        return files

    def _walk(self, current: Path, root: Path, gitignore: GitIgnore):
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except PermissionError:
            return
        for entry in entries:
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                if entry.name in self.settings.ignore_dirs or gitignore.ignored(rel, True):
                    continue
                yield from self._walk(entry, root, gitignore)
            elif entry.is_file() and not gitignore.ignored(rel, False):
                yield entry

    def _to_source_file(self, file: Path, root: Path) -> SourceFile | None:
        raw = file.read_bytes()
        is_utf16 = raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        if not is_utf16 and b"\x00" in raw[:BINARY_SNIFF_BYTES]:
            return None
        content = self._decode(raw)
        return SourceFile(
            name=file.name,
            path=str(file),
            rel_path=file.relative_to(root).as_posix(),
            extension=file.suffix.lower(),
            content=content,
            language=EXTENSION_LANGUAGE.get(file.suffix.lower()),
            size=len(raw),
        )

    @staticmethod
    def _decode(raw: bytes) -> str:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16", errors="replace")
        return raw.decode("utf-8-sig", errors="replace")
