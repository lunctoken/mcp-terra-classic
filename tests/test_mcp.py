import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from starlette.testclient import TestClient

from http_server import build_http_app
from terra_classic_mcp.app import build_server

ADDRESS = "terra18vd8fpwxzck93qlwghaj6arh4p7c5n896xzem5"
ROOT = Path(__file__).resolve().parents[1]
NAMES = [
    "terra_get_chain_status", "terra_get_validators", "terra_get_balance",
    "terra_get_transaction", "terra_query_contract",
]


class FakeClient:
    def chain_status(self):
        return {"chain_id": "columbus-5", "height": "123", "time": "now", "block_id": "ABC"}

    def validators(self, limit=10):
        return {"status": "BOND_STATUS_BONDED", "validator_count": 1, "validators": [{"rank": 1, "moniker": "One", "operator_address": "val1", "tokens": "100", "voting_power_pct": 100.0, "commission_rate_pct": 5.0, "jailed": False, "website": ""}]}

    def balance(self, address):
        return {"address": address, "balances": [], "has_more": False}

    def transaction(self, tx_hash):
        return {"tx_hash": tx_hash, "height": "4", "code": 0, "timestamp": "now", "gas_wanted": "100", "gas_used": "80", "message_types": []}

    def contract_query(self, contract, query_msg):
        return {"contract": contract, "data": query_msg}


def test_in_process_mcp_tools_and_schemas():
    async def check():
        async with Client(build_server(FakeClient())) as client:
            listed = await client.list_tools()
            assert [tool.name for tool in listed.tools] == NAMES
            for tool in listed.tools:
                assert tool.annotations.read_only_hint is True
                assert tool.output_schema is not None
                assert tool.input_schema["additionalProperties"] is False
            result = await client.call_tool("terra_get_chain_status")
            assert result.structured_content["chain_id"] == "columbus-5"
            assert json.loads(result.content[0].text)["height"] == "123"
            balance = await client.call_tool("terra_get_balance", {"address": ADDRESS})
            assert balance.structured_content["address"] == ADDRESS
            validators = await client.call_tool("terra_get_validators", {"limit": 1})
            assert validators.structured_content["validators"][0]["moniker"] == "One"
            transaction = await client.call_tool("terra_get_transaction", {"tx_hash": "a" * 64})
            assert transaction.structured_content["tx_hash"] == "A" * 64
            contract = await client.call_tool("terra_query_contract", {"contract": ADDRESS, "query_msg": {"token_info": {}}})
            assert contract.structured_content["data"] == {"token_info": {}}
            invalid = await client.call_tool("terra_get_balance", {"address": ADDRESS[:-1] + "q"})
            assert invalid.is_error is True
            extra = await client.call_tool("terra_get_chain_status", {"url": "https://other.example"})
            assert extra.is_error is True
    asyncio.run(check())


def test_oversized_contract_result_is_rejected():
    class HugeClient(FakeClient):
        def contract_query(self, contract, query_msg):
            return {"contract": contract, "data": {"value": "x" * 70000}}

    async def check():
        async with Client(build_server(HugeClient())) as client:
            result = await client.call_tool("terra_query_contract", {"contract": ADDRESS, "query_msg": {"token_info": {}}})
            assert result.is_error is True

    asyncio.run(check())


def test_stdio_client_can_discover_tools():
    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], cwd=str(ROOT))
        async with Client(params) as client:
            listed = await client.list_tools()
            assert [tool.name for tool in listed.tools] == NAMES
    asyncio.run(check())


def test_streamable_http_handshake_and_origin_protection():
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(build_http_app(), base_url="http://localhost:8000") as client:
        response = client.post("/mcp", json=initialize, headers=headers)
        assert response.status_code == 200
        assert response.json()["result"]["protocolVersion"] == "2025-11-25"
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers={**headers, "MCP-Protocol-Version": "2025-11-25"})
        assert listed.status_code == 200
        assert len(listed.json()["result"]["tools"]) == 5
        denied = client.post("/mcp", json=initialize, headers={**headers, "Origin": "https://attacker.example"})
        assert denied.status_code == 403


def test_streamable_http_modern_stateless_discovery():
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    with TestClient(build_http_app(), base_url="http://localhost:8000") as client:
        for ident, method in ((1, "server/discover"), (2, "tools/list")):
            response = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": ident, "method": method, "params": {"_meta": meta}},
                headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method},
            )
            assert response.status_code == 200
            assert "mcp-session-id" not in response.headers
            if method == "tools/list":
                assert len(response.json()["result"]["tools"]) == 5
