"""Validated, bounded reads from an operator-selected Terra Classic LCD."""

from __future__ import annotations

import base64
import binascii
from decimal import Decimal, InvalidOperation
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from . import __version__

DEFAULT_LCD_URL = "https://terra-classic-lcd.publicnode.com"
MAX_QUERY_BYTES = 64 * 1024
MAX_LCD_BYTES = 4 * 1024 * 1024
MAX_CALL_SECONDS = 30
CHAIN_STATUS_TTL_SECONDS = 10
MAX_VALIDATOR_PAGES = 5
MAX_SUPPLY_PAGES = 10
EXPECTED_CHAIN_ID = "columbus-5"
_deadline: ContextVar[float | None] = ContextVar("terra_lcd_deadline", default=None)
TX_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
DENOM_RE = re.compile(r"^[A-Za-z0-9/._:-]{1,128}$")
IBC_DENOM_RE = re.compile(r"^ibc/[A-Fa-f0-9]{64}$")
CHANNEL_RE = re.compile(r"^channel-[0-9]{1,12}$")
AMOUNT_RE = re.compile(r"^[0-9]{1,78}(?:\.[0-9]{1,18})?$")
INTEGER_AMOUNT_RE = re.compile(r"^[0-9]{1,78}$")
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


@contextmanager
def lcd_deadline(seconds: int = MAX_CALL_SECONDS):
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def _safe_connection(connection):
    allow_private = os.environ.get("TERRA_CLASSIC_LCD_ALLOW_PRIVATE_IP") == "true"
    try:
        addresses = socket.getaddrinfo(connection.host, connection.port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise TerraError("LCD DNS resolution failed") from exc
    for family, socktype, protocol, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        local_host = ip.is_loopback and connection.host in {"localhost", "127.0.0.1", "::1"}
        private_lcd = allow_private and ip.is_private and not (
            ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        )
        if not (ip.is_global or local_host or private_lcd):
            raise TerraError("LCD resolved to a disallowed IP address")
    if not addresses:
        raise TerraError("LCD DNS resolution failed")
    last_error = None
    for family, socktype, protocol, _, address in addresses:
        sock = socket.socket(family, socktype, protocol)
        try:
            sock.settimeout(connection.timeout)
            sock.connect(address)
            connection.sock = sock
            return
        except OSError as exc:
            last_error = exc
            sock.close()
    raise TerraError("LCD connection unavailable") from last_error


class SafeHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        _safe_connection(self)


class SafeHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        _safe_connection(self)
        try:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        except Exception:
            self.sock.close()
            raise


class SafeHTTPHandler(HTTPHandler):
    def http_open(self, request):
        return self.do_open(SafeHTTPConnection, request)


class SafeHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(SafeHTTPSConnection, request, context=self._context)


def _validated_lcd_url(value: str, allowed_hosts: str) -> str:
    value = value.rstrip("/")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise TerraError("Invalid LCD URL in server configuration") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise TerraError("Invalid LCD URL in server configuration")
    if parsed.scheme == "http" and os.environ.get("TERRA_CLASSIC_ALLOW_HTTP_LCD") != "true":
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise TerraError("HTTP LCD requires TERRA_CLASSIC_ALLOW_HTTP_LCD=true")
    allowed = {host.strip().lower() for host in allowed_hosts.split(",") if host.strip()}
    if parsed.hostname.lower() not in allowed:
        raise TerraError("LCD host is not in the operator allowlist")
    return value


def configured_lcd_url() -> str:
    return _validated_lcd_url(
        os.environ.get("TERRA_CLASSIC_LCD_URL", DEFAULT_LCD_URL),
        os.environ.get("TERRA_CLASSIC_LCD_ALLOWED_HOSTS", "terra-classic-lcd.publicnode.com"),
    )


def configured_secondary_lcd_url() -> str | None:
    value = os.environ.get("TERRA_CLASSIC_SECONDARY_LCD_URL")
    if not value:
        return None
    validated = _validated_lcd_url(value, os.environ.get("TERRA_CLASSIC_SECONDARY_LCD_ALLOWED_HOSTS", ""))
    if urlsplit(validated).hostname == urlsplit(configured_lcd_url()).hostname:
        raise TerraError("Secondary LCD must use a different configured host")
    return validated


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


def next_page_key(payload: dict, previous: str = "") -> str | None:
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        raise TerraError("Invalid LCD pagination")
    key = pagination.get("next_key")
    if key is None or key == "":
        return None
    key = require_page_key(key)
    if key == previous:
        raise TerraError("LCD pagination cursor did not advance")
    return key


def require_page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 20:
        raise TerraError("limit must be an integer between 1 and 20")
    return value


def require_denom(value: str) -> str:
    if not isinstance(value, str) or not DENOM_RE.fullmatch(value):
        raise TerraError("Invalid denomination")
    return value


def require_ibc_denom(value: str) -> str:
    if not isinstance(value, str) or not IBC_DENOM_RE.fullmatch(value):
        raise TerraError("IBC denomination must be ibc/ followed by 64 hexadecimal characters")
    return "ibc/" + value[4:].upper()


def require_channel(value: str) -> str:
    if not isinstance(value, str) or not CHANNEL_RE.fullmatch(value):
        raise TerraError("Invalid IBC channel ID")
    return value


def require_sequence(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise TerraError("IBC packet sequence must be a positive integer")
    return value


def require_allowlisted_cw20_contract(value: str) -> str:
    contract = require_address(value)
    configured = os.environ.get("TERRA_CLASSIC_CW20_CONTRACTS", "")
    contracts = [part.strip() for part in configured.split(",") if part.strip()]
    if len(contracts) > 20 or any(require_address(item) != item for item in contracts):
        raise TerraError("Invalid CW20 contract allowlist in server configuration")
    if contract not in contracts:
        raise TerraError("CW20 contract is not allowlisted by the server")
    return contract


def _coins(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 200:
        raise TerraError("Invalid LCD coin list")
    coins = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("denom"), str)
            or not DENOM_RE.fullmatch(item["denom"])
            or not isinstance(item.get("amount"), str)
            or not AMOUNT_RE.fullmatch(item["amount"])
        ):
            raise TerraError("Invalid LCD coin list")
        coins.append({"denom": item["denom"], "amount": item["amount"]})
    return coins


def validate_json_tree(value: object, *, max_depth: int = 12, max_nodes: int = 512, max_string: int = 4096) -> None:
    """Reject deeply nested or oversized untrusted JSON without changing its meaning."""
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise TerraError("JSON structure is too complex")
        if isinstance(item, dict):
            if len(item) > 100:
                raise TerraError("JSON object has too many fields")
            for key, child in item.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(char) < 32 for char in key):
                    raise TerraError("Invalid JSON field name")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > 200:
                raise TerraError("JSON array is too large")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > max_string:
                raise TerraError("JSON text is too large")
        elif item is None or isinstance(item, (bool, int)):
            if type(item) is int and item.bit_length() > 260:
                raise TerraError("JSON integer is too large")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise TerraError("Invalid JSON number")
        else:
            raise TerraError("Invalid JSON value")


def _reject_json_constant(_value: str):
    raise TerraError("Invalid JSON number")


def require_tx_hash(value: str) -> str:
    if not isinstance(value, str) or not TX_HASH_RE.fullmatch(value):
        raise TerraError("Transaction hash must contain 64 hexadecimal characters")
    return value.upper()


def require_query(value: dict) -> dict:
    if not isinstance(value, dict) or not value:
        raise TerraError("Contract query must be a non-empty JSON object")
    validate_json_tree(value, max_nodes=256)
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise TerraError("Invalid contract query JSON") from exc
    if len(encoded) > MAX_QUERY_BYTES:
        raise TerraError("Contract query is too large")
    return value


class LcdClient:
    def __init__(self, base_url: str | None = None, opener=None, source_label: str | None = None):
        self.base_url = (base_url or configured_lcd_url()).rstrip("/")
        self.source_label = source_label or os.environ.get("TERRA_CLASSIC_LCD_SOURCE_LABEL", "operator-configured LCD")
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect(), SafeHTTPHandler(), SafeHTTPSHandler())
        self._status_lock = threading.Lock()
        self._checked_at = 0.0
        self._cached_status = None
        self._validators_lock = threading.Lock()
        self._validators_checked_at = 0.0
        self._cached_validators = None

    def source_metadata(self) -> dict:
        if self._cached_status is None:
            raise TerraError("LCD chain status has not been verified")
        return {
            "lcd_id": self.source_label,
            "chain_id": self._cached_status["chain_id"],
            "verified_height": self._cached_status["height"],
            "verified_block_time": self._cached_status["time"],
        }

    def _raw_request(self, path: str, *, max_bytes: int = MAX_LCD_BYTES, allow_not_found: bool = False):
        deadline = _deadline.get()
        remaining = deadline - time.monotonic() if deadline is not None else 15
        if remaining <= 0:
            raise TerraError("LCD call deadline exceeded")
        timeout = min(15, remaining)
        request = Request(
            self.base_url + path,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": f"terra-classic-mcp/{__version__}"},
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = bytearray()
                reader = getattr(response, "read1", response.read)
                while len(raw) <= max_bytes:
                    if deadline is not None and time.monotonic() > deadline:
                        raise TerraError("LCD call deadline exceeded")
                    chunk = reader(min(65536, max_bytes + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
        except HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            raise TerraError("LCD request unavailable") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TerraError("LCD request unavailable") from exc
        if len(raw) > max_bytes:
            raise TerraError("LCD response is too large")
        if deadline is not None and time.monotonic() > deadline:
            raise TerraError("LCD call deadline exceeded")
        try:
            payload = json.loads(raw, parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError) as exc:
            raise TerraError("Invalid LCD JSON response") from exc
        validate_json_tree(payload, max_depth=32, max_nodes=30000, max_string=MAX_LCD_BYTES)
        return payload

    def _check_status(self, *, refresh: bool = False) -> dict:
        with self._status_lock:
            if not refresh and self._cached_status is not None and time.monotonic() - self._checked_at < CHAIN_STATUS_TTL_SECONDS:
                return self._cached_status
            payload = self._raw_request("/cosmos/base/tendermint/v1beta1/blocks/latest")
            try:
                header = payload["block"]["header"]
                result = {
                    "chain_id": header["chain_id"], "height": header["height"],
                    "time": header["time"], "block_id": payload["block_id"]["hash"],
                }
            except (KeyError, TypeError) as exc:
                raise TerraError("Invalid latest block response") from exc
            if not all(isinstance(value, str) and value for value in result.values()):
                raise TerraError("Invalid latest block response")
            expected = os.environ.get("TERRA_CLASSIC_EXPECTED_CHAIN_ID", EXPECTED_CHAIN_ID)
            if result["chain_id"] != expected or not re.fullmatch(r"[1-9][0-9]{0,19}", result["height"]):
                raise TerraError("LCD chain ID or block height does not match the configured network")
            try:
                block_time = datetime.fromisoformat(result["time"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise TerraError("Invalid latest block time") from exc
            if block_time.tzinfo is None:
                raise TerraError("Invalid latest block time")
            try:
                max_age = int(os.environ.get("TERRA_CLASSIC_MAX_BLOCK_AGE_SECONDS", "120"))
            except ValueError as exc:
                raise TerraError("Invalid maximum block age in server configuration") from exc
            age = (datetime.now(timezone.utc) - block_time).total_seconds()
            if max_age < 1 or age > max_age or age < -60:
                raise TerraError("LCD latest block is stale or in the future")
            self._cached_status = result
            self._checked_at = time.monotonic()
            return result

    def request(self, path: str, *, max_bytes: int = MAX_LCD_BYTES, allow_not_found: bool = False):
        if not path.startswith("/") or ".." in path or "#" in path:
            raise TerraError("Invalid LCD path")
        self._check_status()
        return self._raw_request(path, max_bytes=max_bytes, allow_not_found=allow_not_found)

    def chain_status(self) -> dict:
        return self._check_status(refresh=True)

    def balance(self, address: str, limit: int = 20, page_key: str = "") -> dict:
        address = require_address(address)
        limit = require_page_limit(limit)
        page_key = require_page_key(page_key)
        path = "/cosmos/bank/v1beta1/balances/" + quote(address, safe="") + f"?pagination.limit={limit}"
        if page_key:
            path += "&pagination.key=" + quote(page_key, safe="")
        payload = self.request(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("balances"), list) or len(payload["balances"]) > limit:
            raise TerraError("Invalid balance response")
        balances = _coins(payload["balances"])
        key = next_page_key(payload, page_key)
        return {"address": address, "balances": balances, "next_key": key, "has_more": key is not None}

    def denom_metadata(self, denom: str) -> dict:
        denom = require_denom(denom)
        payload = self.request("/cosmos/bank/v1beta1/denoms_metadata/" + quote(denom, safe=""), max_bytes=32 * 1024)
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict) or metadata.get("base") != denom:
            raise TerraError("Invalid bank denomination metadata")
        display, name, symbol = (metadata.get(key) for key in ("display", "name", "symbol"))
        if any(not isinstance(value, str) or not 1 <= len(value) <= 64 or any(ord(char) < 32 for char in value) for value in (display, name, symbol)):
            raise TerraError("Invalid bank denomination metadata")
        units = metadata.get("denom_units")
        if not isinstance(units, list) or not 1 <= len(units) <= 20:
            raise TerraError("Invalid bank denomination units")
        compact = []
        for unit in units:
            if not isinstance(unit, dict) or not isinstance(unit.get("denom"), str) or not DENOM_RE.fullmatch(unit["denom"]):
                raise TerraError("Invalid bank denomination units")
            exponent = unit.get("exponent")
            if type(exponent) is int:
                pass
            elif isinstance(exponent, str) and exponent.isascii() and exponent.isdigit():
                exponent = int(exponent)
            else:
                raise TerraError("Invalid bank denomination exponent")
            if not 0 <= exponent <= 36:
                raise TerraError("Invalid bank denomination exponent")
            compact.append({"denom": unit["denom"], "exponent": exponent})
        if not any(unit["denom"] == denom and unit["exponent"] == 0 for unit in compact):
            raise TerraError("Bank metadata is missing the base denomination")
        display_units = [unit for unit in compact if unit["denom"] == display]
        if len(display_units) != 1:
            raise TerraError("Bank metadata is missing the display denomination")
        return {"denom": denom, "display": display, "name": name, "symbol": symbol,
                "display_decimals": display_units[0]["exponent"], "denom_units": compact}

    def ibc_denom_trace(self, denom: str) -> dict:
        denom = require_ibc_denom(denom)
        payload = self.request("/ibc/apps/transfer/v1/denoms/" + denom[4:], max_bytes=32 * 1024)
        record = payload.get("denom") if isinstance(payload, dict) else None
        if not isinstance(record, dict):
            raise TerraError("Invalid IBC denomination trace")
        base, trace = record.get("base"), record.get("trace")
        if not isinstance(base, str) or not DENOM_RE.fullmatch(base) or not isinstance(trace, list) or not 1 <= len(trace) <= 8:
            raise TerraError("Invalid IBC denomination trace")
        hops = []
        for hop in trace:
            if not isinstance(hop, dict) or hop.get("port_id") != "transfer":
                raise TerraError("Invalid IBC denomination trace")
            hops.append({"port_id": "transfer", "channel_id": require_channel(hop.get("channel_id"))})
        full_trace = "/".join([part for hop in hops for part in (hop["port_id"], hop["channel_id"])] + [base])
        if hashlib.sha256(full_trace.encode()).hexdigest().upper() != denom[4:]:
            raise TerraError("IBC denomination trace hash mismatch")
        return {"denom": denom, "base_denom": base, "trace": hops, "full_trace": full_trace, "hash_verified": True}

    def balance_of_denom(self, address: str, denom: str) -> str:
        address, denom = require_account_address(address), require_denom(denom)
        cursor = ""
        seen_cursors: set[str] = set()
        amount = None
        for _ in range(5):
            page = self.balance(address, limit=20, page_key=cursor)
            for coin in page["balances"]:
                if coin["denom"] == denom:
                    if amount is not None:
                        raise TerraError("Duplicate denomination in LCD balances")
                    amount = coin["amount"]
            cursor = page["next_key"] or ""
            if not cursor:
                return amount or "0"
            if cursor in seen_cursors:
                raise TerraError("Balance pagination cycle")
            seen_cursors.add(cursor)
        raise TerraError("Balance comparison requires more than five LCD pages")

    def tax_parameters(self) -> dict:
        payload = self.request("/terra/tax/v1beta1/params", max_bytes=32 * 1024)
        params = payload.get("params") if isinstance(payload, dict) else None
        if not isinstance(params, dict):
            raise TerraError("Invalid tax parameters")
        rate = params.get("burn_tax_rate")
        if not isinstance(rate, str) or not re.fullmatch(r"[0-9]{1,2}(?:\.[0-9]{1,18})?", rate):
            raise TerraError("Invalid burn tax rate")
        try:
            if not Decimal(0) <= Decimal(rate) <= Decimal(1):
                raise TerraError("Invalid burn tax rate")
        except InvalidOperation as exc:
            raise TerraError("Invalid burn tax rate") from exc
        prices = _coins(params.get("gas_prices"))
        if len(prices) > 100 or len({coin["denom"] for coin in prices}) != len(prices):
            raise TerraError("Invalid tax gas prices")
        return {"burn_tax_rate": rate, "gas_prices": prices}

    def burn_snapshot(self) -> dict:
        # The single-denomination supply route is not implemented on some Terra Classic LCDs.
        cursor = ""
        seen: set[str] = set()
        uluna = None
        for _ in range(MAX_SUPPLY_PAGES):
            path = "/cosmos/bank/v1beta1/supply?pagination.limit=100"
            if cursor:
                path += "&pagination.key=" + quote(cursor, safe="")
            payload = self.request(path, max_bytes=128 * 1024)
            if not isinstance(payload, dict) or not isinstance(payload.get("supply"), list) or len(payload["supply"]) > 100:
                raise TerraError("Invalid bank supply response")
            for coin in _coins(payload["supply"]):
                if coin["denom"] == "uluna":
                    if uluna is not None or not INTEGER_AMOUNT_RE.fullmatch(coin["amount"]):
                        raise TerraError("Invalid uluna supply")
                    uluna = coin["amount"]
            next_key = next_page_key(payload, cursor)
            if not next_key:
                if uluna is None:
                    raise TerraError("uluna not found in complete bank supply")
                return {"denom": "uluna", "supply_base_units": uluna, "burn_tax_rate": self.tax_parameters()["burn_tax_rate"]}
            if next_key in seen:
                raise TerraError("Bank supply pagination cycle")
            seen.add(next_key)
            cursor = next_key
        raise TerraError("Bank supply requires too many pages")

    def authz_grants_received(self, address: str, limit: int = 20, page_key: str = "") -> dict:
        address = require_account_address(address)
        limit, page_key = require_page_limit(limit), require_page_key(page_key)
        path = "/cosmos/authz/v1beta1/grants/grantee/" + quote(address, safe="") + f"?pagination.limit={limit}"
        if page_key:
            path += "&pagination.key=" + quote(page_key, safe="")
        payload = self.request(path, max_bytes=128 * 1024)
        if not isinstance(payload, dict) or not isinstance(payload.get("grants"), list) or len(payload["grants"]) > limit:
            raise TerraError("Invalid authz grants response")
        grants = []
        for item in payload["grants"]:
            if not isinstance(item, dict) or not isinstance(item.get("authorization"), dict):
                raise TerraError("Invalid authz grant")
            authorization = item["authorization"]
            kind, granter, expiration = authorization.get("@type"), item.get("granter"), item.get("expiration")
            if not isinstance(kind, str) or not re.fullmatch(r"/[A-Za-z0-9_.]{1,128}", kind):
                raise TerraError("Invalid authz grant type")
            granter = require_account_address(granter)
            if not isinstance(expiration, str) or not 20 <= len(expiration) <= 50:
                raise TerraError("Invalid authz grant expiration")
            try:
                if datetime.fromisoformat(expiration.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("missing timezone")
            except ValueError as exc:
                raise TerraError("Invalid authz grant expiration") from exc
            validate_json_tree(authorization, max_depth=8, max_nodes=100, max_string=512)
            if len(json.dumps(authorization, separators=(",", ":")).encode()) > 4096:
                raise TerraError("Authz grant is too large")
            grants.append({"granter": granter, "authorization_type": kind, "authorization": authorization, "expiration": expiration})
        key = next_page_key(payload, page_key)
        return {"grantee": address, "grants": grants, "next_key": key, "has_more": key is not None}

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

    def explain_transaction(self, tx_hash: str) -> dict:
        tx_hash = require_tx_hash(tx_hash)
        payload = self.request("/cosmos/tx/v1beta1/txs/" + tx_hash, max_bytes=512 * 1024)
        try:
            response, body = payload["tx_response"], payload["tx"]["body"]
            if require_tx_hash(response["txhash"]) != tx_hash or type(response["code"]) is not int or response["code"] < 0:
                raise TerraError("Invalid transaction response")
            messages = body["messages"]
            if not isinstance(messages, list) or len(messages) > 50:
                raise TerraError("Invalid transaction messages")
            fee = payload["tx"]["auth_info"]["fee"]
            fee_amount = _coins(fee["amount"])
            if len(fee_amount) > 20 or any(not INTEGER_AMOUNT_RE.fullmatch(coin["amount"]) for coin in fee_amount):
                raise TerraError("Invalid transaction fee")
            gas_limit = fee["gas_limit"]
            if not isinstance(gas_limit, str) or not INTEGER_AMOUNT_RE.fullmatch(gas_limit):
                raise TerraError("Invalid transaction gas limit")
            memo = body.get("memo", "")
            if not isinstance(memo, str):
                raise TerraError("Invalid transaction memo")
            compact = []
            for index, message in enumerate(messages):
                if not isinstance(message, dict):
                    raise TerraError("Invalid transaction message")
                kind = message.get("@type")
                if not isinstance(kind, str) or not re.fullmatch(r"/[A-Za-z0-9_.]{1,128}", kind):
                    raise TerraError("Invalid transaction message type")
                detail = {"index": index, "type_url": kind, "kind": "other", "addresses": [], "amount": [], "proposal_id": None, "vote_option": None}
                if kind == "/cosmos.bank.v1beta1.MsgSend":
                    detail.update(kind="bank_send", addresses=[require_address(message["from_address"]), require_address(message["to_address"])], amount=_coins(message["amount"]))
                elif kind in {"/cosmos.staking.v1beta1.MsgDelegate", "/cosmos.staking.v1beta1.MsgUndelegate"}:
                    validator = message["validator_address"]
                    if not isinstance(validator, str) or not re.fullmatch(r"terravaloper1[023456789acdefghjklmnpqrstuvwxyz]{38}", validator):
                        raise TerraError("Invalid transaction validator address")
                    detail.update(kind="delegate" if kind.endswith("MsgDelegate") else "undelegate", addresses=[require_account_address(message["delegator_address"]), validator], amount=_coins([message["amount"]]))
                elif kind in {"/cosmos.gov.v1.MsgVote", "/cosmos.gov.v1beta1.MsgVote"}:
                    proposal_id, option = message["proposal_id"], message["option"]
                    if not isinstance(proposal_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", proposal_id) or not isinstance(option, str) or not re.fullmatch(r"VOTE_OPTION_[A-Z_]{1,32}", option):
                        raise TerraError("Invalid transaction vote")
                    detail.update(kind="vote", addresses=[require_account_address(message["voter"])], proposal_id=proposal_id, vote_option=option)
                if len(detail["amount"]) > 20 or any(not INTEGER_AMOUNT_RE.fullmatch(coin["amount"]) for coin in detail["amount"]):
                    raise TerraError("Invalid transaction amount")
                compact.append(detail)
            return {"tx_hash": tx_hash, "height": response["height"], "code": response["code"], "timestamp": response["timestamp"],
                    "gas_limit": gas_limit, "fee": fee_amount, "memo": memo[:256], "memo_truncated": len(memo) > 256, "messages": compact}
        except (KeyError, TypeError, IndexError) as exc:
            raise TerraError("Invalid transaction response") from exc

    def ibc_transfer_transaction(self, tx_hash: str) -> dict:
        tx_hash = require_tx_hash(tx_hash)
        payload = self.request("/cosmos/tx/v1beta1/txs/" + tx_hash, max_bytes=512 * 1024)
        response = payload.get("tx_response") if isinstance(payload, dict) else None
        if (not isinstance(response, dict) or not isinstance(response.get("txhash"), str)
                or require_tx_hash(response["txhash"]) != tx_hash or type(response.get("code")) is not int):
            raise TerraError("Invalid IBC transaction response")
        events = response.get("events")
        if not isinstance(events, list) or len(events) > 200:
            raise TerraError("Invalid IBC transaction events")
        packets = []
        for event in events:
            if not isinstance(event, dict):
                raise TerraError("Invalid IBC transaction event")
            if event.get("type") != "send_packet":
                continue
            attributes = event.get("attributes")
            if not isinstance(attributes, list) or len(attributes) > 30:
                raise TerraError("Invalid IBC packet event")
            values = {}
            for attribute in attributes:
                if not isinstance(attribute, dict) or not isinstance(attribute.get("key"), str) or not isinstance(attribute.get("value"), str):
                    raise TerraError("Invalid IBC packet event")
                key, value = attribute["key"], attribute["value"]
                if len(key) > 64 or len(value) > 256 or key in values:
                    raise TerraError("Invalid IBC packet event")
                values[key] = value
            try:
                raw_sequence = values["packet_sequence"]
                if not re.fullmatch(r"[1-9][0-9]{0,18}", raw_sequence):
                    raise ValueError("Invalid packet sequence")
                sequence = require_sequence(int(raw_sequence))
                source_channel = require_channel(values["packet_src_channel"])
                destination_channel = require_channel(values["packet_dst_channel"])
                if values["packet_src_port"] != "transfer" or values["packet_dst_port"] != "transfer":
                    raise TerraError("Not an ICS-20 transfer packet")
            except (KeyError, ValueError) as exc:
                raise TerraError("Invalid IBC packet event") from exc
            packets.append({"sequence": sequence, "source_channel": source_channel, "destination_channel": destination_channel})
            if len(packets) > 10:
                raise TerraError("Too many IBC transfer packets in transaction")
        return {"tx_hash": tx_hash, "tx_code": response["code"], "packets": packets}

    def ibc_packet_observation(self, channel_id: str, sequence: int, local_role: str) -> dict:
        channel_id, sequence = require_channel(channel_id), require_sequence(sequence)
        if local_role not in {"source", "destination"}:
            raise TerraError("Invalid IBC local role")
        base = f"/ibc/core/channel/v1/channels/{channel_id}/ports/transfer"
        if local_role == "source":
            payload = self.request(f"{base}/packet_commitments/{sequence}", max_bytes=16 * 1024, allow_not_found=True)
            if payload is None:
                observation = "not_found_or_unsupported"
            elif isinstance(payload, dict) and isinstance(payload.get("commitment"), str) and 1 <= len(payload["commitment"]) <= 512:
                try:
                    base64.b64decode(payload["commitment"], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise TerraError("Invalid IBC packet commitment response") from exc
                observation = "commitment_present"
            else:
                raise TerraError("Invalid IBC packet commitment response")
        else:
            receipt = self.request(f"{base}/packet_receipts/{sequence}", max_bytes=16 * 1024, allow_not_found=True)
            acknowledgement = self.request(f"{base}/packet_acks/{sequence}", max_bytes=16 * 1024, allow_not_found=True)
            if receipt is not None and (not isinstance(receipt, dict) or type(receipt.get("received")) is not bool):
                raise TerraError("Invalid IBC packet receipt response")
            if acknowledgement is not None and (not isinstance(acknowledgement, dict) or not isinstance(acknowledgement.get("acknowledgement"), str)
                                                or not 1 <= len(acknowledgement["acknowledgement"]) <= 4096):
                raise TerraError("Invalid IBC packet acknowledgement response")
            if acknowledgement is not None:
                try:
                    base64.b64decode(acknowledgement["acknowledgement"], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise TerraError("Invalid IBC packet acknowledgement response") from exc
            observation = "acknowledgement_present" if acknowledgement is not None else "receipt_present" if receipt and receipt["received"] else "not_found_or_unsupported"
        return {"channel_id": channel_id, "sequence": sequence, "local_role": local_role,
                "observation": observation, "transfer_outcome_confirmed": False,
                "commitment_observed": payload is not None if local_role == "source" else None,
                "receipt_observed": receipt["received"] if local_role == "destination" and receipt is not None else None,
                "acknowledgement_observed": acknowledgement is not None if local_role == "destination" else None}

    def contract_query(self, contract: str, query_msg: dict) -> dict:
        raw = json.dumps(query_msg, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        query_data = quote(base64.b64encode(raw).decode(), safe="")
        path = "/cosmwasm/wasm/v1/contract/" + quote(contract, safe="") + "/smart/" + query_data
        if len(path) > 8192:
            raise TerraError("Contract query is too large for the LCD route")
        payload = self.request(path)
        if not isinstance(payload, dict) or "data" not in payload:
            raise TerraError("Invalid contract response")
        validate_json_tree(payload["data"])
        return {"contract": contract, "data": payload["data"]}

    def cw20_token_info(self, contract: str) -> dict:
        contract = require_allowlisted_cw20_contract(contract)
        data = self.contract_query(contract, {"token_info": {}})["data"]
        if not isinstance(data, dict):
            raise TerraError("Invalid CW20 token information")
        name, symbol, decimals, supply = (data.get(key) for key in ("name", "symbol", "decimals", "total_supply"))
        if (not isinstance(name, str) or not 1 <= len(name) <= 128 or any(ord(char) < 32 for char in name)
                or not isinstance(symbol, str) or not 1 <= len(symbol) <= 32 or any(ord(char) < 32 for char in symbol)
                or type(decimals) is not int or not 0 <= decimals <= 18
                or not isinstance(supply, str) or not re.fullmatch(r"[0-9]{1,78}", supply)):
            raise TerraError("Invalid CW20 token information")
        return {"contract": contract, "name": name, "symbol": symbol, "decimals": decimals, "total_supply": supply}

    def cw20_balance(self, contract: str, address: str) -> dict:
        contract = require_allowlisted_cw20_contract(contract)
        address = require_account_address(address)
        data = self.contract_query(contract, {"balance": {"address": address}})["data"]
        if not isinstance(data, dict) or not isinstance(data.get("balance"), str) or not re.fullmatch(r"[0-9]{1,78}", data["balance"]):
            raise TerraError("Invalid CW20 balance response")
        return {"contract": contract, "address": address, "balance": data["balance"]}

    def validators(self, limit: int = 10) -> dict:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise TerraError("limit must be an integer between 1 and 20")
        self._check_status()
        with self._validators_lock:
            if self._cached_validators is None or time.monotonic() - self._validators_checked_at >= 10:
                self._cached_validators = self._fetch_validators()
                self._validators_checked_at = time.monotonic()
            return {**self._cached_validators, "validators": self._cached_validators["validators"][:limit]}

    def validator_page(self, page: int = 1, limit: int = 20) -> dict:
        if type(page) is not int or not 1 <= page <= 500:
            raise TerraError("page must be an integer between 1 and 500")
        limit = require_page_limit(limit)
        self._check_status()
        with self._validators_lock:
            if self._cached_validators is None or time.monotonic() - self._validators_checked_at >= 10:
                self._cached_validators = self._fetch_validators()
                self._validators_checked_at = time.monotonic()
            data = self._cached_validators
            start = (page - 1) * limit
            end = start + limit
            return {"status": data["status"], "validator_count": data["validator_count"], "page": page, "limit": limit,
                    "next_page": page + 1 if end < data["validator_count"] else None, "validators": data["validators"][start:end]}

    def _fetch_validators(self) -> dict:
        all_validators: list[dict] = []
        next_key = None
        seen_keys: set[str] = set()
        for _ in range(MAX_VALIDATOR_PAGES):
            path = "/cosmos/staking/v1beta1/validators?status=BOND_STATUS_BONDED&pagination.limit=100"
            if next_key:
                path += "&pagination.key=" + quote(next_key, safe="")
            page = self.request(path)
            if not isinstance(page, dict) or not isinstance(page.get("validators"), list) or len(page["validators"]) > 100:
                raise TerraError("Invalid validator response")
            all_validators.extend(page["validators"])
            next_key = next_page_key(page, next_key or "")
            if next_key in seen_keys:
                raise TerraError("Validator pagination cycle")
            if next_key:
                seen_keys.add(next_key)
            if not next_key:
                break
        if next_key or not all_validators:
            raise TerraError("Validator set is incomplete")
        try:
            ranked = sorted(all_validators, key=lambda item: int(item["tokens"]), reverse=True)
            total_tokens = sum(int(item["tokens"]) for item in ranked)
            validators = []
            for rank, item in enumerate(ranked, 1):
                tokens = int(item["tokens"])
                description = item.get("description") or {}
                rate = (item.get("commission") or {}).get("commission_rates") or {}
                moniker = description.get("moniker") or item["operator_address"]
                website = description.get("website") or ""
                if not isinstance(moniker, str) or len(moniker) > 128 or not isinstance(website, str) or len(website) > 256:
                    raise TerraError("Invalid validator description")
                validators.append({
                    "rank": rank,
                    "moniker": moniker,
                    "operator_address": item["operator_address"],
                    "tokens": str(tokens),
                    "voting_power_pct": round(tokens * 100 / total_tokens, 6) if total_tokens else 0.0,
                    "commission_rate_pct": round(float(rate.get("rate", 0)) * 100, 6),
                    "jailed": bool(item.get("jailed", False)),
                    "website": website,
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
        return {"address": address, "delegations": delegations, "next_key": next_page_key(payload, page_key)}

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
        return {"address": address, "unbonding": unbonding, "next_key": next_page_key(payload, page_key)}

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
            if len(item["title"]) > 256:
                raise TerraError("Governance proposal title is too large")
            proposals.append({key: item[key] for key in ("id", "title", "status", "voting_end_time")})
        return {"status_filter": status, "proposals": proposals, "next_key": next_page_key(payload, page_key)}

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
        if len(item["title"]) > 256:
            raise TerraError("Governance proposal title is too large")
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


def compare_chain_status(primary: LcdClient, secondary: LcdClient) -> dict:
    first, second = primary.chain_status(), secondary.chain_status()
    same_height = first["height"] == second["height"]
    same_block = first["block_id"] == second["block_id"] if same_height else None
    verdict = "agree_observed" if same_block else "divergent_observation" if same_block is False else "inconclusive_height_mismatch"
    return {"primary_height": first["height"], "secondary_height": second["height"],
            "same_block": same_block, "verdict": verdict, "secondary_source": secondary.source_metadata()}


def compare_balance(primary: LcdClient, secondary: LcdClient, address: str, denom: str) -> dict:
    address, denom = require_account_address(address), require_denom(denom)
    first = primary.balance_of_denom(address, denom)
    second = secondary.balance_of_denom(address, denom)
    same_height = primary.source_metadata()["verified_height"] == secondary.source_metadata()["verified_height"]
    verdict = "inconclusive_height_mismatch" if not same_height else "agree_observed" if first == second else "divergent_observation"
    return {"address": address, "denom": denom, "primary_amount": first, "secondary_amount": second,
            "verdict": verdict, "secondary_source": secondary.source_metadata()}
