---
name: ibc
description: Inspect Terra Classic IBC send transactions and local packet observations without claiming cross-chain finality.
---

# IBC observations

Use `terra_get_transaction` to read the local transaction result. Use `terra_get_ibc_transfer_transaction` to extract send_packet channel IDs and sequences. Use `terra_get_ibc_packet_observation` with the channel ID on Terra Classic and the correct local role. A commitment, receipt or acknowledgement is only an observation from the configured LCD. A missing record never proves delivery, success or refund. The tool always returns `transfer_outcome_confirmed=false`; explain this limit to the user.
