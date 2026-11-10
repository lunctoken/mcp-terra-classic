---
name: chain-status
description: Read live Terra Classic chain identity and latest block status through the Terra Classic MCP server.
---

# Chain status

Use `terra_get_chain_status` when the user asks for Terra Classic's chain ID, latest height, block time or block hash. These values change; call the tool rather than recalling them. Report the returned height and time together so the user can judge freshness.
