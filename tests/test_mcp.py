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
    "terra_get_delegations", "terra_get_unbonding_delegations", "terra_get_staking_rewards",
    "terra_get_governance_proposals", "terra_get_governance_proposal",
    "terra_get_transaction", "terra_query_contract",
]


class FakeClient:
    def chain_status(self):
        return {"chain_id": "columbus-5", "height": "123", "time": "now", "block_id": "ABC"}

    def validators(self, limit=10):
        return {"status": "BOND_STATUS_BONDED", "validator_count": 1, "validators": [{"rank": 1, "moniker": "One", "operator_address": "val1", "tokens": "100", "voting_power_pct": 100.0, "commission_rate_pct": 5.0, "jailed": False, "website": ""}]}

    def balance(self, address):
        return {"address": address, "balances": [], "has_more": False}

    def delegations(self, address, limit=20, page_key=""):
        return {"address": address, "delegations": [{"validator_address": "terravaloper1test", "balance": {"denom": "uluna", "amount": "10"}}], "next_key": None}

    def unbonding_delegations(self, address, limit=20, page_key=""):
        return {"address": address, "unbonding": [{"validator_address": "terravaloper1test", "entries": [{"balance": "5", "completion_time": "2026-12-01T00:00:00Z"}]}], "next_key": None}

    def staking_rewards(self, address):
        return {"address": address, "rewards": [{"validator_address": "terravaloper1test", "reward": [{"denom": "uluna", "amount": "1.25"}]}], "total": [{"denom": "uluna", "amount": "1.25"}]}

    def governance_proposals(self, status="all", limit=10, page_key=""):
        return {"status_filter": status, "proposals": [{"id": "12", "title": "Test", "status": "PROPOSAL_STATUS_PASSED", "voting_end_time": "2026-11-10T00:00:00Z"}], "next_key": None}

    def governance_proposal(self, proposal_id):
        return {"proposal_id": str(proposal_id), "title": "Test", "summary": "Summary", "summary_truncated": False, "status": "PROPOSAL_STATUS_PASSED", "submit_time": "2026-11-01T00:00:00Z", "voting_start_time": "2026-11-02T00:00:00Z", "voting_end_time": "2026-11-10T00:00:00Z", "tally_kind": "final", "tally": {"yes_count": "10", "abstain_count": "0", "no_count": "1", "no_with_veto_count": "0"}}

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
            delegations = await client.call_tool("terra_get_delegations", {"address": ADDRESS})
            assert delegations.structured_content["delegations"][0]["balance"]["amount"] == "10"
            unbonding = await client.call_tool("terra_get_unbonding_delegations", {"address": ADDRESS})
            assert unbonding.structured_content["unbonding"][0]["entries"][0]["balance"] == "5"
            rewards = await client.call_tool("terra_get_staking_rewards", {"address": ADDRESS})
            assert rewards.structured_content["total"][0]["amount"] == "1.25"
            proposals = await client.call_tool("terra_get_governance_proposals", {"status": "passed", "limit": 1})
            assert proposals.structured_content["proposals"][0]["id"] == "12"
            proposal = await client.call_tool("terra_get_governance_proposal", {"proposal_id": 12})
            assert proposal.structured_content["tally_kind"] == "final"
            validators = await client.call_tool("terra_get_validators", {"limit": 1})
            assert validators.structured_content["validators"][0]["moniker"] == "One"
            transaction = await client.call_tool("terra_get_transaction", {"tx_hash": "a" * 64})
            assert transaction.structured_content["tx_hash"] == "A" * 64
            contract = await client.call_tool("terra_query_contract", {"contract": ADDRESS, "query_msg": {"token_info": {}}})
            assert contract.structured_content["data"] == {"token_info": {}}
            invalid = await client.call_tool("terra_get_balance", {"address": ADDRESS[:-1] + "q"})
            assert invalid.is_error is True
            invalid_delegation = await client.call_tool("terra_get_delegations", {"address": ADDRESS[:-1] + "q"})
            assert invalid_delegation.is_error is True
            invalid_page = await client.call_tool("terra_get_delegations", {"address": ADDRESS, "page_key": "../bad"})
            assert invalid_page.is_error is True
            invalid_status = await client.call_tool("terra_get_governance_proposals", {"status": "unknown"})
            assert invalid_status.is_error is True
            invalid_governance_page = await client.call_tool("terra_get_governance_proposals", {"page_key": "../bad"})
            assert invalid_governance_page.is_error is True
            invalid_id = await client.call_tool("terra_get_governance_proposal", {"proposal_id": 0})
            assert invalid_id.is_error is True
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
        assert len(listed.json()["result"]["tools"]) == len(NAMES)
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
                assert len(response.json()["result"]["tools"]) == len(NAMES)
