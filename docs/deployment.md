# HTTP deployment

`http_server:app` exposes Streamable HTTP at `/mcp`. HTTP requires Bearer authentication by default. `stdio` remains available without an HTTP token and uses the operating-system permissions of the client process.

## Create client credentials

Generate a different random token for each client. Run `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` for each token, then create a JSON file outside the repository:

```json
{
  "open-webui": {
    "token": "GENERATE_TOKEN_1",
    "tools": ["terra_get_chain_status", "terra_get_validators", "terra_get_balance", "terra_get_delegations", "terra_get_unbonding_delegations", "terra_get_staking_rewards", "terra_get_governance_proposals", "terra_get_governance_proposal", "terra_get_transaction"]
  },
  "contract-research": {
    "token": "GENERATE_TOKEN_2",
    "tools": ["terra_query_contract"]
  }
}
```

Give the file restrictive permissions on the host (`chmod 600 /absolute/path/to/mcp-tokens.json`) and keep it out of Git, backups shared with clients, and application logs. Set `TERRA_CLASSIC_MCP_TOKENS_FILE` to its absolute path. The Docker Compose file mounts this file as a read-only secret. Rotate a token by replacing its value and restarting the server. A client can call only the tools in its explicit allowlist; it may still see the server's full tool catalog during discovery.

The example above grants an older, narrow tool subset. To permit the new read-only tools, add the exact names needed: `terra_get_tax_parameters`, `terra_get_burn_snapshot`, `terra_get_authz_grants_received`, `terra_explain_transaction`, and `terra_get_validator_page`. Existing Bearer token files do not gain these permissions automatically. These tools still cannot sign or broadcast transactions.

Version 0.5.0 also adds `terra_get_account_overview` and `terra_get_governance_parameters`. Add either name explicitly to each existing client allowlist that should use it. The account overview makes five LCD reads and counts against the expensive-tool quota.

The legacy unauthenticated HTTP behavior is available only when `TERRA_CLASSIC_MCP_AUTH_MODE=local` and the connecting peer IP is loopback. It is intended for an isolated single-machine setup and does not work for a different Docker container. A reverse proxy on the same host can appear as a loopback peer, so do not use this mode behind a public proxy.

## Publish an HTTPS endpoint

The sample Docker Compose publishes port 8000 on host loopback only. Containers on its network can reach `http://terra-classic-mcp:8000/mcp` if they send a configured Bearer token. [Caddyfile.example](../docker/Caddyfile.example) is a minimal HTTPS reverse-proxy template for Bearer-capable clients; replace its hostname and add that hostname to `TERRA_CLASSIC_MCP_ALLOWED_HOSTS`. For public access, place a reverse proxy or gateway in front of the app and configure:

1. TLS with a valid certificate and a stable `/mcp` URL. Keep the backend port private.
2. Authentication for every external client. If a client requires OAuth rather than a static Bearer token, terminate OAuth at the gateway, validate token audience and scopes there, strip any incoming backend `Authorization` header, and inject that client's backend Bearer token. Never forward an unvalidated client-supplied backend token.
3. Rate limits and concurrency limits at the gateway across all workers or replicas. The server also enforces per-client limits within one process: 60 requests/minute, six expensive reads/minute (validators, contracts, LCD comparisons and IBC packet observations), eight concurrent requests, plus global limits of 240 requests/minute and 24 expensive queries/minute. The request body is limited to 1 MiB and LCD work to 30 seconds per tool call by default. Validator queries fetch at most five pages of 100 and fail if the bonded set is incomplete.
4. `TERRA_CLASSIC_MCP_ALLOWED_HOSTS` with the exact external host and the proxy's internal host; `TERRA_CLASSIC_MCP_ALLOWED_ORIGINS` with only trusted browser origins. Host and Origin checks are not authentication.
5. `TERRA_CLASSIC_LCD_URL` and `TERRA_CLASSIC_LCD_ALLOWED_HOSTS` set by the operator. The server resolves each outbound LCD connection and connects to the checked IP with the original hostname used for TLS. Private IPs require explicit permission; link-local, multicast and unspecified addresses remain rejected, including the usual link-local cloud metadata endpoint. Add an infrastructure egress firewall that permits only the configured LCD service for another independent barrier.
6. `TERRA_CLASSIC_EXPECTED_CHAIN_ID` (default `columbus-5`) and a suitable `TERRA_CLASSIC_MAX_BLOCK_AGE_SECONDS` (default 120). Every read first verifies a recent latest block on the expected chain. If your node is intentionally behind, fix or replace it instead of increasing the age without a reason.

The verified height in each response describes the latest-block check; the subsequent LCD result may be from a nearby height. For critical decisions, recheck the chain state and use an independent node. The `source.lcd_id` label identifies the configured source without exposing an internal LCD URL.

## P2 read-only features

To compare two LCDs, set `TERRA_CLASSIC_SECONDARY_LCD_URL`, `TERRA_CLASSIC_SECONDARY_LCD_ALLOWED_HOSTS`, and a meaningful `TERRA_CLASSIC_SECONDARY_LCD_SOURCE_LABEL`. The secondary host must differ from the primary host. Select independently operated nodes where possible; distinct hostnames alone do not prove operator independence. Both LCDs must answer for the configured chain ID and have fresh latest blocks. Comparison tools report separate source labels and an inconclusive verdict when heights differ. Each native balance comparison reads at most five pages per node and fails if the result is incomplete. Give these more costly tools to only the HTTP clients that need them.

Set `TERRA_CLASSIC_CW20_CONTRACTS` to at most 20 reviewed, comma-separated contract addresses to enable the fixed CW20 reads. Without an allowlist, those tools reject calls. This list is server configuration, not a tool argument. Continue to grant `terra_query_contract` only to credentials meant for free-form read-only contract queries. The sample README token generator excludes it by default. The [query examples](queries.md) show the workflows and limits of IBC observations and metadata.

## Untrusted chain content

Validator monikers and websites, governance titles and summaries, and contract query results can be written by third parties. The server bounds result sizes and marks each result `source.untrusted_chain_data=true`. Clients must display these fields as data and must not obey instructions found inside them, browse returned URLs automatically, or pass them to a signing or transaction tool. The MCP `readOnlyHint` annotation describes this server's tools; it does not enforce a client policy.

The server has no wallet, seed phrase, key import, signing, transaction construction, vote submission or broadcast function. Keep any future action service separate from this process and its HTTP credentials.

## Health, metrics, and alerts

The HTTP process serves `GET /healthz` for liveness, `GET /readyz` for LCD chain/freshness readiness, and `GET /metrics` with Prometheus counters for HTTP statuses, total request time, and tool errors. These routes require both a loopback peer IP and a loopback `Host` header. The sample Caddy configuration forwards only `/mcp`; keep all three operational paths off the public proxy. For container monitoring, collect them through a local sidecar or host-local probe. Do not publish the metrics port.

The Compose health check calls `/readyz` every 30 seconds and marks the container unhealthy after three failures. Alert on persistent `readyz` failures, a sustained rise in HTTP 401/403/429/5xx responses or `terra_mcp_tool_errors_total`, and an abnormal rise in `terra_mcp_http_request_duration_seconds_sum` relative to request count. Counters reset at process restart and are per process. Denied HTTP requests and unexpected tool failures are logged to stderr with status/category only; the server does not log query bodies, addresses, tokens or LCD URLs. Configure log retention and access at the deployment layer.

## Network egress and resource isolation

Compose limits the process to one CPU, 256 MiB of memory, 100 processes, a read-only filesystem, no Linux capabilities and no privilege escalation. Its port binding remains on host loopback. The server validates the configured LCD origin and checked destination IPs, but Docker Compose alone cannot express a portable DNS-aware egress allowlist. Enforce an outbound firewall or egress gateway permitting only the operator's LCD endpoint (and required DNS resolution) from the MCP container; deny cloud metadata, private services and general internet destinations. If the LCD IP changes, update the firewall policy before changing the configured host. For Kubernetes, apply a namespace NetworkPolicy or CNI policy with the same LCD and DNS allowlist. Test from inside the deployed container that permitted LCD requests work and blocked destinations fail.

Keep the HTTPS proxy's Caddy route restricted to `/mcp` as in the template. If adding another proxy, preserve the client `Host` and `Origin` checks and never route `/healthz`, `/readyz` or `/metrics` publicly. The backend Bearer credential file stays a private Docker secret and must never be mounted into a client container.

## Release and repository controls

The committed `uv.lock` fixes Python dependencies. CI checks the lock, audits production dependencies, scans Git history for secrets, builds and scans the Docker image, and uploads an SPDX SBOM. Tagged builds produce wheel/sdist provenance attestations. GitHub Actions are pinned to commit SHAs; the Docker Python base is pinned to a manifest digest. Dependabot proposes dependency, action and base-image updates, which must be reviewed before merging. The [security policy](../SECURITY.md) describes private vulnerability reporting.

Repository administrators must enable GitHub private vulnerability reporting and native secret scanning in **Settings → Advanced Security**, and protect `main` with required CI checks and review for workflow changes. Those repository settings cannot be enforced by files in this checkout. A security report should be handled in a private advisory until a tested fix is available.
