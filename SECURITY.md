# Security policy

## Supported versions

Security fixes are developed against the current `main` branch and released in the newest tagged version. Earlier releases are not maintained after a security update. Operators should update promptly and rotate HTTP credentials when a report involves credential exposure.

## Report a vulnerability privately

Use the repository's [private vulnerability report](https://github.com/lunctoken/mcp-terra-classic/security/advisories/new). Do not open a public issue or include a working exploit, token, address query, or infrastructure detail in a public discussion. If GitHub does not show **Report a vulnerability**, the maintainer must enable **Private vulnerability reporting** under **Settings → Advanced Security** before this channel is usable.

Include the affected version or commit, impact, a minimal reproduction with synthetic data, and any deployment conditions. Do not send seed phrases or private keys. The maintainers will acknowledge the report, reproduce it privately, prepare and test a fix, release an update, and publish an advisory when users can safely upgrade. The reporter and maintainers should coordinate disclosure timing through the advisory.

## Scope

The server is read-only. Reports about authentication bypass, LCD origin bypass or SSRF, unbounded resource use, corrupted tool results, prompt injection that crosses the data boundary, and dependency or release compromise are in scope. An upstream LCD supplying false chain data is an operational trust risk; include evidence that this server failed its documented validation if reporting it as a server vulnerability.
