import json

from devanalyzer.core.analyzer import ProjectAnalyzer
from devanalyzer.core.models import EXTENSION_LANGUAGE, SourceFile
from devanalyzer.languages.node.nextjs import extract_next_endpoints, is_next_project


def _source(path, content):
    name = path.rsplit("/", 1)[-1]
    ext = "." + name.rsplit(".", 1)[-1]
    return SourceFile(name=name, path=path, rel_path=path, extension=ext, language=EXTENSION_LANGUAGE.get(ext), content=content)


def test_nextjs_file_system_routes(settings, fixtures):
    analysis = ProjectAnalyzer(settings).run(fixtures / "node_next")
    assert {f.name for f in analysis.frameworks} >= {"Next.js", "React"}
    eps = {(e.http_method, e.path): e for e in analysis.endpoints}
    assert ("GET", "/api/orders/{id}") in eps and ("DELETE", "/api/orders/{id}") in eps
    assert eps[("GET", "/api/orders/{id}")].framework == "Next.js (route)" and eps[("GET", "/api/orders/{id}")].handler.endswith("route.GET")
    assert ("GET", "/products/{slug}") in eps  # route group "(shop)" dropped
    assert {m for m, p in eps if p == "/api/checkout"} == {"GET", "POST"}
    assert ("GET", "/legacy") in eps and eps[("GET", "/legacy")].framework == "Next.js (page)"


def test_next_detection_requires_dependency_or_config():
    for manifest in ({"scripts": {"dev": "next"}}, {"name": "next"}, {"dependencies": {"next-auth": "5"}}, []):
        assert not is_next_project([_source("package.json", json.dumps(manifest))])
    assert not is_next_project([_source("package.json", "{broken")])
    assert is_next_project([_source("package.json", '{"devDependencies":{"next":"15"}}')])
    assert is_next_project([_source("next.config.mjs", "export default {};")])


def test_next_routes_require_real_exports_and_correct_roots():
    files = [
        _source("package.json", '{"dependencies":{"next":"15"}}'),
        _source("app/empty/route.ts", "export const runtime = 'edge';"),
        _source("app/comment/route.ts", "// export function GET() {}\n/* export const POST = () => {}; */"),
        _source("app/string/route.ts", 'const example = "export function GET() {}";'),
        _source("app/app/route.ts", "export async function GET() {}\nexport const POST = () => {};"),
        _source("app/aliased/route.ts", "function handler() {}\nexport { handler as PATCH };"),
        _source("app/_private/route.ts", "export function GET() {}"),
        _source(".next/server/app/generated/route.js", "export function GET() {}"),
        _source("tests/app/example/route.ts", "export function GET() {}"),
        _source("pages/layout.tsx", "export default function Layout() {}"),
        _source("pages/plain.js", "export default function Page() {}"),
        _source("pages/no-page.tsx", "export function Helper() {}"),
        _source("pages/_app.tsx", "export default function App() {}"),
    ]
    eps = extract_next_endpoints(files)
    assert {(e.http_method, e.path) for e in eps} == {("GET", "/app"), ("POST", "/app"), ("PATCH", "/aliased"), ("GET", "/layout"), ("GET", "/plain")}
    patch = next(e for e in eps if e.http_method == "PATCH")
    assert patch.handler == "app/aliased/route.handler" and patch.line == 2


def test_next_scope_stays_with_the_declaring_package():
    files = [
        _source("packages/web/package.json", '{"dependencies":{"next":"15"}}'),
        _source("packages/web/src/app/page.tsx", "export default function Page() {}"),
        _source("packages/other/package.json", '{"dependencies":{"react":"19"}}'),
        _source("packages/other/app/page.tsx", "export default function Page() {}"),
    ]
    eps = extract_next_endpoints(files)
    assert [(e.path, e.file) for e in eps] == [("/", "packages/web/src/app/page.tsx")]
