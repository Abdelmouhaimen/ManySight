# Render free demo

This deployment intentionally starts with an empty StoreLens workspace and does not set
an API key or MCP Bearer token. Nginx exposes one Render port and routes `/mcp` to the MCP
process while routing every other request to the dashboard/API process.

Deploy the root `render.yaml` as a Render Blueprint, or create a **Web Service** manually
with runtime **Docker**, Dockerfile `./Dockerfile.render`, plan **Free**, and health check
path `/api/v1/health`. No environment variables are required when using the Blueprint.

Render supplies `RENDER_EXTERNAL_URL`; StoreLens uses it to advertise the correct dashboard,
REST, documentation, agent-guide, and MCP URLs automatically.

The free service filesystem is ephemeral. The dashboard is fresh on the first deployment,
and all configuration/detections can be lost when Render sleeps, restarts, or redeploys the
service. This topology is for a short demo, not persistent client use.
