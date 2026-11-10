import base64
import json
from urllib.parse import unquote

import pytest

from terra_classic_mcp.core import LcdClient, TerraError, require_address, require_query

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
    with pytest.raises(TerraError, match="checksum"):
        require_address(ADDRESS[:-1] + "q")
    with pytest.raises(TerraError):
        require_query({"x": "a" * 70000})


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
