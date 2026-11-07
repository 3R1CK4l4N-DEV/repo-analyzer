"""Database schema reconstruction from migrations and schema files.

Supported sources: plain SQL / Flyway (.sql), Liquibase (XML, YAML), Alembic (versions/*.py),
TypeORM migrations (SQL inside queryRunner.query(`...`)), Prisma (schema.prisma).
Statements are applied in file order so later ALTER/DROP statements update earlier tables.
"""

from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET

from devanalyzer.core.models import CodeUnit, CodeUnitKind, Column, ForeignKey, Language, SchemaInfo, SourceFile, Table

SQL_TYPES = r"[A-Za-z_]\w*(?:\s*\([^)]*\))?(?:\s+(?:UNSIGNED|PRECISION|VARYING|WITH\s+TIME\s+ZONE|WITHOUT\s+TIME\s+ZONE|WITH\s+LOCAL\s+TIME\s+ZONE))*"
IDENT = r'[`"\[]?([\w.]+)[`"\]]?'
CREATE_TABLE_START_RE = re.compile(rf"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{IDENT}\s*\(", re.I)
ALTER_ADD_FK_RE = re.compile(
    rf"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?{IDENT}\s+ADD\s+(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\(\s*{IDENT}\s*\)\s*REFERENCES\s+{IDENT}\s*(?:\(\s*{IDENT}\s*\))?",
    re.I,
)
ALTER_ADD_COL_RE = re.compile(
    rf"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?{IDENT}\s+ADD\s+(?:COLUMN\s+)?(?!CONSTRAINT|FOREIGN|PRIMARY|UNIQUE|INDEX|KEY){IDENT}\s+({SQL_TYPES})",
    re.I,
)
ALTER_DROP_COL_RE = re.compile(rf"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?{IDENT}\s+DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?{IDENT}", re.I)
DROP_TABLE_RE = re.compile(rf"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?{IDENT}", re.I)
RENAME_TABLE_RE = re.compile(rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?{IDENT}\s+RENAME\s+TO\s+{IDENT}", re.I)
INLINE_REF_RE = re.compile(rf"REFERENCES\s+{IDENT}\s*(?:\(\s*{IDENT}\s*\))?", re.I)
TABLE_FK_RE = re.compile(rf"FOREIGN\s+KEY\s*\(\s*{IDENT}\s*\)\s*REFERENCES\s+{IDENT}\s*(?:\(\s*{IDENT}\s*\))?", re.I)
TABLE_PK_RE = re.compile(r"PRIMARY\s+KEY\s*\(([^)]*)\)", re.I)
CONSTRAINT_START = re.compile(r"^\s*(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|INDEX|KEY|CHECK|EXCLUDE)\b", re.I)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def _split_columns(body: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _clean(name: str) -> str:
    return name.strip().strip('`"[]').split(".")[-1].lower()


def _balanced_body(text: str, open_idx: int) -> str:
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i]
    return text[open_idx + 1 :]


def parse_sql(sql: str, source_file: str, tool: str = "sql", tables: dict[str, Table] | None = None) -> list[Table]:
    """Parse DDL statements in order; `tables` carries state across migration files (returns tables touched here)."""
    text = _strip_comments(sql)
    tables = tables if tables is not None else {}
    events: list[tuple[int, str, object]] = [(m.start(), "create", (m, _balanced_body(text, m.end() - 1))) for m in CREATE_TABLE_START_RE.finditer(text)]
    for kind, rx in (
        ("fk", ALTER_ADD_FK_RE),
        ("addcol", ALTER_ADD_COL_RE),
        ("dropcol", ALTER_DROP_COL_RE),
        ("drop", DROP_TABLE_RE),
        ("rename", RENAME_TABLE_RE),
    ):
        events += [(m.start(), kind, m) for m in rx.finditer(text)]
    for pos, kind, m in sorted(events, key=lambda e: e[0]):
        line = text.count("\n", 0, pos) + 1
        if kind == "create":
            m, body = m
            name = _clean(m.group(1))
            table = Table(name=name, source_file=source_file, line=line, tool=tool)
            for raw in _split_columns(body):
                if CONSTRAINT_START.match(raw):
                    if fk := TABLE_FK_RE.search(raw):
                        table.foreign_keys.append(
                            ForeignKey(column=_clean(fk.group(1)), ref_table=_clean(fk.group(2)), ref_column=_clean(fk.group(3)) if fk.group(3) else None)
                        )
                    if pk := TABLE_PK_RE.search(raw):
                        for col in pk.group(1).split(","):
                            if c := table.column(_clean(col)):
                                c.primary_key = True
                    continue
                cm = re.match(rf"{IDENT}\s+({SQL_TYPES})", raw, re.I)
                if not cm:
                    continue
                col_name = _clean(cm.group(1))
                col_type = cm.group(2).strip().split(" NOT")[0].split(" PRIMARY")[0].split(" DEFAULT")[0].split(" NULL")[0].split(" REFERENCES")[0].strip()
                table.columns.append(
                    Column(
                        name=col_name,
                        type=col_type.upper(),
                        nullable="NOT NULL" not in raw.upper() and "PRIMARY KEY" not in raw.upper(),
                        primary_key="PRIMARY KEY" in raw.upper(),
                    )
                )
                if ref := INLINE_REF_RE.search(raw):
                    table.foreign_keys.append(
                        ForeignKey(column=col_name, ref_table=_clean(ref.group(1)), ref_column=_clean(ref.group(2)) if ref.group(2) else None)
                    )
            tables[name] = table
        elif kind == "fk" and (t := tables.get(_clean(m.group(1)))):
            t.foreign_keys.append(ForeignKey(column=_clean(m.group(2)), ref_table=_clean(m.group(3)), ref_column=_clean(m.group(4)) if m.group(4) else None))
        elif kind == "addcol" and (t := tables.get(_clean(m.group(1)))):
            if not t.column(_clean(m.group(2))):
                t.columns.append(Column(name=_clean(m.group(2)), type=m.group(3).strip().upper()))
        elif kind == "dropcol" and (t := tables.get(_clean(m.group(1)))):
            t.columns = [c for c in t.columns if c.name != _clean(m.group(2))]
        elif kind == "drop":
            tables.pop(_clean(m.group(1)), None)
        elif kind == "rename" and (t := tables.pop(_clean(m.group(1)), None)):
            t.name = _clean(m.group(2))
            tables[t.name] = t
    return list(tables.values())


# ---------------------------------------------------------------- Liquibase
def parse_liquibase_xml(content: str, source_file: str) -> list[Table]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    tables: dict[str, Table] = {}

    def local(tag: str) -> str:
        return tag.rpartition("}")[2]

    for el in root.iter():
        tag = local(el.tag)
        if tag == "createTable":
            name = _clean(el.get("tableName", ""))
            table = Table(name=name, source_file=source_file, tool="liquibase")
            for col in el:
                if local(col.tag) != "column":
                    continue
                constraints = next((c for c in col if local(c.tag) == "constraints"), None)
                pk = constraints is not None and constraints.get("primaryKey", "false").lower() == "true"
                nullable = not (constraints is not None and constraints.get("nullable", "true").lower() == "false") and not pk
                table.columns.append(Column(name=_clean(col.get("name", "")), type=(col.get("type") or "").upper(), nullable=nullable, primary_key=pk))
                if constraints is not None and (ref := constraints.get("references")):
                    rm = re.match(rf"{IDENT}\s*(?:\(\s*{IDENT}\s*\))?", ref)
                    if rm:
                        table.foreign_keys.append(
                            ForeignKey(
                                column=_clean(col.get("name", "")), ref_table=_clean(rm.group(1)), ref_column=_clean(rm.group(2)) if rm.group(2) else None
                            )
                        )
            tables[name] = table
        elif tag == "addForeignKeyConstraint" and (t := tables.get(_clean(el.get("baseTableName", "")))):
            t.foreign_keys.append(
                ForeignKey(
                    column=_clean(el.get("baseColumnNames", "")),
                    ref_table=_clean(el.get("referencedTableName", "")),
                    ref_column=_clean(el.get("referencedColumnNames", "")) or None,
                )
            )
        elif tag == "addColumn" and (t := tables.get(_clean(el.get("tableName", "")))):
            for col in el:
                if local(col.tag) == "column":
                    t.columns.append(Column(name=_clean(col.get("name", "")), type=(col.get("type") or "").upper()))
        elif tag == "dropTable":
            tables.pop(_clean(el.get("tableName", "")), None)
        elif tag == "sql" and el.text:
            for t in parse_sql(el.text, source_file, tool="liquibase"):
                tables[t.name] = t
    return list(tables.values())


def parse_liquibase_yaml(content: str, source_file: str) -> list[Table]:
    """Lightweight YAML changelog reader (no PyYAML): handles createTable/addForeignKeyConstraint/dropTable blocks."""
    tables: dict[str, Table] = {}
    current: Table | None = None
    col: Column | None = None
    mode = None
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("- createTable:") or line == "createTable:":
            mode, current, col = "create", None, None
        elif line.startswith("- addForeignKeyConstraint:") or line == "addForeignKeyConstraint:":
            mode, fk = "fk", {}
        elif line.startswith("- dropTable:") or line == "dropTable:":
            mode = "drop"
        elif line.startswith("- changeSet:"):
            mode = None
        if mode == "create":
            if m := re.match(r"tableName:\s*(\S+)", line):
                current = Table(name=_clean(m.group(1)), source_file=source_file, tool="liquibase")
                tables[current.name] = current
            elif current is not None and (m := re.match(r"-\s*column:\s*$", line)):
                col = None
            elif current is not None and (m := re.match(r"(?:-\s*)?name:\s*(\S+)", line)) and col is None:
                col = Column(name=_clean(m.group(1)))
                current.columns.append(col)
            elif col is not None and (m := re.match(r"type:\s*(\S+)", line)):
                col.type = m.group(1).upper()
            elif col is not None and re.match(r"primaryKey:\s*true", line):
                col.primary_key, col.nullable = True, False
            elif col is not None and re.match(r"nullable:\s*false", line):
                col.nullable = False
            elif col is not None and (m := re.match(r"references:\s*(\S+)", line)):
                rm = re.match(rf"{IDENT}\s*(?:\(\s*{IDENT}\s*\))?", m.group(1))
                if rm:
                    current.foreign_keys.append(
                        ForeignKey(column=col.name, ref_table=_clean(rm.group(1)), ref_column=_clean(rm.group(2)) if rm.group(2) else None)
                    )
            if re.match(r"-\s*column:", line):
                col = None
        elif mode == "fk":
            for key in ("baseTableName", "baseColumnNames", "referencedTableName", "referencedColumnNames"):
                if m := re.match(rf"{key}:\s*(\S+)", line):
                    fk[key] = _clean(m.group(1))
            if (
                len(fk) >= 3
                and (t := tables.get(fk.get("baseTableName", "")))
                and not any(f.column == fk.get("baseColumnNames") and f.ref_table == fk.get("referencedTableName") for f in t.foreign_keys)
            ):
                t.foreign_keys.append(
                    ForeignKey(column=fk.get("baseColumnNames", ""), ref_table=fk.get("referencedTableName", ""), ref_column=fk.get("referencedColumnNames"))
                )
        elif mode == "drop":
            if m := re.match(r"tableName:\s*(\S+)", line):
                tables.pop(_clean(m.group(1)), None)
    return list(tables.values())


# ---------------------------------------------------------------- Alembic
def parse_alembic(content: str, source_file: str, tables: dict[str, Table] | None = None) -> list[Table]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    tables = tables if tables is not None else {}

    def const(node: ast.expr) -> str | None:
        return str(node.value) if isinstance(node, ast.Constant) else None

    def column_from(call: ast.Call) -> tuple[Column, ForeignKey | None] | None:
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "Column") or not call.args:
            return None
        name = const(call.args[0])
        if not name:
            return None
        col = Column(name=_clean(name))
        fk = None
        for arg in call.args[1:]:
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "ForeignKey" and arg.args and (ref := const(arg.args[0])):
                rt, _, rc = ref.partition(".")
                fk = ForeignKey(column=col.name, ref_table=_clean(rt), ref_column=_clean(rc) or None)
            elif col.type is None:
                col.type = ast.unparse(arg).replace("sa.", "").upper()
        for kw in call.keywords:
            if kw.arg == "nullable" and isinstance(kw.value, ast.Constant):
                col.nullable = bool(kw.value.value)
            elif kw.arg == "primary_key" and isinstance(kw.value, ast.Constant) and kw.value.value:
                col.primary_key, col.nullable = True, False
        return col, fk

    # Only the upgrade path describes the resulting schema; downgrade() would undo it.
    upgrade = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
    for node in ast.walk(upgrade or tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or not isinstance(node.func.value, ast.Name)
            or node.func.value.id != "op"
        ):
            continue
        op = node.func.attr
        first = const(node.args[0]) if node.args else None
        if op == "create_table" and first:
            table = Table(name=_clean(first), source_file=source_file, line=node.lineno, tool="alembic")
            for arg in node.args[1:]:
                if isinstance(arg, ast.Call):
                    if (res := column_from(arg)) is not None:
                        table.columns.append(res[0])
                        if res[1]:
                            table.foreign_keys.append(res[1])
                    elif isinstance(arg.func, ast.Attribute) and arg.func.attr == "ForeignKeyConstraint" and len(arg.args) >= 2:
                        cols = [const(e) for e in getattr(arg.args[0], "elts", [])]
                        refs = [const(e) for e in getattr(arg.args[1], "elts", [])]
                        for c, r in zip(cols, refs):
                            if c and r:
                                rt, _, rc = r.partition(".")
                                table.foreign_keys.append(ForeignKey(column=_clean(c), ref_table=_clean(rt), ref_column=_clean(rc) or None))
                    elif isinstance(arg.func, ast.Attribute) and arg.func.attr == "PrimaryKeyConstraint":
                        for e in arg.args:
                            if (n := const(e)) and (c := table.column(n)):
                                c.primary_key, c.nullable = True, False
            tables[table.name] = table
        elif op == "add_column" and first and (t := tables.get(_clean(first))):
            for arg in node.args[1:]:
                if isinstance(arg, ast.Call) and (res := column_from(arg)) is not None:
                    t.columns.append(res[0])
                    if res[1]:
                        t.foreign_keys.append(res[1])
        elif op == "drop_column" and first and (t := tables.get(_clean(first))) and len(node.args) > 1 and (cn := const(node.args[1])):
            t.columns = [c for c in t.columns if c.name != _clean(cn)]
        elif op == "drop_table" and first:
            tables.pop(_clean(first), None)
        elif op == "create_foreign_key" and len(node.args) >= 5:
            src, ref = const(node.args[1]), const(node.args[2])
            cols = [const(e) for e in getattr(node.args[3], "elts", [])]
            refs = [const(e) for e in getattr(node.args[4], "elts", [])]
            if src and ref and (t := tables.get(_clean(src))):
                for c, r in zip(cols, refs):
                    if c:
                        t.foreign_keys.append(ForeignKey(column=_clean(c), ref_table=_clean(ref), ref_column=_clean(r) if r else None))
        elif op == "rename_table" and len(node.args) >= 2 and first and (new := const(node.args[1])) and (t := tables.pop(_clean(first), None)):
            t.name = _clean(new)
            tables[t.name] = t
    return list(tables.values())


# ---------------------------------------------------------------- Django migrations
DJANGO_SCALAR_FIELDS = ("ForeignKey", "OneToOneField", "ManyToManyField")


def _dotted_name(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def parse_django_migration(content: str, source_file: str, tables: dict[str, Table] | None = None, model_tables: dict[str, str] | None = None) -> list[Table]:
    """Apply `migrations.CreateModel/AddField/...` operations. Tables follow Django's `<app>_<model>` naming unless
    `db_table` is given; `model_tables` ("app.model" -> table) carries renames across migration files."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    tables = tables if tables is not None else {}
    model_tables = model_tables if model_tables is not None else {}
    parts = source_file.replace("\\", "/").split("/")
    app = parts[parts.index("migrations") - 1] if "migrations" in parts and parts.index("migrations") > 0 else "app"

    def const(node: ast.expr | None) -> str | None:
        return str(node.value) if isinstance(node, ast.Constant) and node.value is not None else None

    def kw(call: ast.Call, name: str, position: int | None = None) -> ast.expr | None:
        value = next((k.value for k in call.keywords if k.arg == name), None)
        return value if value is not None or position is None or len(call.args) <= position else call.args[position]

    def model_key(name: str, app_label: str = app) -> str:
        return f"{app_label}.{name}".lower()

    def table_of(name: str) -> Table | None:
        return tables.get(model_tables.get(model_key(name), f"{app}_{name}".lower()))

    def ref_table(to: ast.expr | None) -> str | None:
        if (value := const(to)) is not None:
            label, _, model = value.rpartition(".")
            key = model_key(model, label) if label else model_key(model)
            return model_tables.get(key, key.replace(".", "_"))
        return "auth_user" if _dotted_name(to).endswith("AUTH_USER_MODEL") else None

    def column_from(name: str, field: ast.expr) -> tuple[Column | None, ForeignKey | None]:
        if not isinstance(field, ast.Call):
            return None, None
        ftype = _dotted_name(field.func).rsplit(".", 1)[-1]
        if ftype == "ManyToManyField":
            return None, None  # join tables are implicit; not modelled
        pk = const(kw(field, "primary_key")) == "True"
        nullable = const(kw(field, "null")) == "True" and not pk
        if ftype in DJANGO_SCALAR_FIELDS:
            column = Column(name=f"{name}_id", type=ftype.upper(), nullable=nullable, primary_key=pk)
            target = ref_table(kw(field, "to", 0))
            return column, ForeignKey(column=column.name, ref_table=target, ref_column="id") if target else None
        return Column(name=const(kw(field, "db_column")) or name, type=ftype.removesuffix("Field").upper(), nullable=nullable, primary_key=pk), None

    operations: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "operations" for t in node.targets) and isinstance(node.value, ast.List):
            operations.extend(node.value.elts)
    for op in operations:
        if not isinstance(op, ast.Call):
            continue
        kind = _dotted_name(op.func).rsplit(".", 1)[-1]
        if kind == "CreateModel" and (name := const(kw(op, "name", 0))):
            options = kw(op, "options")
            db_table = None
            if isinstance(options, ast.Dict):
                db_table = next((const(v) for k, v in zip(options.keys, options.values) if const(k) == "db_table"), None)
            table_name = _clean(db_table) if db_table else f"{app}_{name}".lower()
            table = Table(name=table_name, source_file=source_file, line=op.lineno, tool="django")
            fields = kw(op, "fields", 1)
            for item in fields.elts if isinstance(fields, (ast.List, ast.Tuple)) else []:
                if isinstance(item, ast.Tuple) and len(item.elts) >= 2 and (field_name := const(item.elts[0])):
                    column, fk = column_from(field_name, item.elts[1])
                    if column is not None:
                        table.columns.append(column)
                    if fk is not None:
                        table.foreign_keys.append(fk)
            tables[table.name] = table
            model_tables[model_key(name)] = table.name
        elif kind in ("AddField", "AlterField") and (model := const(kw(op, "model_name", 0))) and (field_name := const(kw(op, "name", 1))):
            if (table := table_of(model)) is None:
                continue
            column, fk = column_from(field_name, kw(op, "field", 2))
            if column is None:
                continue
            table.columns = [c for c in table.columns if c.name not in (column.name, field_name)]
            table.foreign_keys = [f for f in table.foreign_keys if f.column not in (column.name, field_name)]
            table.columns.append(column)
            if fk is not None:
                table.foreign_keys.append(fk)
        elif kind == "RemoveField" and (model := const(kw(op, "model_name", 0))) and (field_name := const(kw(op, "name", 1))):
            if (table := table_of(model)) is not None:
                table.columns = [c for c in table.columns if c.name not in (field_name, f"{field_name}_id")]
                table.foreign_keys = [f for f in table.foreign_keys if f.column != f"{field_name}_id"]
        elif (
            kind == "RenameField"
            and (model := const(kw(op, "model_name", 0)))
            and (old := const(kw(op, "old_name", 1)))
            and (new := const(kw(op, "new_name", 2)))
        ):
            if (table := table_of(model)) is not None:
                for c in table.columns:
                    if c.name in (old, f"{old}_id"):
                        c.name = new if c.name == old else f"{new}_id"
                for f in table.foreign_keys:
                    if f.column == f"{old}_id":
                        f.column = f"{new}_id"
        elif kind == "DeleteModel" and (name := const(kw(op, "name", 0))):
            if (table := table_of(name)) is not None:
                tables.pop(table.name, None)
                model_tables.pop(model_key(name), None)
        elif kind in ("RenameModel", "AlterModelTable"):
            if kind == "RenameModel":
                old, new = const(kw(op, "old_name", 0)), const(kw(op, "new_name", 1))
                new_table = f"{app}_{new}".lower() if new else None
            else:
                old, new, new_table = const(kw(op, "name", 0)), const(kw(op, "name", 0)), const(kw(op, "table", 1))
            if old and new and new_table and (table := table_of(old)) is not None:
                tables.pop(table.name, None)
                model_tables.pop(model_key(old), None)
                table.name = _clean(new_table)
                tables[table.name] = table
                model_tables[model_key(new)] = table.name
    return list(tables.values())


# ---------------------------------------------------------------- TypeORM / Prisma
TEMPLATE_SQL_RE = re.compile(r"query\s*\(\s*(?:`([^`]*)`|'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")", re.S)


def parse_typeorm_migration(content: str, source_file: str, tables: dict[str, Table] | None = None) -> list[Table]:
    if "MigrationInterface" not in content and "queryRunner" not in content:
        return []
    up = content.split("down(")[0] if "down(" in content else content
    sql = "\n".join((m.group(1) or m.group(2) or m.group(3) or "") for m in TEMPLATE_SQL_RE.finditer(up))
    return parse_sql(sql, source_file, tool="typeorm", tables=tables)


PRISMA_MODEL_RE = re.compile(r"model\s+(\w+)\s*\{(.*?)\n\}", re.S)
PRISMA_TABLE_MAP_RE = re.compile(r'@@map\(\s*(?:name\s*:\s*)?"([^"]+)"')
PRISMA_MAP_RE = re.compile(r'(?<!@)@map\(\s*(?:name\s*:\s*)?"([^"]+)"')


def parse_prisma(content: str, source_file: str) -> list[Table]:
    tables: list[Table] = []
    for m in PRISMA_MODEL_RE.finditer(content):
        model, body = m.group(1), m.group(2)
        table = Table(name=model.lower(), source_file=source_file, line=content.count("\n", 0, m.start()) + 1, tool="prisma")
        table.mapped_entities.append(f"prisma.{model}")
        # `@@map("accounts")` and `@@map(name: "accounts")` rename the table; `@map` renames a column.
        if mm := PRISMA_TABLE_MAP_RE.search(body):
            table.name = _clean(mm.group(1))
        columns_by_field: dict[str, str] = {}
        pending_fks: list[tuple[str, str, str]] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith(("//", "@@")):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            name, ftype = parts[0], parts[1]
            attrs = " ".join(parts[2:])
            if rel := re.search(r"@relation\(\s*fields:\s*\[([^\]]+)\]\s*,\s*references:\s*\[([^\]]+)\]", attrs):
                for c, r in zip(rel.group(1).split(","), rel.group(2).split(",")):
                    pending_fks.append((_clean(c), ftype.rstrip("?[]").lower(), _clean(r)))
                continue
            if ftype[0].isupper() and ftype.rstrip("?[]") not in ("String", "Int", "BigInt", "Float", "Decimal", "Boolean", "DateTime", "Json", "Bytes"):
                continue  # relation field without fk mapping (back-reference)
            column = _clean(cm.group(1)) if (cm := PRISMA_MAP_RE.search(attrs)) else _clean(name)
            columns_by_field[_clean(name)] = column
            table.columns.append(Column(name=column, type=ftype.rstrip("?").upper(), nullable=ftype.endswith("?"), primary_key="@id" in attrs))
        for field, ref_table, ref_field in pending_fks:
            table.foreign_keys.append(ForeignKey(column=columns_by_field.get(field, field), ref_table=ref_table, ref_column=ref_field))
        tables.append(table)
    # resolve @@map names for FK targets (and @map names of the referenced fields)
    by_model = {t.mapped_entities[0].split(".")[1].lower(): t for t in tables}
    for t in tables:
        for fk in t.foreign_keys:
            if (target := by_model.get(fk.ref_table)) is not None:
                fk.ref_table = target.name
                if fk.ref_column and not target.column(fk.ref_column):
                    fk.ref_column = next((c.name for c in target.columns if c.primary_key), fk.ref_column)
    return tables


# ---------------------------------------------------------------- orchestration
MIGRATION_HINTS = ("migration", "migrations", "changelog", "db/", "schema", "versions", "flyway", "liquibase", "alembic")


def _is_test_or_data(rel: str) -> bool:
    low = rel.lower()
    return "/test/" in low or low.endswith(("data.sql", "import.sql")) or "seed" in low


def detect_schema(files: list[SourceFile]) -> SchemaInfo:
    """Apply every migration/schema source in path order on a shared table state."""
    state: dict[str, Table] = {}
    django_models: dict[str, str] = {}
    sources: list[str] = []

    def alembic_key(rel: str) -> tuple:
        # alembic file names usually start with a revision/number; keep lexical order otherwise
        return (0 if "versions/" in rel else 1, (), rel)

    def flyway_key(rel: str) -> tuple:
        m = re.search(r"/V(\d+(?:[._]\d+)*)__", rel)
        return (0, tuple(int(x) for x in re.split(r"[._]", m.group(1))), rel) if m else (1, (), rel)

    ordered = sorted(
        (f for f in files if not _is_test_or_data(f.rel_path)), key=lambda f: flyway_key(f.rel_path) if f.extension == ".sql" else alembic_key(f.rel_path)
    )
    for f in ordered:
        rel, low = f.rel_path, f.rel_path.lower()
        before = len(state)
        touched = False
        if f.extension == ".sql":
            tool = "flyway" if re.search(r"/V\d+(?:[._]\d+)*__", rel) or "flyway" in low else "sql"
            touched = bool(CREATE_TABLE_START_RE.search(f.content) or "ALTER TABLE" in f.content.upper())
            parse_sql(f.content, rel, tool, tables=state)
        elif f.extension == ".prisma":
            for t in parse_prisma(f.content, rel):
                state[t.name] = t
            touched = True
        elif f.extension == ".xml" and ("changelog" in low or "<databaseChangeLog" in f.content[:2000]):
            for t in parse_liquibase_xml(f.content, rel):
                state[t.name] = t
            touched = True
        elif f.extension in (".yml", ".yaml") and ("changelog" in low or "databaseChangeLog" in f.content[:2000]):
            for t in parse_liquibase_yaml(f.content, rel):
                state[t.name] = t
            touched = True
        elif f.language == Language.PYTHON and "/migrations/" in low and "migrations.Migration" in f.content:
            parse_django_migration(f.content, rel, tables=state, model_tables=django_models)
            touched = True
        elif f.language == Language.PYTHON and ("alembic" in low or "/versions/" in low or low.startswith("versions/")) and "op." in f.content:
            parse_alembic(f.content, rel, tables=state)
            touched = True
        elif f.language in (Language.TYPESCRIPT, Language.JAVASCRIPT) and "migration" in low:
            touched = bool(parse_typeorm_migration(f.content, rel, tables=state)) or len(state) != before
        if touched:
            sources.append(rel)
    return SchemaInfo(tables=sorted(state.values(), key=lambda t: t.name), sources=sources)


# ---------------------------------------------------------------- entity <-> table mapping
def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _variants(name: str) -> set[str]:
    base = _snake(name)
    out = {base, base.replace("_", ""), name.lower()}
    for b in list(out):
        out |= {b + "s", b + "es", b[:-1] + "ies" if b.endswith("y") else b}
    return out


def _table_hint(unit: CodeUnit) -> str | None:
    for a in unit.annotations:
        if a.name in ("Table", "Entity", "Document") and a.arguments:
            if m := re.search(r"""(?:name\s*=\s*)?['"]([\w.]+)['"]""", a.arguments):
                return m.group(1)
    for c in unit.children:
        if c.kind == CodeUnitKind.FIELD and c.name == "__tablename__":
            return None  # value not captured by the parser; fall back to name variants
    return None


def map_entities(schema: SchemaInfo, units: list[CodeUnit]) -> None:
    """Link ORM entities (Java @Entity, TypeORM @Entity, SQLAlchemy/pydantic entities) to tables by name."""
    if not schema.tables:
        return
    by_name = {t.name: t for t in schema.tables}
    stack = list(units)
    while stack:
        u = stack.pop()
        stack.extend(c for c in u.children if c.kind in (CodeUnitKind.CLASS, CodeUnitKind.RECORD))
        if u.stereotype != "entity":
            continue
        candidates = []
        if hint := _table_hint(u):
            candidates.append(_clean(hint))
        parts = u.fqn.split(".")
        if "models" in parts[:-1] and parts.index("models") > 0:
            candidates.append(f"{parts[parts.index('models') - 1]}_{u.name}".lower())  # Django: <app>_<model>
        candidates += sorted(_variants(u.name), key=len)
        for cand in candidates:
            if (t := by_name.get(cand)) is not None:
                if u.fqn not in t.mapped_entities:
                    t.mapped_entities.append(u.fqn)
                break
