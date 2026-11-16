# Read-only query examples

These tools are available over both `stdio` and Streamable HTTP. HTTP callers also need a Bearer credential whose `tools` list permits the requested name. All returned chain and contract text is untrusted data.

## Interpret a bank balance

1. Ask for `terra_get_balance` with a public `terra1...` address. Continue with each `next_key` as `page_key` until `has_more` is false.
2. For a reported denom, call `terra_get_denom_metadata` with that exact denom. Use `display_decimals` and the returned `denom_units` to interpret the integer amount. If metadata is unavailable, report the raw denom and amount without guessing a symbol or decimal scale.
3. If the denom begins with `ibc/`, call `terra_get_ibc_denom_trace`. The server checks the trace hash locally. A trace identifies port/channel hops and a base denom; it does not by itself establish the remote chain's identity, token issuer, or price. Terra Classic's v4 LCD uses `/ibc/apps/transfer/v1/denoms/{hash}` for this lookup ([chain update](https://github.com/classic-terra/documents/blob/main/chain-updates/v4_0_0.md)).

Example prompt: “Use `terra_get_balance` for this public Terra Classic address, read every page, and show raw amounts. For `uluna`, get the on-chain display decimals; do not guess metadata for any denom that has none.”

## Inspect an IBC transfer

1. Call `terra_get_transaction` for the local transaction hash to see whether the transaction itself succeeded (`code = 0`).
2. Call `terra_get_ibc_transfer_transaction` for the same hash to extract any ICS-20 `send_packet` sequence and local source/destination channel IDs from its events.
3. When Terra Classic is the source chain, call `terra_get_ibc_packet_observation` with `local_role="source"`, the `source_channel`, and the sequence. A present commitment is an observation of an outstanding packet on this chain. A missing commitment is **not** proof of success or refund.
4. When Terra Classic is the destination chain, use `local_role="destination"` and its local channel ID. The tool can observe a receipt and acknowledgement on this chain. It does not decode the acknowledgement outcome, verify a counterparty proof, or establish final settlement on another chain.

The packet tool never submits a transaction. It marks `transfer_outcome_confirmed=false` even when a local acknowledgement is observed. A 404 from the LCD is reported as `not_found_or_unsupported`, because a missing record and an unsupported endpoint cannot safely be distinguished from that response alone. For a final transfer conclusion, inspect both chains and the relevant acknowledgement or timeout transaction using independently trusted infrastructure. The [IBC packet protocol](https://github.com/cosmos/ibc/blob/main/spec/core/ics-004-channel-and-packet-semantics/README.md) explains the distinction between commitments, receipts and acknowledgements.

Example prompt: “Read this Terra Classic IBC send transaction by hash. Give me the packet channel and sequence, then describe only what the Terra Classic LCD currently observes; do not claim delivery from a missing commitment.”

## Query an identified CW20 contract

The operator first sets `TERRA_CLASSIC_CW20_CONTRACTS` to a comma-separated list of reviewed contract addresses. `terra_get_cw20_token_info` reads only `token_info`; `terra_get_cw20_balance` reads only `balance` for a public account. Both results have strict shapes, and amounts stay in integer contract base units. The contract supplies its own name, symbol and decimals; clients must treat those fields as claims by that contract.

`terra_query_contract` remains available for other read-only smart queries, but an HTTP credential should include that name only for clients specifically authorized to submit free-form queries. The operator's CW20 allowlist is independent of the HTTP client allowlist.

Example prompt: “For this operator-allowlisted CW20 contract, read `token_info` and the balance of this public address. Show the raw balance and the contract-reported decimals. Do not execute the contract.”

## Compare two LCDs

Set `TERRA_CLASSIC_SECONDARY_LCD_URL` and `TERRA_CLASSIC_SECONDARY_LCD_ALLOWED_HOSTS` on the server. Choose a different host and preferably a different operator, plus a meaningful `TERRA_CLASSIC_SECONDARY_LCD_SOURCE_LABEL`. The URL is never a tool argument.

`terra_compare_chain_status` compares the latest verified heights and block hashes. `terra_compare_balance` reads all pages of one native denom balance from both LCDs, up to five pages per node. If the checked heights differ, the verdict is `inconclusive_height_mismatch`; if the heights match but values differ, it is `divergent_observation`. Agreement is an observation from two LCDs, **not** a cryptographic proof that their queried state came from exactly that height.

Example prompt: “Compare the current block heads from both configured Terra Classic LCDs. If their heights differ, report the result as inconclusive. Then compare this public address's `uluna` balance and give both raw amounts and source labels.”
