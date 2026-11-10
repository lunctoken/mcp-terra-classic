# Terra Classic MCP

A client-independent, read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for the Terra Classic blockchain. The same five tools run over local `stdio` or Streamable HTTP at `/mcp`.

This project contains the server itself. Client configuration and optional skills are integration layers; no client plugin is required to use the server. The layout is inspired by [Aave Skills](https://github.com/aave/skills), whose client integrations point to an independent MCP endpoint.

## Tools

| Tool | Result |
| --- | --- |
| `terra_get_chain_status` | Chain ID, latest block height, time and hash |
| `terra_get_validators` | Top bonded validators, voting power and commission |
| `terra_get_balance` | Native bank balances for a Terra address |
| `terra_get_transaction` | Compact transaction status and message types |
| `terra_query_contract` | Read-only CosmWasm smart query |

All tools publish input and output schemas, structured JSON results and MCP read-only annotations. They never accept a caller-selected LCD URL.

## Install and run

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

For a local MCP client, configure the command `python /absolute/path/to/mcp-terra-classic/server.py` using the virtual environment's Python executable. The server speaks MCP on standard input and output; logs stay on standard error.

For a local HTTP service:

```sh
.venv/bin/uvicorn http_server:app --host 127.0.0.1 --port 8000
```

Connect an HTTP MCP client to `http://127.0.0.1:8000/mcp`. See [client setup](docs/clients.md) for Open WebUI, OpenBot, Codex, ChatGPT, Claude Code, Cursor and other clients.

Docker runs the same HTTP app and publishes it only on host loopback by default:

```sh
docker compose -f docker/compose.yaml up --build
```

## Configuration and deployment

| Variable | Default | Meaning |
| --- | --- | --- |
| `TERRA_CLASSIC_LCD_URL` | `https://terra-classic-lcd.publicnode.com` | Operator-selected LCD base URL; no path or credentials |
| `TERRA_CLASSIC_ALLOW_HTTP_LCD` | unset | Set to `true` only for an HTTP LCD outside loopback |
| `TERRA_CLASSIC_MCP_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*` | Comma-separated HTTP Host allowlist |
| `TERRA_CLASSIC_MCP_ALLOWED_ORIGINS` | localhost origins | Comma-separated browser Origin allowlist |

The server follows no LCD redirects, ignores environment proxy settings for LCD calls, limits request and response sizes, and validates Terra address checksums. HTTP uses the official Python MCP SDK's stateless Streamable HTTP transport and DNS rebinding protection.

The HTTP service has no built-in user authentication. Keep the default loopback binding for local use. Before exposing `/mcp` on a public domain, put it behind HTTPS, authentication and rate limiting, and set the host and origin allowlists for that domain. See [deployment notes](docs/deployment.md).

No seed phrase, private key, signing, transaction construction, or transaction broadcast code belongs in this project. Public LCD operators can observe queried public addresses and contract calls; operators can point the server at their own node.

## Development

```sh
pip install -e '.[test]'
pytest -q
```

Tests cover LCD request construction, input validation, tool schemas, stdio discovery and an HTTP MCP exchange. They use local fixtures and do not require a live chain.

## License

MIT. See [LICENSE](LICENSE).
