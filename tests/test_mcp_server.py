import json
import shutil

import pytest

pytest.importorskip("mcp")

from mcp.client.session import ClientSession  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from devanalyzer.kb.store import KnowledgeBase  # noqa: E402
from devanalyzer.llm.business_rules import BusinessRuleExtractor  # noqa: E402
from devanalyzer.mcp_server.server import create_server  # noqa: E402
from tests.test_business_rules import FakeProvider  # noqa: E402


@pytest.fixture
def project(fixtures, tmp_path, spring_analysis):
    root = tmp_path / "java_spring"
    shutil.copytree(fixtures / "java_spring", root)
    result = BusinessRuleExtractor(FakeProvider()).extract(spring_analysis, fixtures / "java_spring", stereotypes={"service"})
    with KnowledgeBase(root / ".devanalyzer" / "kb.sqlite") as kb:
        kb.ingest(result)
    return root


async def _call(session: ClientSession, tool: str, **args):
    res = await session.call_tool(tool, args)
    return json.loads(res.content[0].text)


@pytest.mark.anyio
async def test_mcp_tools_end_to_end(project):
    server = create_server(project)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"get_project_summary", "get_code_unit", "query_graph", "get_business_rules", "search_knowledge", "read_source"} <= tools

        summary = await _call(session, "get_project_summary")
        assert summary["project"] == "bank-service" and summary["versions"]["java"] == "21"

        unit = await _call(session, "get_code_unit", name="AccountService", include_source=True)
        assert unit["stereotype"] == "service"
        assert any(m["name"] == "deposit" for m in unit["methods"])
        assert any(o["target"] == "com.acme.bank.repository.AccountRepository" for o in unit["outgoing"])
        assert len(unit["business_rules"]) == 2
        assert "MAX_DEPOSIT" in unit["source"]

        graph = await _call(session, "query_graph", node="AccountController", relation="depends_on", direction="out")
        assert graph["edges"] == [{"source": "com.acme.bank.controller.AccountController", "kind": "depends_on", "target": "com.acme.bank.service.AccountService"}]

        path = await _call(session, "find_path", source="AccountController", target="Account")
        assert path["path"][0].endswith("AccountController") and path["path"][-1].endswith("Account")

        rules = await _call(session, "get_business_rules", status="inferred")
        assert rules["summary"]["rules"] == 2
        rid = rules["rules"][0]["id"]
        assert (await _call(session, "review_business_rule", rule_id=rid, status="confirmed", note="ok"))["updated"]
        assert (await _call(session, "get_business_rules", status="confirmed"))["summary"]["by_status"]["confirmed"] == 1

        hits = await _call(session, "search_knowledge", query="deposit")
        assert {h["type"] for h in hits} >= {"method", "business_rule", "glossary_term"}

        eps = await _call(session, "list_endpoints", http_method="post")
        assert eps[0]["path"] == "/accounts/{id}/deposit"

        outside = await _call(session, "read_source", file="../../etc/passwd")
        assert "error" in outside
