import base64
import json
from urllib.parse import unquote

import pytest

from terra_classic_mcp.core import LcdClient, TerraError, require_account_address, require_address, require_page_key, require_query

ADDRESS = "terra18vd8fpwxzck93qlwghaj6arh4p7c5n896xzem5"
LONG_CONTRACT = "terra1genyaw32qah4544m0sfgmchegf7emft77u7hug63cc6a72mvpntsfupzmd"


class Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _size):
        return self.value


class Opener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        assert timeout == 15
        self.requests.append(request)
        return Response(next(self.responses))


def test_address_checksum_and_query_bounds():
    assert require_address(ADDRESS) == ADDRESS
    assert require_address(LONG_CONTRACT) == LONG_CONTRACT
    with pytest.raises(TerraError, match="account address"):
        require_account_address(LONG_CONTRACT)
    with pytest.raises(TerraError, match="checksum"):
        require_address(ADDRESS[:-1] + "q")
    with pytest.raises(TerraError):
        require_query({"x": "a" * 70000})
    with pytest.raises(TerraError, match="pagination key"):
        require_page_key("../bad")


def test_lcd_url_is_operator_controlled(monkeypatch):
    monkeypatch.setenv("TERRA_CLASSIC_LCD_URL", "http://example.com")
    with pytest.raises(TerraError, match="HTTP LCD"):
        LcdClient()
    monkeypatch.setenv("TERRA_CLASSIC_LCD_URL", "https://user:pass@example.com")
    with pytest.raises(TerraError, match="Invalid LCD URL"):
        LcdClient()


def test_chain_status_returns_only_small_fields():
    opener = Opener([{"block": {"header": {"chain_id": "columbus-5", "height": "123", "time": "now"}, "signatures": ["large"]}, "block_id": {"hash": "ABC"}}])
    result = LcdClient(base_url="https://lcd.example", opener=opener).chain_status()
    assert result == {"chain_id": "columbus-5", "height": "123", "time": "now", "block_id": "ABC"}
    assert opener.requests[0].get_method() == "GET"


def test_contract_query_uses_read_only_gateway_route():
    opener = Opener([{"data": {"name": "test"}}])
    result = LcdClient(base_url="https://lcd.example", opener=opener).contract_query(ADDRESS, {"token_info": {}})
    request = opener.requests[0]
    assert request.get_method() == "GET"
    encoded = unquote(request.full_url.rsplit("/", 1)[1])
    assert json.loads(base64.b64decode(encoded)) == {"token_info": {}}
    assert result == {"contract": ADDRESS, "data": {"name": "test"}}


def test_balance_reports_pagination():
    opener = Opener([{"balances": [{"denom": "uluna", "amount": "12"}], "pagination": {"next_key": "more"}}])
    result = LcdClient(base_url="https://lcd.example", opener=opener).balance(ADDRESS)
    assert result["balances"] == [{"denom": "uluna", "amount": "12"}]
    assert result["has_more"] is True


def test_transaction_is_bounded_summary():
    opener = Opener([{"tx_response": {"txhash": "A" * 64, "height": "4", "code": 0, "timestamp": "now", "gas_wanted": "100", "gas_used": "80", "logs": ["large"]}, "tx": {"body": {"messages": [{"@type": "/cosmos.bank.v1beta1.MsgSend", "value": "large"}]}}}])
    result = LcdClient(base_url="https://lcd.example", opener=opener).transaction("A" * 64)
    assert result["message_types"] == ["/cosmos.bank.v1beta1.MsgSend"]
    assert "logs" not in result


def test_validator_rank_uses_all_bonded_tokens():
    first = {"operator_address": "val1", "tokens": "100", "description": {"moniker": "One"}, "commission": {"commission_rates": {"rate": "0.05"}}}
    second = {"operator_address": "val2", "tokens": "300", "description": {"moniker": "Two"}, "commission": {"commission_rates": {"rate": "0.1"}}}
    opener = Opener([{"validators": [first], "pagination": {"next_key": "abc"}}, {"validators": [second], "pagination": {"next_key": ""}}])
    result = LcdClient(base_url="https://lcd.example", opener=opener).validators(limit=1)
    assert result["validator_count"] == 2
    assert result["validators"][0]["moniker"] == "Two"
    assert result["validators"][0]["voting_power_pct"] == 75.0


def test_delegations_use_validated_pagination():
    operator = "terravaloper1test"
    opener = Opener([{
        "delegation_responses": [{"delegation": {"validator_address": operator, "shares": "12"}, "balance": {"denom": "uluna", "amount": "10"}}],
        "pagination": {"next_key": "bmV4dA=="},
    }, {"delegation_responses": [], "pagination": {"next_key": None}}])
    client = LcdClient(base_url="https://lcd.example", opener=opener)
    first = client.delegations(ADDRESS, limit=1)
    assert first == {"address": ADDRESS, "delegations": [{"validator_address": operator, "balance": {"denom": "uluna", "amount": "10"}}], "next_key": "bmV4dA=="}
    assert opener.requests[0].get_method() == "GET"
    second = client.delegations(ADDRESS, limit=1, page_key=first["next_key"])
    assert second["delegations"] == []
    assert "pagination.key=bmV4dA%3D%3D" in opener.requests[1].full_url
    with pytest.raises(TerraError, match="pagination key"):
        client.delegations(ADDRESS, page_key="../bad")
    assert len(opener.requests) == 2


def test_unbonding_and_rewards_are_compact_reads():
    operator = "terravaloper1test"
    opener = Opener([
        {"unbonding_responses": [{"validator_address": operator, "entries": [{"balance": "5", "initial_balance": "6", "completion_time": "2026-12-01T00:00:00Z"}]}], "pagination": {"next_key": None}},
        {"rewards": [{"validator_address": operator, "reward": [{"denom": "uluna", "amount": "1.250000000000000000"}]}], "total": [{"denom": "uluna", "amount": "1.250000000000000000"}]},
    ])
    client = LcdClient(base_url="https://lcd.example", opener=opener)
    unbonding = client.unbonding_delegations(ADDRESS)
    rewards = client.staking_rewards(ADDRESS)
    assert unbonding["unbonding"][0]["entries"] == [{"balance": "5", "completion_time": "2026-12-01T00:00:00Z"}]
    assert rewards["total"][0]["amount"] == "1.250000000000000000"
    assert all(request.get_method() == "GET" for request in opener.requests)
    assert "/cosmos/staking/v1beta1/delegators/" in opener.requests[0].full_url
    assert "/cosmos/distribution/v1beta1/delegators/" in opener.requests[1].full_url


def test_governance_list_and_final_tally():
    proposal = {"id": "12", "title": "Test", "summary": "A" * 2100, "status": "PROPOSAL_STATUS_PASSED", "submit_time": "submitted", "voting_start_time": "started", "voting_end_time": "ended", "final_tally_result": {"yes_count": "10", "abstain_count": "1", "no_count": "2", "no_with_veto_count": "3"}}
    opener = Opener([{"proposals": [proposal], "pagination": {"next_key": "bmV4dA=="}}, {"proposal": proposal}])
    client = LcdClient(base_url="https://lcd.example", opener=opener)
    listed = client.governance_proposals(status="passed", limit=1)
    detail = client.governance_proposal(12)
    assert listed["proposals"] == [{"id": "12", "title": "Test", "status": "PROPOSAL_STATUS_PASSED", "voting_end_time": "ended"}]
    assert listed["next_key"] == "bmV4dA=="
    assert "pagination.reverse=true" in opener.requests[0].full_url
    assert "proposal_status=PROPOSAL_STATUS_PASSED" in opener.requests[0].full_url
    assert detail["tally_kind"] == "final"
    assert detail["tally"]["yes_count"] == "10"
    assert len(detail["summary"]) == 2000 and detail["summary_truncated"] is True
    assert len(opener.requests) == 2


def test_governance_voting_tally_is_live_and_inputs_are_bounded():
    proposal = {"id": "13", "title": "Voting", "summary": "Summary", "status": "PROPOSAL_STATUS_VOTING_PERIOD", "submit_time": "submitted", "voting_start_time": "started", "voting_end_time": "ended"}
    opener = Opener([{"proposal": proposal}, {"tally": {"yes_count": "10", "abstain_count": "0", "no_count": "1", "no_with_veto_count": "0"}}])
    client = LcdClient(base_url="https://lcd.example", opener=opener)
    detail = client.governance_proposal(13)
    assert detail["tally_kind"] == "current"
    assert detail["tally"]["no_count"] == "1"
    assert opener.requests[1].full_url.endswith("/cosmos/gov/v1/proposals/13/tally")
    with pytest.raises(TerraError, match="proposal ID"):
        client.governance_proposal(0)
    with pytest.raises(TerraError, match="proposal status"):
        client.governance_proposals(status="../bad")
    assert len(opener.requests) == 2


def test_missing_pagination_is_not_reported_as_complete():
    opener = Opener([{"proposals": []}])
    with pytest.raises(TerraError, match="pagination"):
        LcdClient(base_url="https://lcd.example", opener=opener).governance_proposals()
