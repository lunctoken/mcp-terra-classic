# Terra Classic MCP contributor guide

- Keep blockchain queries in `terra_classic_mcp/core.py` and MCP tools in `terra_classic_mcp/app.py`. `server.py` and `http_server.py` are transport entry points for the same tools.
- Keep all exposed tools read-only. Never add seed phrase, private key, signing, transaction construction or broadcast behavior to this server.
- LCD origin comes from operator configuration, never from tool arguments. Validate inputs before LCD requests and return bounded, structured results.
- Keep client-specific instructions in `docs/`, `.mcp.json` and `skills/`; the server must work without them.
- Run `python -m pytest -q` before changing a tool contract. Update schemas and tests with result-shape changes.
