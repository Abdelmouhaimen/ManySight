# Cloudflare deployment

This package runs the FastAPI dashboard/API and Streamable HTTP MCP server in one
Cloudflare Container. A private Durable Object holds compressed SQLite checkpoints so
container sleep or replacement does not erase platform data.

## Endpoint configuration

Edit `../../config/endpoints.json` for checked-in local/cloud profiles. Deployment
variables in `wrangler.jsonc` override the selected profile. Every agent-facing skill,
`/agent.md`, `/.well-known/storelens.json`, `/api/v1/platform-config`, and the dashboard
developer panel use the resolved values.

The important deployment variables are:

- `STORELENS_PUBLIC_URL`: dashboard origin.
- `STORELENS_PUBLIC_MCP_URL`: authenticated MCP URL, normally `<origin>/mcp`.
- `STORELENS_ENDPOINT_PROFILE`: `cloudflare` for this deployment.
- `STORELENS_PUBLIC_READS`: allows the public dashboard to read data without exposing writes.

## Deploy

Docker Desktop and a Cloudflare account with Workers Containers enabled are required.

```powershell
cd deploy/cloudflare
npm install
npm exec wrangler -- secret put STORELENS_API_KEY
npm exec wrangler -- secret put STORELENS_MCP_TOKEN
npm run deploy
```

`STORELENS_API_KEY` protects REST mutations through `X-API-Key`. The dashboard stores it
only in the current browser's local storage. `STORELENS_MCP_TOKEN` protects `/mcp` as an
HTTP Bearer token and should be supplied to Codex through `bearer_token_env_var`, never
written into a skill or committed configuration.

After the first deployment, put the exact Workers URL in the `cloudflare` profile of
`config/endpoints.json` and in `wrangler.jsonc`, then deploy once more. A custom domain
can replace it later without rewriting skills.

## Persistence model

There is deliberately one scale-to-zero `lite` container instance and one private state Durable Object. The
launcher creates a consistent SQLite backup, gzip-compresses it, and checkpoints it every
30 seconds, plus once during graceful shutdown. On a cold start it restores the latest
checkpoint before serving traffic. At most the most recent checkpoint interval can be lost
after an ungraceful container failure. The container sleeps after 30 idle minutes to limit
billable memory while retaining a practical demo experience.

This is appropriate for a hackathon and a single-workspace pilot. A multi-tenant product
should move primary data to a managed multi-tenant database, add per-user OAuth and scoped
authorization, and run one isolated workspace/tenant boundary per customer.
