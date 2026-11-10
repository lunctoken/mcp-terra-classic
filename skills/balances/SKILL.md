---
name: balances
description: Read native Terra Classic bank balances for a public address through the Terra Classic MCP server.
---

# Native balances

Use `terra_get_balance` with a public `terra1` address. The response contains base-unit amounts keyed by denom; do not silently convert them to display units without an authoritative denomination rule. If `has_more` is true, state that the result is partial. This tool does not query CW20 contract balances; those require a contract-specific `terra_query_contract` call.
