---
name: lcd-comparison
description: Compare read-only Terra Classic chain heads and native balances from two operator-configured LCDs.
---

# LCD comparison

Use `terra_compare_chain_status` or `terra_compare_balance` only when the server operator configured a second LCD. Report both source labels, checked heights and the exact verdict. Treat `inconclusive_height_mismatch` as inconclusive. Even `agree_observed` is not a cryptographic proof of a historical state or a guarantee about the next block. The LCD URLs are operator configuration and must never be requested from the user as tool arguments.
