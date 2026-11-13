---
name: staking
description: Inspect Terra Classic delegations, unbonding entries and unwithdrawn staking rewards through the Terra Classic MCP server.
---

# Staking position

Use `terra_get_delegations` for active stake, `terra_get_unbonding_delegations` for stake being released, and `terra_get_staking_rewards` for currently accrued rewards. All three tools accept a public 44-character `terra1` account address.

The two delegation lists return at most 20 validators per page. If `next_key` is present, pass it unchanged as `page_key` to continue. State when a result is only one page.

Delegation and unbonding balances are base units. Reward `amount` strings are decimal base units; `total` is the aggregate across validators. Do not present these amounts as a percentage yield or assume they have already been withdrawn. These tools never delegate, undelegate or withdraw funds.
