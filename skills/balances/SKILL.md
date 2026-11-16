---
name: balances
description: Read native Terra Classic bank balances for a public address through the Terra Classic MCP server.
---

# Native balances

Use `terra_get_balance` with a public `terra1` address. The response contains base-unit amounts keyed by denom; do not silently convert them to display units without on-chain denomination metadata. Results contain at most 20 denoms per page. If `has_more` is true, pass `next_key` unchanged as `page_key` to read the next page, or state that the result is partial. Use `terra_get_denom_metadata` for display decimals and `terra_get_ibc_denom_trace` for an IBC hash. CW20 balances come from `terra_get_cw20_balance` for contracts allowlisted by the server operator.
