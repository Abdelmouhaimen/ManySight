import { DurableObject, env } from "cloudflare:workers";
import { Container, getContainer, switchPort } from "@cloudflare/containers";


const STATE_CHUNK_BYTES = 64 * 1024;


async function secureEqual(left, right) {
  const encoder = new TextEncoder();
  const [leftHash, rightHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const a = new Uint8Array(leftHash);
  const b = new Uint8Array(rightHash);
  let mismatch = a.length ^ b.length;
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    mismatch |= a[index] ^ b[index];
  }
  return mismatch === 0;
}


export class StoreLensContainer extends Container {
  defaultPort = 8080;
  requiredPorts = [8080, 8001];
  sleepAfter = "30m";
  enableInternet = true;
  envVars = {
    PORT: "8080",
    STORELENS_API_KEY: env.STORELENS_API_KEY,
    STORELENS_ENDPOINT_PROFILE: env.STORELENS_ENDPOINT_PROFILE,
    STORELENS_PUBLIC_URL: env.STORELENS_PUBLIC_URL,
    STORELENS_PUBLIC_MCP_URL: env.STORELENS_PUBLIC_MCP_URL,
    STORELENS_PUBLIC_READS: env.STORELENS_PUBLIC_READS,
    STORELENS_SEED_DEMO: env.STORELENS_SEED_DEMO,
    STORELENS_STATE_URL: env.STORELENS_STATE_URL,
    STORELENS_BACKUP_INTERVAL_S: env.STORELENS_BACKUP_INTERVAL_S,
  };
}


export class StoreLensState extends DurableObject {
  async fetch(request) {
    if (request.method === "GET") {
      const meta = await this.ctx.storage.get("snapshot:meta");
      if (!meta) return new Response(null, { status: 404 });
      const result = new Uint8Array(meta.byteLength);
      let offset = 0;
      for (let index = 0; index < meta.chunkCount; index += 1) {
        const chunk = await this.ctx.storage.get(`snapshot:chunk:${index}`);
        if (!chunk) return new Response("Incomplete state snapshot", { status: 503 });
        result.set(new Uint8Array(chunk), offset);
        offset += chunk.byteLength;
      }
      return new Response(result, {
        headers: {
          "Content-Type": meta.contentType || "application/vnd.sqlite3",
          ...(meta.contentEncoding ? { "Content-Encoding": meta.contentEncoding } : {}),
          "Cache-Control": "no-store",
        },
      });
    }
    if (request.method === "PUT") {
      const snapshot = new Uint8Array(await request.arrayBuffer());
      const previous = await this.ctx.storage.get("snapshot:meta");
      const chunkCount = Math.ceil(snapshot.byteLength / STATE_CHUNK_BYTES);
      for (let index = 0; index < chunkCount; index += 1) {
        const start = index * STATE_CHUNK_BYTES;
        await this.ctx.storage.put(
          `snapshot:chunk:${index}`,
          snapshot.slice(start, start + STATE_CHUNK_BYTES),
        );
      }
      if (previous?.chunkCount > chunkCount) {
        const obsolete = [];
        for (let index = chunkCount; index < previous.chunkCount; index += 1) {
          obsolete.push(`snapshot:chunk:${index}`);
        }
        await this.ctx.storage.delete(obsolete);
      }
      await this.ctx.storage.put("snapshot:meta", {
        byteLength: snapshot.byteLength,
        chunkCount,
        contentType: request.headers.get("content-type") || "application/vnd.sqlite3",
        contentEncoding: request.headers.get("content-encoding") || "",
        savedAt: Date.now(),
      });
      return new Response(null, { status: 204 });
    }
    return new Response("Method not allowed", { status: 405 });
  }
}


StoreLensContainer.outboundByHost = {
  "storelens-state.do": async (request, workerEnv) => {
    const id = workerEnv.STORELENS_STATE.idFromName("primary");
    return workerEnv.STORELENS_STATE.get(id).fetch(request);
  },
};


export default {
  async fetch(request, workerEnv) {
    const url = new URL(request.url);
    const container = getContainer(workerEnv.STORELENS_CONTAINER, "primary");
    if (url.pathname === "/mcp" || url.pathname.startsWith("/mcp/")) {
      const supplied = request.headers.get("authorization") || "";
      const expected = `Bearer ${workerEnv.STORELENS_MCP_TOKEN || ""}`;
      if (!workerEnv.STORELENS_MCP_TOKEN || !(await secureEqual(supplied, expected))) {
        return new Response(JSON.stringify({ detail: "invalid or missing MCP bearer token" }), {
          status: 401,
          headers: {
            "Content-Type": "application/json",
            "WWW-Authenticate": "Bearer",
          },
        });
      }
      return container.fetch(switchPort(request, 8001));
    }
    return container.fetch(request);
  },
};
