"""Regressions found by running DevAnalyzer on real repositories (spring-petclinic-microservices, shadcn/taxonomy,
nestjs-realworld-example-app, Django-Styleguide-Example). Each test reproduces the shape of code that failed."""

import json
import textwrap

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.build_detectors import MavenDetector, PythonBuildDetector
from devanalyzer.core.models import EXTENSION_LANGUAGE, CodeUnitKind, Endpoint, LanguageAnalysis, ProjectAnalysis, RelationKind, SourceFile
from devanalyzer.core.plugin import is_test_path
from devanalyzer.core.schema import detect_schema, parse_prisma
from devanalyzer.core.workspace import WorkspaceAnalysis, WorkspaceAnalyzer, WorkspaceConfig, _common_prefix, _path_matches
from devanalyzer.languages.node.tsconfig import _loads_tolerant, path_aliases
from devanalyzer.llm.business_rules import BusinessRuleExtractor
from tests.test_business_rules import FakeProvider


def _source(path, content, language=None):
    name = path.rsplit("/", 1)[-1]
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return SourceFile(name=name, path=path, rel_path=path, extension=ext, language=language or EXTENSION_LANGUAGE.get(ext), content=textwrap.dedent(content))


def _write(root, files):
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content), encoding="utf-8")
    return root


# ---------------------------------------------------------------- #10 test sources
def test_test_sources_are_not_architecture(settings, tmp_path):
    assert is_test_path("src/test/java/com/acme/PetResourceTest.java")
    assert (
        is_test_path("app/__tests__/orders.ts") and is_test_path("src/orders.spec.ts") and is_test_path("pkg/tests/test_x.py") and is_test_path("conftest.py")
    )
    assert not is_test_path("src/main/java/com/acme/testing_examples/Roster.java") and not is_test_path("app/contest/page.tsx")
    _write(
        tmp_path,
        {
            "pom.xml": "<project><artifactId>x</artifactId></project>",
            "src/main/java/com/acme/Svc.java": "package com.acme; public class Svc { public void a() {} }",
            "src/test/java/com/acme/SvcTest.java": "package com.acme; public class SvcTest { public void t() {} }",
            "api/views.py": "class Real:\n    def get(self):\n        return 1\n",
            "api/tests/test_views.py": "from unittest.mock import patch\n\n@patch('api.views.x')\ndef test_it(m):\n    pass\n",
            "web/order.ts": "export class OrderService {}",
            "web/order.spec.ts": "export class OrderServiceSpec {}",
        },
    )
    names = {u.name for u in ProjectAnalyzer(settings).run(tmp_path).units}
    assert names == {"Svc", "Real", "OrderService"}


# ---------------------------------------------------------------- #3 / #4 Python routes
def test_bare_patch_decorator_is_not_a_route(settings, tmp_path):
    _write(
        tmp_path,
        {
            "svc/handlers.py": (
                "from unittest.mock import patch\nfrom fastapi import APIRouter\nrouter = APIRouter(prefix='/items')\n\n"
                "@patch('svc.other.fn')\ndef helper(m):\n    return 1\n\n@router.patch('/{id}')\ndef update(id: int):\n    return id\n"
            ),
        },
    )
    analysis = ProjectAnalyzer(settings).run(tmp_path)
    assert [(e.http_method, e.path) for e in analysis.endpoints] == [("PATCH", "/items/{id}")]
    assert next(u for u in analysis.units if u.name == "helper").stereotype is None
    assert next(u for u in analysis.units if u.name == "update").stereotype == "controller"


def test_django_inline_nested_includes_keep_their_prefix(settings, tmp_path):
    _write(
        tmp_path,
        {
            "manage.py": "",
            "config/__init__.py": "",
            "config/urls.py": 'from django.urls import include, path\nurlpatterns = [path("api/", include(("shop.files.urls", "api")))]\n',
            "shop/__init__.py": "",
            "shop/files/__init__.py": "",
            "shop/files/apis.py": (
                "from rest_framework.views import APIView\n\nclass StartApi(APIView):\n    def post(self, request):\n        return None\n\n"
                "class LocalApi(APIView):\n    def post(self, request, file_id):\n        return None\n\n"
                "class StandardApi(APIView):\n    def post(self, request):\n        return None\n\nclass LegacyApi(APIView):\n    def get(self, request):\n        return None\n"
            ),
            "shop/files/urls.py": (
                "from django.urls import include, path\nfrom shop.files.apis import LegacyApi, LocalApi, StandardApi, StartApi\n\n"
                "direct_patterns = [path('start/', StartApi.as_view(), name='start'), path('local/<str:file_id>/', LocalApi.as_view())]\n"
                "urlpatterns = [path('upload/', include(([path('standard/', StandardApi.as_view()), path('direct/', include((direct_patterns, 'direct')))], 'upload')))]\n"
                "urlpatterns += [path('legacy/', LegacyApi.as_view())]\n"
            ),
        },
    )
    paths = sorted((e.http_method, e.path) for e in ProjectAnalyzer(settings).run(tmp_path).endpoints)
    assert paths == [
        ("GET", "/api/legacy"),
        ("POST", "/api/upload/direct/local/{file_id}"),
        ("POST", "/api/upload/direct/start"),
        ("POST", "/api/upload/standard"),
    ]


def test_django_manager_alias_and_models_map_to_app_tables(settings, tmp_path):
    _write(
        tmp_path,
        {
            "manage.py": "",
            "shop/__init__.py": "",
            "shop/files/__init__.py": "",
            "shop/files/models.py": (
                "from django.contrib.auth.models import BaseUserManager as BUM\nfrom django.db import models\n\n"
                "class FileManager(BUM):\n    def active(self):\n        return self\n\n"
                "class File(models.Model):\n    name = models.CharField(max_length=10)\n"
            ),
            "shop/files/migrations/__init__.py": "",
            "shop/files/migrations/0001_initial.py": (
                "from django.conf import settings\nfrom django.db import migrations, models\n\nclass Migration(migrations.Migration):\n"
                "    operations = [\n        migrations.CreateModel(\n            name='File',\n            fields=[\n"
                "                ('id', models.UUIDField(primary_key=True, serialize=False)),\n                ('name', models.CharField(max_length=255)),\n"
                "                ('size', models.IntegerField(null=True)),\n"
                "                ('uploaded_by', models.ForeignKey(null=True, on_delete=models.SET_NULL, to=settings.AUTH_USER_MODEL)),\n"
                "                ('tags', models.ManyToManyField(to='files.Tag')),\n            ],\n        ),\n"
                "        migrations.CreateModel(name='Tag', fields=[('id', models.AutoField(primary_key=True))], options={'db_table': 'labels'}),\n"
                "        migrations.AddField(model_name='file', name='tag', field=models.ForeignKey(to='files.Tag', on_delete=models.CASCADE)),\n"
                "    ]\n"
            ),
            "shop/files/migrations/0002_rename.py": (
                "from django.db import migrations, models\n\nclass Migration(migrations.Migration):\n    operations = [\n"
                "        migrations.RenameField(model_name='file', old_name='size', new_name='byte_size'),\n"
                "        migrations.RemoveField(model_name='file', name='uploaded_by'),\n"
                "        migrations.AlterField(model_name='file', name='name', field=models.TextField()),\n    ]\n"
            ),
        },
    )
    analysis = ProjectAnalyzer(settings).run(tmp_path)
    assert next(u for u in analysis.units if u.name == "FileManager").stereotype == "repository"
    assert next(u for u in analysis.units if u.name == "FileManager").extends == ["BaseUserManager"]
    tables = {t.name: t for t in analysis.db_schema.tables}
    assert set(tables) == {"files_file", "labels"}
    file = tables["files_file"]
    assert [(c.name, c.type, c.primary_key, c.nullable) for c in file.columns] == [
        ("id", "UUID", True, False),
        ("byte_size", "INTEGER", False, True),
        ("tag_id", "FOREIGNKEY", False, False),
        ("name", "TEXT", False, False),
    ]
    assert [(fk.column, fk.ref_table) for fk in file.foreign_keys] == [("tag_id", "labels")]
    assert file.mapped_entities == ["shop.files.models.File"]


# ---------------------------------------------------------------- #6 Python build manifests
def test_tooling_only_pyproject_falls_back_to_layered_requirements():
    files = [
        _source("pyproject.toml", "[tool.ruff]\nline-length = 100\n"),
        _source("requirements.txt", "-r requirements/base.txt\n-r requirements/local.txt\n"),
        _source("requirements/base.txt", "Django==5.2.7\ndjangorestframework==3.16.1  # api\ncelery==5.5.3\n"),
        _source("requirements/local.txt", "-r base.txt\npytest==8.0.0\nDjango==5.2.7\n"),
    ]
    infos = PythonBuildDetector().detect(files)
    assert len(infos) == 1 and infos[0].manifest_path == "requirements.txt"
    assert [d.name for d in infos[0].dependencies] == ["Django", "djangorestframework", "celery", "pytest"]
    assert {f.name for f in infos[0].frameworks} == {"Django", "Celery", "pytest"}
    # without a root requirements.txt every requirements file counts once
    assert [d.name for d in PythonBuildDetector().detect(files[2:])[0].dependencies] == ["Django", "djangorestframework", "celery", "pytest"]


# ---------------------------------------------------------------- #9 Maven parents
def test_maven_module_inherits_aggregator_parent_from_disk_and_within_project(settings, tmp_path):
    root = _write(
        tmp_path,
        {
            "pom.xml": (
                "<project><parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>3.4.1</version></parent>"
                "<artifactId>shop</artifactId><packaging>pom</packaging><properties><java.version>21</java.version><shop.version>1.2</shop.version></properties>"
                "<modules><module>orders</module></modules></project>"
            ),
            "orders/pom.xml": (
                "<project><parent><groupId>com.acme</groupId><artifactId>shop</artifactId><version>1.0</version></parent><artifactId>orders</artifactId>"
                "<properties><java.version>17</java.version></properties><dependencies><dependency><groupId>org.springframework.boot</groupId>"
                "<artifactId>spring-boot-starter-web</artifactId></dependency><dependency><groupId>com.acme</groupId><artifactId>lib</artifactId>"
                "<version>${shop.version}</version></dependency></dependencies></project>"
            ),
            "orders/src/main/java/com/acme/A.java": "package com.acme; public class A {}",
        },
    )
    alone = ProjectAnalyzer(settings).run(root / "orders")
    assert alone.versions == {"spring_boot": "3.4.1", "java": "17"}
    assert {f.name for f in alone.frameworks} >= {"Spring Boot", "Spring Web"}
    assert next(d for d in alone.dependencies if d.name == "com.acme:lib").version == "1.2"
    whole = {b.manifest_path: b for b in ProjectAnalyzer(settings).run(root).builds}
    assert whole["orders/pom.xml"].versions == {"spring_boot": "3.4.1", "java": "17"} and whole["pom.xml"].versions["java"] == "21"


def test_maven_relative_path_never_walks_sideways_or_too_far_up(settings, tmp_path):
    elsewhere = _write(
        tmp_path / "elsewhere", {"pom.xml": "<project><artifactId>evil</artifactId><properties><java.version>8</java.version></properties></project>"}
    )
    module = _write(
        tmp_path / "a" / "b" / "c" / "module",
        {
            "pom.xml": (
                "<project><parent><groupId>x</groupId><artifactId>shop</artifactId><version>1</version>"
                "<relativePath>../../../../elsewhere/pom.xml</relativePath></parent><artifactId>m</artifactId></project>"
            ),
            "src/main/java/A.java": "public class A {}",
        },
    )
    assert elsewhere.is_dir()
    assert ProjectAnalyzer(settings).run(module).versions == {}  # sideways traversal ignored
    (module / "pom.xml").write_text(
        "<project><parent><groupId>x</groupId><artifactId>shop</artifactId><version>1</version><relativePath>../../../../pom.xml</relativePath></parent>"
        "<artifactId>m</artifactId></project>",
        encoding="utf-8",
    )
    (tmp_path / "pom.xml").write_text(
        "<project><artifactId>root</artifactId><properties><java.version>21</java.version></properties></project>", encoding="utf-8"
    )
    assert ProjectAnalyzer(settings).run(module).versions == {}  # four levels up is beyond the ancestor allowance
    (tmp_path / "a" / "b" / "pom.xml").write_text(
        "<project><artifactId>mid</artifactId><properties><java.version>17</java.version></properties></project>", encoding="utf-8"
    )
    (module / "pom.xml").write_text(
        "<project><parent><groupId>x</groupId><artifactId>mid</artifactId><version>1</version><relativePath>../../pom.xml</relativePath></parent>"
        "<artifactId>m</artifactId></project>",
        encoding="utf-8",
    )
    assert ProjectAnalyzer(settings).run(module).versions == {"java": "17"}  # direct ancestor within three levels


def test_requirement_include_syntaxes():
    from devanalyzer.core.build_detectors import _requirement_target

    assert _requirement_target("-r requirements/base.txt") == "requirements/base.txt"
    assert _requirement_target("-rbase.txt") == "base.txt"
    assert _requirement_target("-r\tbase.txt") == "base.txt"
    assert _requirement_target("--requirement base.txt") == "base.txt"
    assert _requirement_target("--requirement=base.txt") == "base.txt"
    assert _requirement_target("-r") == ""


def test_spring_boot_detected_from_starters_without_parent():
    pom = _source(
        "pom.xml",
        "<project><artifactId>x</artifactId><dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency></dependencies></project>",
    )
    info = MavenDetector().detect([pom])[0]
    assert info.versions == {"spring_boot": "managed"} and [f.name for f in info.frameworks][:2] == ["Spring Boot", "Spring Data JPA"]


# ---------------------------------------------------------------- #7 / #8 / #1 Node
def test_node_generic_injection_aliases_and_function_calls(settings, tmp_path):
    _write(
        tmp_path,
        {
            "package.json": '{"name": "shop", "dependencies": {"@nestjs/core": "10", "next": "15"}}',
            "tsconfig.json": '{\n  // comments and trailing commas are legal here\n  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"],},},\n  "include": ["**/*.ts"],\n}\n',
            "lib/subscription.ts": "export async function getUserSubscriptionPlan(userId: string) {\n  return { isPro: false };\n}\n",
            "lib/db.ts": "export const db = {};\n",
            "app/api/posts/route.ts": (
                'import { db } from "@/lib/db"\nimport { getUserSubscriptionPlan } from "@/lib/subscription"\n\n'
                "function helper() {\n  return 1\n}\n\n"
                "export async function POST(req: Request) {\n  const plan = await getUserSubscriptionPlan('u')\n  if (!plan.isPro) {\n    throw new Error('limit')\n  }\n  return new Response(null)\n}\n"
            ),
            "app/page.tsx": "export default function Page() {\n  return null\n}\n",
            "components/button.tsx": "export function Button() {\n  return null\n}\nexport function Icon() {\n  return null\n}\n",
            "hooks/use-toast.ts": "export function useToast() {\n  const limit = 10\n  return limit\n}\n",
            "src/article/article.entity.ts": "export class ArticleEntity {\n  id: number;\n}\n",
            "src/article/article.service.ts": (
                "import { Injectable } from '@nestjs/common';\nimport { Repository } from 'typeorm';\nimport { ArticleEntity } from './article.entity';\n\n"
                "@Injectable()\nexport class ArticleService {\n  constructor(private readonly articleRepository: Repository<ArticleEntity>) {}\n\n"
                "  save(): Promise<ArticleEntity[]> {\n    return this.articleRepository.find();\n  }\n}\n"
            ),
        },
    )
    assert path_aliases([_source("tsconfig.json", (tmp_path / "tsconfig.json").read_text())]) == [("@/", ""), ("", "")]
    assert _loads_tolerant('{"paths": {"@/*": ["./*"]}, "include": ["**/*.ts"] /* c */}') == {"paths": {"@/*": ["./*"]}, "include": ["**/*.ts"]}
    # No `baseUrl`: `paths` still resolve against the tsconfig directory, but bare imports stay packages (no catch-all).
    assert path_aliases([_source("web/tsconfig.json", '{"compilerOptions": {"paths": {"@app/*": ["src/app/*"]}}}')]) == [("@app/", "web/src/app/")]
    assert path_aliases([_source("tsconfig.json", '{"compilerOptions": {"baseUrl": 1}}')]) == []
    assert _loads_tolerant('{"a": "unterminated \\') == {}  # trailing escape must not crash the comment stripper
    analysis = ProjectAnalyzer(settings).run(tmp_path)
    rels = {(r.source, r.target, r.kind) for r in analysis.relations}
    assert ("src/article/article.service.ArticleService", "src/article/article.entity.ArticleEntity", RelationKind.DEPENDS_ON) in rels
    assert ("app/api/posts/route.POST", "lib/subscription.getUserSubscriptionPlan", RelationKind.CALLS) in rels
    assert ("app/api/posts/route.POST", "lib/subscription.getUserSubscriptionPlan", RelationKind.IMPORTS) in rels
    post = next(u for u in analysis.units if u.fqn == "app/api/posts/route.POST")
    assert post.stereotype == "controller" and next(u for u in analysis.units if u.fqn == "app/page.Page").stereotype is None

    candidates = BusinessRuleExtractor.candidate_units(analysis)
    modules = {c.fqn: c for c in candidates if c.kind == CodeUnitKind.MODULE}
    assert "app/api/posts/route" in modules and modules["app/api/posts/route"].stereotype == "controller"
    assert "lib/subscription" in modules and modules["lib/subscription"].stereotype is None
    assert not {"components/button", "app/page", "hooks/use-toast"} & set(modules)  # UI components and hooks are not rule candidates
    assert [c.fqn for c in BusinessRuleExtractor.candidate_units(analysis, {"controller"})] == ["app/api/posts/route"]
    route = modules["app/api/posts/route"]
    # The helper declared before the handler must not demote the file from controller.
    assert (route.line_start, route.line_end) == (4, 14) and [c.name for c in route.children] == ["helper", "POST"]
    # The prompt context of a module lists the functions it calls, with their signatures.
    extractor = BusinessRuleExtractor(FakeProvider(), settings)
    context = extractor._context(analysis, route)
    assert "function getUserSubscriptionPlan(string)" in context and "route" not in context.split("Project context")[1].split("\n")[1]
    # A bare function unit (not wrapped in a module) gets the same call/import context.
    assert "function getUserSubscriptionPlan(string)" in extractor._context(analysis, post)


# ---------------------------------------------------------------- #5 Prisma
def test_prisma_named_map_arguments_rename_tables_and_columns():
    schema = """
    model Account {
      id        String @id @default(cuid())
      userId    String @map(name: "user_id")
      expiresAt Int?   @map("expires_at")
      user User @relation(fields: [userId], references: [id], onDelete: Cascade)
      @@map(name: "accounts")
    }

    model User {
      id       String    @id
      accounts Account[]
      @@map("users")
    }
    """
    tables = {t.name: t for t in parse_prisma(textwrap.dedent(schema), "prisma/schema.prisma")}
    assert set(tables) == {"accounts", "users"}
    assert [c.name for c in tables["accounts"].columns] == ["id", "user_id", "expires_at"]
    assert [(fk.column, fk.ref_table, fk.ref_column) for fk in tables["accounts"].foreign_keys] == [("user_id", "users", "id")]
    # migrations and schema.prisma describe the same database: one table per name
    files = [
        _source("prisma/schema.prisma", schema),
        _source("prisma/migrations/1_init/migration.sql", 'CREATE TABLE "accounts" ("id" TEXT PRIMARY KEY, "user_id" TEXT);'),
    ]
    assert [t.name for t in detect_schema(files).tables] == ["accounts", "users"]


# ---------------------------------------------------------------- #2 workspace
def test_workspace_names_share_a_prefix_and_callers_never_wildcard_fixed_segments():
    names = {f"spring-petclinic-{n}": f"spring-petclinic-{n}" for n in ("customers-service", "vets-service", "api-gateway", "visits-service")}
    assert _common_prefix(list(names)) == "spring-petclinic-"
    assert WorkspaceAnalyzer._project_for_name("http://customers-service/owners/{ownerId}", names) == "spring-petclinic-customers-service"
    assert WorkspaceAnalyzer._project_for_name("lb://vets-service", names) == "spring-petclinic-vets-service"
    assert WorkspaceAnalyzer._project_for_name("http://payments-service/x", names) is None
    assert _common_prefix(["orders", "orders-legacy", "preorders"]) == "" and _common_prefix(["only-one"]) == ""
    assert _common_prefix(["same-thing", "same-thing"]) == "" and _common_prefix(["shop-", "shop-api"]) == ""  # never swallow a whole name
    assert _path_matches("/api/customer/owners/${ownerId}/pets/${petId}", "/owners/{ownerId}/pets/{petId}")
    assert not _path_matches("/owners/details/${ownerId}", "/owners/{ownerId}/pets")
    assert not _path_matches("/api/customer/owners/${ownerId}/pets/${petId}", "/pets/visits")


def test_workspace_skips_navigation_literals_and_links_base_urls_and_gateway_routes(settings):
    analyzer = WorkspaceAnalyzer(settings)
    config = WorkspaceConfig.model_validate(
        {"projects": [{"name": "pc-gateway", "path": "g"}, {"name": "pc-customers-service", "path": "c"}, {"name": "pc-visits-service", "path": "v"}]}
    )
    endpoints = {
        "pc-customers-service": [
            Endpoint(http_method="GET", path="/owners", handler="X.a", file="X.java", line=1),
            Endpoint(http_method="GET", path="/owners/{ownerId}", handler="X.b", file="X.java", line=2),
        ],
        "pc-visits-service": [Endpoint(http_method="GET", path="/pets/visits", handler="Y.a", file="Y.java", line=1)],
    }
    analyzer.analyses = {
        p.name: ProjectAnalysis(
            project_name=p.name, project_path=p.path, languages=[LanguageAnalysis(language="java", confidence=1, endpoints=endpoints.get(p.name, []))]
        )
        for p in config.projects
    }
    analyzer.files = {
        "pc-gateway": [
            _source("Nav.js", '<router-link to="/owners">Find owners</router-link>\nrouter.push("/owners/new");\nconst url = "/owners";\n'),
            _source("Client.java", 'private String hostname = "http://visits-service/";\nString x = uri("http://customers-service/owners/{ownerId}");\n'),
            _source(
                "application.yml",
                "routes:\n  - id: vets\n    uri: lb://customers-service\n    predicates:\n      - Path=/api/customer/**\n  - id: visits\n    uri: lb://visits-service\n    predicates:\n      - Path=/api/visit/**\n",
            ),
        ],
        "pc-customers-service": [],
        "pc-visits-service": [],
    }
    ws = WorkspaceAnalysis(name="t")
    links = sorted((link.target, link.label, link.evidence_file, link.evidence_line) for link in analyzer._http_links(config, ws))
    assert links == [
        ("pc-customers-service", "GET /owners", "Nav.js", 3),
        ("pc-customers-service", "GET /owners/{ownerId}", "Client.java", 2),
        ("pc-customers-service", "gateway /api/customer/**", "application.yml", 3),
        ("pc-visits-service", "base url", "Client.java", 1),
        ("pc-visits-service", "gateway /api/visit/**", "application.yml", 7),
    ]
    assert ws.unresolved_calls == []
    assert json.dumps([link.model_dump() for link in analyzer._http_links(config, WorkspaceAnalysis(name="t"))])  # serialisable
