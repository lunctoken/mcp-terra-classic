# Connect a client

The server offers the same 25 tools to every MCP client, but each HTTP client's Bearer token permits only its configured subset. Install the Python project first as described in the [README](../README.md), and create client credentials as described in [HTTP deployment](deployment.md). Configure the client to send `Authorization: Bearer <its-token>` when it supports static Bearer authentication. If it requires OAuth, use an OAuth gateway that validates the client and injects a separate backend token. Keep tokens out of chat prompts and MCP tool arguments. Local `stdio` needs no HTTP token. For remote clients, replace `https://YOUR_DOMAIN/mcp` with your deployed HTTPS endpoint. A GitHub repository URL is not an MCP endpoint.

## Open WebUI

Start the HTTP server, then as an Open WebUI admin open **Settings → Integrations → External Tool Servers → Add Connection**. Choose **MCP (Streamable HTTP)**, enter the `/mcp` URL, set **Auth** to **Bearer** and put Open WebUI's own token in **Key**. A Dockerized Open WebUI must use an address reachable from its container, such as `http://terra-classic-mcp:8000/mcp` on the same Docker network. Open WebUI supports Streamable HTTP for native MCP connections. [Open WebUI MCP guide](https://docs.openwebui.com/features/extensibility/mcp/).

## OpenBot

For the `local-ia` OpenBot integration, keep its ACP `mcpServers` entry in `local-ia` and point it to this repository's installed `server.py` via a Python executable. The server entry is equivalent to:

```json
{"name":"terra_classic","command":"/absolute/path/to/.venv/bin/python","args":["/absolute/path/to/mcp-terra-classic/server.py"],"env":[]}
```

The `TERRA_CLASSIC_LCD_URL` environment variable belongs to the OpenBot process or server deployment, not to a user prompt. Other OpenBot installations can use their own MCP configuration mechanism with the same stdio command.

## Codex

For an HTTPS deployment, configure authentication in the Codex MCP connection or use the OAuth gateway described above:

```sh
codex mcp add terra-classic --url https://YOUR_DOMAIN/mcp
codex mcp list
```

For a local process, add this to `~/.codex/config.toml`, replacing both paths with absolute paths:

```toml
[mcp_servers.terra_classic]
command = "/absolute/path/to/mcp-terra-classic/.venv/bin/python"
args = ["/absolute/path/to/mcp-terra-classic/server.py"]
```

[Codex MCP setup](https://developers.openai.com/resources/docs-mcp).

## ChatGPT

ChatGPT's custom MCP connection supports OAuth or no authentication, not a fixed Bearer key. Use a [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) to this project's local `stdio` entry point, or put an OAuth-compatible gateway in front of an HTTPS `/mcp` endpoint. In **ChatGPT Plugins**, choose **Add custom MCP server**, enter a name, select the tunnel or enter the gateway URL, configure authentication, then create and install the plugin. The tools come from this server; the ChatGPT plugin is only a connection layer. [ChatGPT custom MCP guide](https://developers.openai.com/api/docs/guides/custom-mcp-server).

## Claude on the web or desktop

Deploy an HTTPS `/mcp` URL. In **Customize → Connectors → Add custom connector**, enter that URL. For this project's Bearer mode, choose **No sign in** under Authentication and add `Authorization: Bearer <claude-key>` under **Request headers**. Give Claude its own token from the credentials file. For a local installation, Claude Code's `stdio` route below is simpler. [Claude remote connector guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

## Claude Code

For an HTTPS deployment behind an OAuth-compatible gateway:

```sh
claude mcp add --transport http terra-classic https://YOUR_DOMAIN/mcp
claude mcp get terra-classic
```

For local stdio, replace the paths with absolute paths:

```sh
claude mcp add --transport stdio terra-classic -- /absolute/path/to/mcp-terra-classic/.venv/bin/python /absolute/path/to/mcp-terra-classic/server.py
```

The root [`.mcp.json`](../.mcp.json) is a project-scoped stdio example for Claude Code when its `python3` has this package installed. It uses Claude's project-directory variable to find the script. [Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## Cursor

Create `.cursor/mcp.json` in the project using Cursor, or `~/.cursor/mcp.json` for a global connection. For local stdio:

```json
{
  "mcpServers": {
    "terra-classic": {
      "command": "/absolute/path/to/mcp-terra-classic/.venv/bin/python",
      "args": ["/absolute/path/to/mcp-terra-classic/server.py"]
    }
  }
}
```

Cursor also supports Streamable HTTP; enter the deployed `/mcp` URL in its MCP settings. [Cursor MCP guide](https://docs.cursor.com/context/model-context-protocol).

## Any other MCP client

Use one of these connection forms:

- `stdio`: executable `/absolute/path/to/.venv/bin/python`, argument `/absolute/path/to/mcp-terra-classic/server.py`.
- Streamable HTTP: `https://YOUR_DOMAIN/mcp`, or `http://127.0.0.1:8000/mcp` from the same machine.

After connection, list tools and check that all 25 `terra_` names appear before calling one. Tool discovery shows the full catalog, while each HTTP Bearer credential permits only its configured subset. The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) describes both transports.

## Questions to try in ChatGPT, Open WebUI or Claude

- “Use `terra_get_chain_status` to give me the current Terra Classic chain ID and latest verified block height.”
- “Read all pages of the native balance of this public `terra1...` address. Get on-chain display decimals for `uluna`; leave unknown denoms in raw units.”
- “Use `terra_get_account_overview` for this public account. Identify every section with another page before summarizing it.”
- “Read `terra_get_governance_parameters` and give the current quorum, approval threshold and veto threshold as on-chain fractions.”
- “Explain this included transaction by hash. If it contains `MsgMultiSend`, report the returned recipients and say whether any were truncated.”
- “For this IBC transaction hash, show the local transaction result and any `send_packet` channel and sequence. Describe only what the local packet query observes.”
- “Compare the latest block heads from the two configured LCDs. If the heights differ, say the comparison is inconclusive.”
- “For this operator-allowlisted CW20 contract, show its contract-reported decimals and the raw balance of this public address.”

See [read-only query examples](queries.md) for the exact tool sequence and limits of each result. Never put a Bearer token, private key or seed phrase into a prompt.
