from devanalyzer.config import Settings
from devanalyzer.core.models import Language
from devanalyzer.core.reader import GitIgnore, ProjectReader


def test_reads_supported_files_with_rel_path_and_language(reader, fixtures):
    files = reader.read(fixtures / "java_spring")
    rel_paths = {f.rel_path for f in files}
    assert "pom.xml" in rel_paths
    assert "src/main/java/com/acme/bank/model/Account.java" in rel_paths
    java = next(f for f in files if f.name == "Account.java")
    assert java.language == Language.JAVA
    assert java.size > 0 and java.loc > 0


def test_skips_ignored_dirs_and_large_files(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("ignored")
    (tmp_path / "keep.js").write_text("ok")
    (tmp_path / "big.js").write_text("x" * 2048)
    settings = Settings(_env_file=None, max_file_size_kb=1)
    names = {f.name for f in ProjectReader(settings).read(tmp_path)}
    assert names == {"keep.js"}


def test_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("generated/\n*.log\n!important.log\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("x")
    settings = Settings(_env_file=None, supported_extensions={".py", ".log"})
    names = {f.name for f in ProjectReader(settings).read(tmp_path)}
    assert names == {"b.py"}


def test_gitignore_negation():
    gi = GitIgnore(["*.log", "!important.log"])
    assert gi.ignored("debug.log", False)
    assert not gi.ignored("important.log", False)


def test_skips_binary_and_decodes_utf16(tmp_path):
    (tmp_path / "bin.txt").write_bytes(b"\x00\x01\x02")
    (tmp_path / "u16.txt").write_bytes("hola".encode("utf-16"))
    files = ProjectReader(Settings(_env_file=None)).read(tmp_path)
    assert [f.name for f in files] == ["u16.txt"]
    assert files[0].content == "hola"
