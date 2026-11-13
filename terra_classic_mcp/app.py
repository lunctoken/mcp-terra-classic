"""One tool registry shared by the stdio and HTTP transports."""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .core import (
    MAX_QUERY_BYTES,
    LcdClient,
    TerraError,
    require_account_address,
    require_address,
    require_page_key,
    require_page_limit,
    require_query,
    require_tx_hash,
)


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


class Delegation(Result):
    validator_address: str
    balance: Coin


class Delegations(Result):
    address: str
    delegations: list[Delegation]
    next_key: str | None


class UnbondingEntry(Result):
    balance: str
    completion_time: str


class UnbondingDelegation(Result):
    validator_address: str
    entries: list[UnbondingEntry]


class UnbondingDelegations(Result):
    address: str
    unbonding: list[UnbondingDelegation]
    next_key: str | None


class ValidatorReward(Result):
    validator_address: str
    reward: list[Coin]


class StakingRewards(Result):
    address: str
    rewards: list[ValidatorReward]
    total: list[Coin]


class GovernanceProposalSummary(Result):
    id: str
    title: str
    status: str
    voting_end_time: str


class GovernanceProposals(Result):
    status_filter: str
    proposals: list[GovernanceProposalSummary]
    next_key: str | None


class GovernanceTally(Result):
    yes_count: str
    abstain_count: str
    no_count: str
    no_with_veto_count: str


class GovernanceProposal(Result):
    proposal_id: str
    title: str
    summary: str
    summary_truncated: bool
    status: str
    submit_time: str
    voting_start_time: str
    voting_end_time: str
    tally_kind: Literal["none", "current", "final"]
    tally: GovernanceTally | None


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
    "terra_get_delegations": frozenset({"address", "limit", "page_key"}),
    "terra_get_unbonding_delegations": frozenset({"address", "limit", "page_key"}),
    "terra_get_staking_rewards": frozenset({"address"}),
    "terra_get_governance_proposals": frozenset({"status", "limit", "page_key"}),
    "terra_get_governance_proposal": frozenset({"proposal_id"}),
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
        title="Terra Classic delegations",
        description="Read active staking delegations for a terra1 account. Amounts are base units; pass next_key as page_key for more.",
        annotations=READ_ONLY,
    )
    def terra_get_delegations(
        address: Annotated[str, Field(min_length=44, max_length=44, description="44-character terra1 account address")],
        limit: Annotated[int, Field(strict=True, ge=1, le=20, description="Delegations per page (1–20)")] = 20,
        page_key: Annotated[str, Field(max_length=512, description="Opaque next_key from a previous response; empty for first page")] = "",
    ) -> Delegations:
        return _read(lambda: lcd.delegations(require_account_address(address), require_page_limit(limit), require_page_key(page_key)), Delegations)

    @server.tool(
        title="Terra Classic unbonding delegations",
        description="Read staking delegations being unbonded, with remaining base-unit amounts and completion times. Pass next_key as page_key for more.",
        annotations=READ_ONLY,
    )
    def terra_get_unbonding_delegations(
        address: Annotated[str, Field(min_length=44, max_length=44, description="44-character terra1 account address")],
        limit: Annotated[int, Field(strict=True, ge=1, le=20, description="Validators per page (1–20)")] = 20,
        page_key: Annotated[str, Field(max_length=512, description="Opaque next_key from a previous response; empty for first page")] = "",
    ) -> UnbondingDelegations:
        return _read(lambda: lcd.unbonding_delegations(require_account_address(address), require_page_limit(limit), require_page_key(page_key)), UnbondingDelegations)

    @server.tool(
        title="Terra Classic staking rewards",
        description="Read currently accrued, unwithdrawn delegator rewards by validator and total. Amounts are decimal base units, not APR.",
        annotations=READ_ONLY,
    )
    def terra_get_staking_rewards(
        address: Annotated[str, Field(min_length=44, max_length=44, description="44-character terra1 account address")],
    ) -> StakingRewards:
        return _read(lambda: lcd.staking_rewards(require_account_address(address)), StakingRewards)

    @server.tool(
        title="Terra Classic governance proposals",
        description="List recent governance proposals and their statuses. Pass next_key as page_key for more; use the detail tool for tally.",
        annotations=READ_ONLY,
    )
    def terra_get_governance_proposals(
        status: Annotated[Literal["all", "voting", "deposit", "passed", "rejected", "failed"], Field(description="Proposal status filter")] = "all",
        limit: Annotated[int, Field(strict=True, ge=1, le=20, description="Proposals per page (1–20)")] = 10,
        page_key: Annotated[str, Field(max_length=512, description="Opaque next_key from a previous response; empty for first page")] = "",
    ) -> GovernanceProposals:
        return _read(lambda: lcd.governance_proposals(status, require_page_limit(limit), require_page_key(page_key)), GovernanceProposals)

    @server.tool(
        title="Terra Classic governance proposal",
        description="Read one proposal, a bounded summary and its current or final vote tally. Tally counts are voting power in base units.",
        annotations=READ_ONLY,
    )
    def terra_get_governance_proposal(
        proposal_id: Annotated[int, Field(strict=True, ge=1, le=9223372036854775807, description="Positive on-chain proposal ID")],
    ) -> GovernanceProposal:
        return _read(lambda: lcd.governance_proposal(proposal_id), GovernanceProposal)

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
