"""Validated, bounded reads from an operator-selected Terra Classic LCD."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import __version__

DEFAULT_LCD_URL = "https://terra-classic-lcd.publicnode.com"
MAX_QUERY_BYTES = 64 * 1024
MAX_LCD_BYTES = 4 * 1024 * 1024
TX_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
PROPOSAL_STATUSES = {
    "all": "",
    "voting": "PROPOSAL_STATUS_VOTING_PERIOD",
    "deposit": "PROPOSAL_STATUS_DEPOSIT_PERIOD",
    "passed": "PROPOSAL_STATUS_PASSED",
    "rejected": "PROPOSAL_STATUS_REJECTED",
    "failed": "PROPOSAL_STATUS_FAILED",
}


class TerraError(Exception):
    """A rejected input or an unusable LCD response."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise TerraError("LCD redirects are not allowed")


def configured_lcd_url() -> str:
    value = os.environ.get("TERRA_CLASSIC_LCD_URL", DEFAULT_LCD_URL).rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise TerraError("Invalid LCD URL in server configuration")
    if parsed.scheme == "http" and os.environ.get("TERRA_CLASSIC_ALLOW_HTTP_LCD") != "true":
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise TerraError("HTTP LCD requires TERRA_CLASSIC_ALLOW_HTTP_LCD=true")
    return value


def _bech32_polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, generator in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if (top >> i) & 1:
                chk ^= generator
    return chk


def require_address(value: str) -> str:
    # Terra Classic account addresses use 20 bytes; CosmWasm contracts may use 32.
    if not isinstance(value, str) or len(value) not in {44, 64} or not value.startswith("terra1"):
        raise TerraError("Invalid Terra Classic address")
    try:
        data = [BECH32_ALPHABET.index(char) for char in value[6:]]
    except ValueError as exc:
        raise TerraError("Invalid Terra Classic address") from exc
    if _bech32_polymod([ord(char) >> 5 for char in "terra"] + [0] + [ord(char) & 31 for char in "terra"] + data) != 1:
        raise TerraError("Invalid Terra Classic address checksum")
    if len(value) == 64 and data[-7] & 0x0F:
        raise TerraError("Invalid Terra Classic address padding")
    return value


def require_account_address(value: str) -> str:
    address = require_address(value)
    if len(address) != 44:
        raise TerraError("Expected a 44-character Terra Classic account address")
    return address


def require_page_key(value: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise TerraError("Invalid pagination key")
    if value:
        try:
            if not base64.b64decode(value, validate=True):
                raise TerraError("Invalid pagination key")
        except (ValueError, binascii.Error) as exc:
            raise TerraError("Invalid pagination key") from exc
    return value


def next_page_key(payload: dict) -> str | None:
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        raise TerraError("Invalid LCD pagination")
    key = pagination.get("next_key")
    if key is None or key == "":
        return None
    return require_page_key(key)


def require_page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 20:
        raise TerraError("limit must be an integer between 1 and 20")
    return value


def _coins(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 200:
        raise TerraError("Invalid LCD coin list")
    coins = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("denom"), str)
            or not item["denom"]
            or not isinstance(item.get("amount"), str)
            or not item["amount"]
        ):
            raise TerraError("Invalid LCD coin list")
        coins.append({"denom": item["denom"], "amount": item["amount"]})
    return coins


def require_tx_hash(value: str) -> str:
    if not isinstance(value, str) or not TX_HASH_RE.fullmatch(value):
        raise TerraError("Transaction hash must contain 64 hexadecimal characters")
    return value.upper()


def require_query(value: dict) -> dict:
    if not isinstance(value, dict) or not value:
        raise TerraError("Contract query must be a non-empty JSON object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise TerraError("Invalid contract query JSON") from exc
    if len(encoded) > MAX_QUERY_BYTES:
        raise TerraError("Contract query is too large")
    return value


class LcdClient:
    def __init__(self, base_url: str | None = None, opener=None):
        self.base_url = (base_url or configured_lcd_url()).rstrip("/")
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path: str, *, max_bytes: int = MAX_LCD_BYTES):
        if not path.startswith("/") or ".." in path or "#" in path:
            raise TerraError("Invalid LCD path")
        request = Request(
            self.base_url + path,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": f"terra-classic-mcp/{__version__}"},
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(max_bytes + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise TerraError("LCD request unavailable") from exc
        if len(raw) > max_bytes:
            raise TerraError("LCD response is too large")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise TerraError("Invalid LCD JSON response") from exc

    def chain_status(self) -> dict:
        payload = self.request("/cosmos/base/tendermint/v1beta1/blocks/latest")
        try:
            header = payload["block"]["header"]
            result = {
                "chain_id": header["chain_id"],
                "height": header["height"],
                "time": header["time"],
                "block_id": payload["block_id"]["hash"],
            }
        except (KeyError, TypeError) as exc:
            raise TerraError("Invalid latest block response") from exc
        if not all(isinstance(value, str) and value for value in result.values()):
            raise TerraError("Invalid latest block response")
        return result

    def balance(self, address: str) -> dict:
        payload = self.request(
            "/cosmos/bank/v1beta1/balances/" + quote(address, safe="") + "?pagination.limit=100"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("balances"), list):
            raise TerraError("Invalid balance response")
        balances = []
        for coin in payload["balances"]:
            if not isinstance(coin, dict) or not isinstance(coin.get("denom"), str) or not isinstance(coin.get("amount"), str):
                raise TerraError("Invalid balance response")
            balances.append({"denom": coin["denom"], "amount": coin["amount"]})
        pagination = payload.get("pagination") or {}
        if not isinstance(pagination, dict):
            raise TerraError("Invalid balance pagination")
        return {"address": address, "balances": balances, "has_more": bool(pagination.get("next_key"))}

    def transaction(self, tx_hash: str) -> dict:
        payload = self.request("/cosmos/tx/v1beta1/txs/" + quote(tx_hash, safe=""))
        try:
            response = payload["tx_response"]
            body = payload["tx"]["body"]
            messages = body["messages"]
            if not isinstance(messages, list) or len(messages) > 100 or not all(isinstance(item, dict) for item in messages):
                raise TerraError("Invalid transaction messages")
            result = {
                "tx_hash": response["txhash"],
                "height": response["height"],
                "code": response["code"],
                "timestamp": response["timestamp"],
                "gas_wanted": response["gas_wanted"],
                "gas_used": response["gas_used"],
                "message_types": [message.get("@type", "unknown") for message in messages],
            }
        except (KeyError, TypeError) as exc:
            raise TerraError("Invalid transaction response") from exc
        return result

    def contract_query(self, contract: str, query_msg: dict) -> dict:
        raw = json.dumps(query_msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        query_data = quote(base64.b64encode(raw).decode(), safe="")
        path = "/cosmwasm/wasm/v1/contract/" + quote(contract, safe="") + "/smart/" + query_data
        if len(path) > 8192:
            raise TerraError("Contract query is too large for the LCD route")
        payload = self.request(path)
        if not isinstance(payload, dict) or "data" not in payload:
            raise TerraError("Invalid contract response")
        return {"contract": contract, "data": payload["data"]}

    def validators(self, limit: int = 10) -> dict:
        all_validators: list[dict] = []
        next_key = None
        for _ in range(20):
            path = "/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED&pagination.limit=100"
            if next_key:
                path += "&pagination.key=" + quote(next_key, safe="")
            page = self.request(path)
            if not isinstance(page, dict) or not isinstance(page.get("validators"), list):
                raise TerraError("Invalid validator response")
            all_validators.extend(page["validators"])
            pagination = page.get("pagination") or {}
            if not isinstance(pagination, dict):
                raise TerraError("Invalid validator pagination")
            next_key = pagination.get("next_key")
            if not next_key:
                break
        if next_key or not all_validators:
            raise TerraError("Validator set is incomplete")
        try:
            ranked = sorted(all_validators, key=lambda item: int(item["tokens"]), reverse=True)
            total_tokens = sum(int(item["tokens"]) for item in ranked)
            validators = []
            for rank, item in enumerate(ranked[:limit], 1):
                tokens = int(item["tokens"])
                description = item.get("description") or {}
                rate = (item.get("commission") or {}).get("commission_rates") or {}
                validators.append({
                    "rank": rank,
                    "moniker": description.get("moniker") or item["operator_address"],
                    "operator_address": item["operator_address"],
                    "tokens": str(tokens),
                    "voting_power_pct": round(tokens * 100 / total_tokens, 6) if total_tokens else 0.0,
                    "commission_rate_pct": round(float(rate.get("rate", 0)) * 100, 6),
                    "jailed": bool(item.get("jailed", False)),
                    "website": description.get("website") or "",
                })
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise TerraError("Invalid validator response") from exc
        return {"status": "BOND_STATUS_BONDED", "validator_count": len(ranked), "validators": validators}

    def delegations(self, address: str, limit: int = 20, page_key: str = "") -> dict:
        address = require_account_address(address)
        limit = require_page_limit(limit)
        page_key = require_page_key(page_key)
        path = f"/cosmos/staking/v1beta1/delegations/{quote(address, safe='')}?pagination.limit={limit}"
        if page_key:
            path += "&pagination.key=" + quote(page_key, safe="")
        payload = self.request(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("delegation_responses"), list):
            raise TerraError("Invalid delegation response")
        items = payload["delegation_responses"]
        if len(items) > limit:
            raise TerraError("Invalid delegation response")
        delegations = []
        for item in items:
            try:
                validator = item["delegation"]["validator_address"]
                balance = _coins([item["balance"]])[0]
            except (KeyError, TypeError, IndexError) as exc:
                raise TerraError("Invalid delegation response") from exc
            if not isinstance(validator, str) or not validator.startswith("terravaloper1"):
                raise TerraError("Invalid delegation response")
            delegations.append({"validator_address": validator, "balance": balance})
        return {"address": address, "delegations": delegations, "next_key": next_page_key(payload)}

    def unbonding_delegations(self, address: str, limit: int = 20, page_key: str = "") -> dict:
        address = require_account_address(address)
        limit = require_page_limit(limit)
        page_key = require_page_key(page_key)
        path = f"/cosmos/staking/v1beta1/delegators/{quote(address, safe='')}/unbonding_delegations?pagination.limit={limit}"
        if page_key:
            path += "&pagination.key=" + quote(page_key, safe="")
        payload = self.request(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("unbonding_responses"), list):
            raise TerraError("Invalid unbonding response")
        items = payload["unbonding_responses"]
        if len(items) > limit:
            raise TerraError("Invalid unbonding response")
        unbonding = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("validator_address"), str) or not item["validator_address"].startswith("terravaloper1"):
                raise TerraError("Invalid unbonding response")
            entries = item.get("entries")
            if not isinstance(entries, list) or len(entries) > 100:
                raise TerraError("Invalid unbonding response")
            compact_entries = []
            for entry in entries:
                if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key] for key in ("balance", "completion_time")):
                    raise TerraError("Invalid unbonding response")
                compact_entries.append({"balance": entry["balance"], "completion_time": entry["completion_time"]})
            unbonding.append({"validator_address": item["validator_address"], "entries": compact_entries})
        return {"address": address, "unbonding": unbonding, "next_key": next_page_key(payload)}

    def staking_rewards(self, address: str) -> dict:
        address = require_account_address(address)
        path = f"/cosmos/distribution/v1beta1/delegators/{quote(address, safe='')}/rewards"
        payload = self.request(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("rewards"), list) or len(payload["rewards"]) > 200:
            raise TerraError("Invalid staking rewards response")
        rewards = []
        for item in payload["rewards"]:
            if not isinstance(item, dict) or not isinstance(item.get("validator_address"), str) or not item["validator_address"].startswith("terravaloper1"):
                raise TerraError("Invalid staking rewards response")
            rewards.append({"validator_address": item["validator_address"], "reward": _coins(item.get("reward"))})
        return {"address": address, "rewards": rewards, "total": _coins(payload.get("total"))}

    def governance_proposals(self, status: str = "all", limit: int = 10, page_key: str = "") -> dict:
        if not isinstance(status, str) or status not in PROPOSAL_STATUSES:
            raise TerraError("Invalid proposal status")
        limit = require_page_limit(limit)
        page_key = require_page_key(page_key)
        path = f"/cosmos/gov/v1/proposals?pagination.limit={limit}&pagination.reverse=true"
        if status != "all":
            path += "&proposal_status=" + PROPOSAL_STATUSES[status]
        if page_key:
            path += "&pagination.key=" + quote(page_key, safe="")
        payload = self.request(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("proposals"), list):
            raise TerraError("Invalid governance proposals response")
        items = payload["proposals"]
        if len(items) > limit:
            raise TerraError("Invalid governance proposals response")
        proposals = []
        for item in items:
            if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ("id", "title", "status", "voting_end_time")):
                raise TerraError("Invalid governance proposals response")
            proposals.append({key: item[key] for key in ("id", "title", "status", "voting_end_time")})
        return {"status_filter": status, "proposals": proposals, "next_key": next_page_key(payload)}

    def governance_proposal(self, proposal_id: int) -> dict:
        if type(proposal_id) is not int or not 1 <= proposal_id <= 9223372036854775807:
            raise TerraError("Invalid proposal ID")
        payload = self.request(f"/cosmos/gov/v1/proposals/{proposal_id}")
        if not isinstance(payload, dict) or not isinstance(payload.get("proposal"), dict):
            raise TerraError("Invalid governance proposal response")
        item = payload["proposal"]
        fields = ("id", "title", "summary", "status", "submit_time", "voting_start_time", "voting_end_time")
        if any(not isinstance(item.get(key), str) for key in fields) or item["id"] != str(proposal_id):
            raise TerraError("Invalid governance proposal response")
        tally_kind = "none"
        tally = None
        if item["status"] == "PROPOSAL_STATUS_VOTING_PERIOD":
            current = self.request(f"/cosmos/gov/v1/proposals/{proposal_id}/tally")
            if not isinstance(current, dict):
                raise TerraError("Invalid governance tally response")
            tally = current.get("tally")
            tally_kind = "current"
        elif item["status"] in {"PROPOSAL_STATUS_PASSED", "PROPOSAL_STATUS_REJECTED", "PROPOSAL_STATUS_FAILED"}:
            tally = item.get("final_tally_result")
            tally_kind = "final"
        if tally_kind != "none":
            keys = ("yes_count", "abstain_count", "no_count", "no_with_veto_count")
            if not isinstance(tally, dict) or any(not isinstance(tally.get(key), str) for key in keys):
                raise TerraError("Invalid governance tally response")
            tally = {key: tally[key] for key in keys}
        summary = item["summary"]
        return {
            "proposal_id": item["id"],
            "title": item["title"],
            "summary": summary[:2000],
            "summary_truncated": len(summary) > 2000,
            "status": item["status"],
            "submit_time": item["submit_time"],
            "voting_start_time": item["voting_start_time"],
            "voting_end_time": item["voting_end_time"],
            "tally_kind": tally_kind,
            "tally": tally,
        }
