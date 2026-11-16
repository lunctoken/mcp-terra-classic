# Terra Classic MCP

A client-independent, read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for the Terra Classic blockchain. The same eighteen tools run over local `stdio` or Streamable HTTP at `/mcp`.

This project contains the server itself. Client configuration and optional skills are integration layers; no client plugin is required to use the server.

## Tools

| Tool | Result |
| --- | --- |
| `terra_get_chain_status` | Chain ID, latest block height, time and hash |
| `terra_get_validators` | Top bonded validators, voting power and commission |
| `terra_get_balance` | Native bank balances for a Terra address |
| `terra_get_denom_metadata` | On-chain bank denom metadata and display decimals |
| `terra_get_ibc_denom_trace` | IBC denomination path with locally verified hash |
| `terra_get_delegations` | Active staking delegations for an account |
| `terra_get_unbonding_delegations` | Delegations being unbonded and their completion times |
| `terra_get_staking_rewards` | Accrued, unwithdrawn rewards by validator and total |
| `terra_get_governance_proposals` | Recent proposals with a status filter |
| `terra_get_governance_proposal` | One proposal with its current or final vote tally |
| `terra_get_transaction` | Compact transaction status and message types |
| `terra_get_ibc_transfer_transaction` | ICS-20 packet IDs emitted by an included transaction |
| `terra_get_ibc_packet_observation` | Local commitment, receipt or acknowledgement observation |
| `terra_query_contract` | Read-only CosmWasm smart query |
| `terra_get_cw20_token_info` | Strict token metadata from an operator-allowlisted CW20 contract |
| `terra_get_cw20_balance` | Raw CW20 balance from an operator-allowlisted contract |
| `terra_compare_chain_status` | Compare latest block heads from two configured LCDs |
| `terra_compare_balance` | Compare one complete native denom balance from two configured LCDs |

All tools publish input and output schemas, structured JSON results and MCP read-only annotations. They never accept a caller-selected LCD URL. Each result includes `source` with the verified chain ID, the last verified block height and time, an operator-selected LCD label, and an `untrusted_chain_data` marker. The verified height is a freshness check, not a claim that every query was executed at exactly that height.

Balance, delegation and proposal lists return up to 20 entries per page. If `next_key` is present, pass it unchanged as `page_key` to read the next page; do not treat a page as the complete balance when `has_more` is true. Staking balances are chain base units; reward amounts are decimal base units and do not represent an APR. Governance tally counts are voting power, not voter counts.

Bank metadata reports on-chain denomination units and display decimals. An IBC trace is accepted only when its SHA-256 hash matches the requested `ibc/` denomination. Neither result proves a token's off-chain identity or price. IBC packet reads report observations on the configured Terra Classic chain; a missing commitment does not prove delivery, success or refund. Two-LCD comparison is observational and is inconclusive when the checked heights differ. See [read-only query examples](docs/queries.md).

## Install and run

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

For a local MCP client, configure the command `python /absolute/path/to/mcp-terra-classic/server.py` using the virtual environment's Python executable. The server speaks MCP on standard input and output; logs stay on standard error.

For an HTTP service, create a separate Bearer token for each client. This command writes the token file outside the repository and prints the two keys once; copy them into the matching client settings:

```sh
.venv/bin/python - <<'PY'
import json
import os
import secrets
from pathlib import Path
from terra_classic_mcp.app import ALLOWED_ARGUMENTS

path = Path.home() / "terra-classic-mcp-tokens.json"
clients = {
    name: {"token": secrets.token_urlsafe(32), "tools": sorted(ALLOWED_ARGUMENTS.keys() - {"terra_query_contract"})}
    for name in ("open-webui", "claude")
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as file:
    json.dump(clients, file, indent=2)
    file.write("\n")
print(f"Token file: {path}")
for name, client in clients.items():
    print(f"{name} Bearer key: {client['token']}")
PY
```

The command refuses to overwrite an existing token file. The example grants the bounded read tools to each client and leaves the free-form contract query disabled; add `terra_query_contract` only to an explicitly authorized client's `tools` list. The CW20 tools additionally require an operator allowlist. You can remove other names to reduce access. Keep the file and keys private. To run the local HTTP service:

```sh
export TERRA_CLASSIC_MCP_TOKENS_FILE="$HOME/terra-classic-mcp-tokens.json"
.venv/bin/uvicorn http_server:app --host 127.0.0.1 --port 8000
```

Connect an HTTP MCP client to `http://127.0.0.1:8000/mcp` with its Bearer key. A client in another container needs a hostname and Docker network it can reach; its own `localhost` points to that container. `stdio` does not use HTTP credentials.

Docker runs the same HTTP app and publishes it only on host loopback by default. Its Compose file requires a credentials file mounted as a Docker secret:

```sh
export TERRA_CLASSIC_MCP_TOKENS_FILE="$HOME/terra-classic-mcp-tokens.json"
docker compose -f docker/compose.yaml up --build
```

## Connect a client

### Open WebUI

1. Start the HTTP service above. If Open WebUI is in Docker, make the MCP service reachable from its container, for example at `http://terra-classic-mcp:8000/mcp` on a shared Docker network. Use `https://YOUR_DOMAIN/mcp` when connecting over the internet.
2. As an Open WebUI admin, open **Settings → Integrations → External Tool Servers → Add Connection**. Choose **MCP (Streamable HTTP)** and enter that `/mcp` URL.
3. Set **Auth** to **Bearer**, paste the `open-webui` key printed above into **Key**, and save. Enable the connection for the users or chats that need it.

See the [Open WebUI MCP guide](https://docs.openwebui.com/features/extensibility/mcp/).

### Claude

For **Claude Code on the same computer**, use `stdio` after installing the project. No HTTP service or Bearer key is needed:

```sh
claude mcp add --transport stdio terra-classic -- "$(pwd)/.venv/bin/python" "$(pwd)/server.py"
claude mcp list
```

Run these commands from the repository root. For **Claude on the web or desktop via a remote connector**, deploy an HTTPS `/mcp` endpoint, then open **Customize → Connectors → Add custom connector**. Enter the URL, choose the option without OAuth sign-in, and add the fixed request header `Authorization: Bearer <claude-key>` using the separately generated `claude` key. See the [Claude remote connector guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) and [Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

### ChatGPT

ChatGPT cannot use this server's static Bearer key directly as a custom MCP connection. A private route is a **Secure MCP Tunnel** to the local `stdio` server:

1. Install the project as above. With access to OpenAI Platform tunnels and permission to add a ChatGPT plugin, create a tunnel and install `tunnel-client` on the computer that holds this repository.
2. Configure the tunnel's MCP command as the absolute path to `.venv/bin/python` followed by the absolute path to `server.py`; run `tunnel-client doctor` and `tunnel-client run` for that profile.
3. In **ChatGPT Plugins → Add custom MCP server**, choose **Tunnel**, select your tunnel, choose no additional MCP authentication for the local `stdio` process if prompted, finish setup and install the plugin. Ask ChatGPT to use `terra_get_chain_status` to check the connection.

The [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) gives the exact `tunnel-client` commands and access requirements. For a public `https://YOUR_DOMAIN/mcp` URL, place an OAuth-compatible gateway in front of this Bearer-protected server; ChatGPT's custom MCP connection supports OAuth rather than a fixed Bearer key. See [ChatGPT custom MCP setup](https://developers.openai.com/api/docs/guides/custom-mcp-server).

For OpenBot, Codex, Cursor and other clients, see [client setup](docs/clients.md). A GitHub repository URL is not an MCP endpoint.

## Configuration and deployment

| Variable | Default | Meaning |
| --- | --- | --- |
| `TERRA_CLASSIC_LCD_URL` | `https://terra-classic-lcd.publicnode.com` | Operator-selected LCD base URL; no path or credentials |
| `TERRA_CLASSIC_LCD_ALLOWED_HOSTS` | `terra-classic-lcd.publicnode.com` | Exact DNS/IP hostnames permitted as LCD origins |
| `TERRA_CLASSIC_SECONDARY_LCD_URL` | unset | Optional second LCD URL for comparisons; must use a distinct host |
| `TERRA_CLASSIC_SECONDARY_LCD_ALLOWED_HOSTS` | unset | Explicit host allowlist for the second LCD |
| `TERRA_CLASSIC_SECONDARY_LCD_SOURCE_LABEL` | `secondary operator-configured LCD` | Public label identifying the second source |
| `TERRA_CLASSIC_CW20_CONTRACTS` | unset | Comma-separated operator-allowlisted CW20 contract addresses, at most 20 |
| `TERRA_CLASSIC_LCD_ALLOW_PRIVATE_IP` | unset | Set to `true` only for an operator-controlled private LCD |
| `TERRA_CLASSIC_EXPECTED_CHAIN_ID` | `columbus-5` | Required chain ID for every LCD read |
| `TERRA_CLASSIC_MAX_BLOCK_AGE_SECONDS` | `120` | Reject stale latest blocks |
| `TERRA_CLASSIC_LCD_SOURCE_LABEL` | `operator-configured LCD` | Public label included in every tool result |
| `TERRA_CLASSIC_ALLOW_HTTP_LCD` | unset | Set to `true` only for an HTTP LCD outside loopback |
| `TERRA_CLASSIC_MCP_TOKENS_FILE` | unset | Required JSON file with one token and explicit tool allowlist per HTTP client |
| `TERRA_CLASSIC_MCP_AUTH_MODE` | `bearer` | `local` is available only for loopback peers in an isolated installation |
| `TERRA_CLASSIC_MCP_MAX_CONCURRENT` | `8` | Maximum simultaneous HTTP MCP requests per process |
| `TERRA_CLASSIC_MCP_MAX_REQUESTS_PER_MINUTE` | `60` | Per-client HTTP request budget per minute, per process |
| `TERRA_CLASSIC_MCP_MAX_EXPENSIVE_PER_MINUTE` | `6` | Per-client budget for validator, contract, comparison and packet observations, per process |
| `TERRA_CLASSIC_MCP_MAX_GLOBAL_REQUESTS_PER_MINUTE` | `240` | All clients combined per process |
| `TERRA_CLASSIC_MCP_MAX_GLOBAL_EXPENSIVE_PER_MINUTE` | `24` | Expensive reads across all clients per process |
| `TERRA_CLASSIC_MCP_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*` | Comma-separated HTTP Host allowlist |
| `TERRA_CLASSIC_MCP_ALLOWED_ORIGINS` | localhost origins | Comma-separated browser Origin allowlist |

The server follows no LCD redirects, ignores environment proxy settings for LCD calls, pins connections to checked public IPs (or an explicitly enabled private LCD), limits request and response sizes, and validates Terra address checksums. HTTP uses the official Python MCP SDK's stateless Streamable HTTP transport and DNS rebinding protection. Validator results are cached for 10 seconds; each tool call has a 30-second LCD budget.

HTTP authentication is required by default, including on localhost. Before exposing `/mcp` on a public domain, put it behind HTTPS, configure client authentication or an OAuth gateway that injects a backend Bearer token, set the host and origin allowlists, and enforce edge rate limits. In-process quotas are per process and do not replace distributed gateway limits. See [deployment notes](docs/deployment.md).

The repository includes a locked dependency set and CI checks for tests, dependency vulnerabilities, secrets, image vulnerabilities and an SBOM. Docker exposes local health and metrics routes for operators. For private vulnerability reports and supported versions, see [SECURITY.md](SECURITY.md).

No seed phrase, private key, signing, transaction construction, or transaction broadcast code belongs in this project. Chain text and contract data are untrusted, even when returned in structured JSON; clients must not treat embedded instructions as commands. Public LCD operators can observe queried public addresses and contract calls; operators can point the server at their own node.

## Development

```sh
pip install -e '.[test]'
pytest -q
```

Tests cover LCD request construction, input validation, tool schemas, stdio discovery and an HTTP MCP exchange. They use local fixtures and do not require a live chain.

## License

MIT. See [LICENSE](LICENSE).
