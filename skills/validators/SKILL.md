---
name: validators
description: Inspect current bonded Terra Classic validators, voting power and commission through the Terra Classic MCP server.
---

# Validators

Use `terra_get_validators` for current bonded validator rankings. `limit` is between 1 and 20; default is 10. `validator_count` is the total bonded set fetched, while `validators` contains only the requested top entries. Voting power percentages use the complete bonded set. Do not present the top entries as the entire validator population.
