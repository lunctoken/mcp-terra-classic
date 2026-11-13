# Connect a client

The server exposes the same tools to every MCP client. Install the Python project first as described in the [README](../README.md). For remote clients, replace `https://YOUR_DOMAIN/mcp` with your deployed HTTPS endpoint. A GitHub repository URL is not an MCP endpoint.

## Open WebUI

Start the HTTP server, then in Open WebUI open **Settings → Admin → Integrations → External Tool Servers → Add Connection**. Choose **MCP (Streamable HTTP)**, enter the `/mcp` URL and select the authentication mode used by your deployment. A Dockerized Open WebUI must use an address reachable from its container, such as `http://terra-classic-mcp:8000/mcp` on the same Docker network. Open WebUI supports Streamable HTTP for native MCP connections. [Open WebUI MCP guide](https://docs.openwebui.com/features/extensibility/mcp/).

## OpenBot

For the `local-ia` OpenBot integration, keep its ACP `mcpServers` entry in `local-ia` and point it to this repository's installed `server.py` via a Python executable. The server entry is equivalent to:

```json
{"name":"terra_classic","command":"/absolute/path/to/.venv/bin/python","args":["/absolute/path/to/mcp-terra-classic/server.py"],"env":[]}
```

The `TERRA_CLASSIC_LCD_URL` environment variable belongs to the OpenBot process or server deployment, not to a user prompt. Other OpenBot installations can use their own MCP configuration mechanism with the same stdio command.

## Codex

For an HTTPS deployment:

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

ChatGPT needs a reachable HTTPS `/mcp` endpoint or a supported Secure MCP Tunnel for a local server. In **ChatGPT Plugins**, choose **Add custom MCP server**, enter a name and the endpoint or tunnel, configure authentication, then create and install the plugin. The tools come from this server; the ChatGPT plugin is only a connection layer. [ChatGPT custom MCP guide](https://developers.openai.com/api/docs/guides/custom-mcp-server).

## Claude Code

For an HTTPS deployment:

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

After connection, list tools and check that all ten `terra_` names appear before calling one. The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) describes both transports.
