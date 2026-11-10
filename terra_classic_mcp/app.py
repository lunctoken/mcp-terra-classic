"""One tool registry shared by the stdio and HTTP transports."""

from __future__ import annotations

from typing import Annotated, Any, Callable, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .core import MAX_QUERY_BYTES, LcdClient, TerraError, require_address, require_query, require_tx_hash


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChainStatus(Result):
    chain_id: str
    height: str
    time: str
    block_id: str


class Coin(Result):
    denom: str
    amount: str


class Balance(Result):
    address: str
    balances: list[Coin]
    has_more: bool


class Transaction(Result):
    tx_hash: str
    height: str
    code: int
    timestamp: str
    gas_wanted: str
    gas_used: str
    message_types: list[str]


class ContractQuery(Result):
    contract: str
    data: Any


class Validator(Result):
    rank: int
    moniker: str
    operator_address: str
    tokens: str
    voting_power_pct: float
    commission_rate_pct: float
    jailed: bool
    website: str


class Validators(Result):
    status: str
    validator_count: int
    validators: list[Validator]


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

ALLOWED_ARGUMENTS = {
    "terra_get_chain_status": frozenset(),
    "terra_get_validators": frozenset({"limit"}),
    "terra_get_balance": frozenset({"address"}),
    "terra_get_transaction": frozenset({"tx_hash"}),
    "terra_query_contract": frozenset({"contract", "query_msg"}),
}


class TerraMCPServer(MCPServer):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema = {**tool.input_schema, "additionalProperties": False}
        return tools

    async def call_tool(self, name, arguments, context=None):
        allowed = ALLOWED_ARGUMENTS.get(name)
        if allowed is not None and set(arguments) - allowed:
            raise ToolError("Unknown tool argument")
        return await super().call_tool(name, arguments, context)

T = TypeVar("T", bound=Result)


def _read(operation: Callable[[], dict], result_type: type[T]) -> T:
    try:
        result = result_type.model_validate(operation())
        if len(result.model_dump_json().encode()) > MAX_QUERY_BYTES:
            raise TerraError("LCD result is too large")
        return result
    except TerraError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError("Terra Classic LCD response unavailable or invalid") from exc


def build_server(client: LcdClient | None = None) -> TerraMCPServer:
    lcd = client or LcdClient()
    server = TerraMCPServer(
        "terra-classic-mcp",
        title="Terra Classic MCP",
        description="Read-only public Terra Classic blockchain queries.",
        instructions="Use these tools for live Terra Classic data. Never provide seed phrases or private keys.",
        version=__version__,
    )

    @server.tool(
        title="Terra Classic chain status",
        description="Read the current chain ID, latest block height, time, and block hash.",
        annotations=READ_ONLY,
    )
    def terra_get_chain_status() -> ChainStatus:
        return _read(lcd.chain_status, ChainStatus)

    @server.tool(
        title="Terra Classic validators",
        description="List up to 20 bonded validators, ranked by voting power. Percentages use the full bonded set.",
        annotations=READ_ONLY,
    )
    def terra_get_validators(
        limit: Annotated[int, Field(ge=1, le=20, description="Number of validators to return (1–20)")] = 10,
    ) -> Validators:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ToolError("limit must be an integer between 1 and 20")
        return _read(lambda: lcd.validators(limit), Validators)

    @server.tool(
        title="Terra Classic native balances",
        description="Read native bank balances for a Terra Classic address. has_more signals additional LCD pages.",
        annotations=READ_ONLY,
    )
    def terra_get_balance(
        address: Annotated[str, Field(min_length=44, max_length=64, description="terra1 Bech32 address (44 or 64 characters)")],
    ) -> Balance:
        return _read(lambda: lcd.balance(require_address(address)), Balance)

    @server.tool(
        title="Terra Classic transaction",
        description="Read a transaction summary by its 64-character hexadecimal hash. No transaction is broadcast.",
        annotations=READ_ONLY,
    )
    def terra_get_transaction(
        tx_hash: Annotated[str, Field(pattern=r"^[A-Fa-f0-9]{64}$", description="Transaction hash")],
    ) -> Transaction:
        return _read(lambda: lcd.transaction(require_tx_hash(tx_hash)), Transaction)

    @server.tool(
        title="Terra Classic contract query",
        description="Run a read-only CosmWasm smart query. It cannot execute a contract or sign a transaction.",
        annotations=READ_ONLY,
    )
    def terra_query_contract(
        contract: Annotated[str, Field(min_length=44, max_length=64, description="terra1 contract address (44 or 64 characters)")],
        query_msg: Annotated[dict[str, Any], Field(description="Contract-specific JSON query object")],
    ) -> ContractQuery:
        return _read(lambda: lcd.contract_query(require_address(contract), require_query(query_msg)), ContractQuery)

    return server
