# HTTP deployment

`http_server:app` exposes only Streamable HTTP at `/mcp`. The default allowlists accept local hosts and origins. Run `uvicorn http_server:app --host 127.0.0.1 --port 8000` for a single-machine installation.

The sample Docker Compose publishes port 8000 on host loopback only. Containers on its network can reach `http://terra-classic-mcp:8000/mcp`.

To serve a public HTTPS hostname, place a reverse proxy or gateway in front of the app. Configure:

1. TLS with a valid certificate and a stable `/mcp` URL.
2. Authentication appropriate for your clients, or an explicit anonymous access policy for a public read-only service.
3. Per-client rate limits, request size limits and a concurrency cap. Contract queries and validator pagination can use substantial LCD capacity.
4. `TERRA_CLASSIC_MCP_ALLOWED_HOSTS` with the exact external host, for example `mcp.example.org:*`, plus any internal host used by the proxy.
5. `TERRA_CLASSIC_MCP_ALLOWED_ORIGINS` with trusted browser origins if browsers connect directly. Keep the list narrow.
6. A reliable LCD via `TERRA_CLASSIC_LCD_URL`; prefer an operator-controlled node for dependable public service.

Do not put an upstream URL, key, seed phrase or wallet credential in client-facing tool arguments. A tunnel can make a private local server reachable to a supported remote client without publishing an open port; the tunnel is a deployment option, not a server dependency.

The server's results are compact summaries. Native balance results expose `has_more` when the LCD has another page. Transaction results include status, gas and message types rather than the entire transaction payload. Validator results fail if the bonded set cannot be fully fetched, avoiding a misleading voting-power percentage.
