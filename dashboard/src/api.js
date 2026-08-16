const BASE = "/api/v1";

export const apiKey = () => localStorage.getItem("manysight_api_key") || "";
export const demoSessionId = () => localStorage.getItem("manysight_demo_session") || "";
export const setDemoSessionId = (value) => {
  if (value) localStorage.setItem("manysight_demo_session", value);
  else localStorage.removeItem("manysight_demo_session");
  window.dispatchEvent(new Event("manysight-demo-session"));
};

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (apiKey()) headers["X-API-Key"] = apiKey();
  if (demoSessionId()) headers["X-ManySight-Demo-Session"] = demoSessionId();
  const response = await fetch(BASE + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      /* non-json error */
    }
    if (response.status === 409 && demoSessionId()
        && /demo session is not active/i.test(String(detail))) {
      localStorage.removeItem("manysight_demo_session");
      window.dispatchEvent(new Event("manysight-demo-session"));
      if (!path.startsWith("/demo/")) return request(method, path, body);
    }
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response.text();
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  patch: (path, body) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),
};

export function assetUrl(path) {
  const parameters = [];
  if (apiKey()) parameters.push(`api_key=${encodeURIComponent(apiKey())}`);
  if (demoSessionId()) parameters.push(`demo_session=${encodeURIComponent(demoSessionId())}`);
  if (!parameters.length) return `${BASE}${path}`;
  return `${BASE}${path}${path.includes("?") ? "&" : "?"}${parameters.join("&")}`;
}

export const formatTime = (ts) =>
  new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
export const formatDateTime = (ts) =>
  ts
    ? new Date(ts * 1000).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
export const formatPreciseDateTime = (ts) =>
  ts == null
    ? "—"
    : new Date(ts * 1000).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        fractionalSecondDigits: 3,
      });
export const formatDuration = (seconds) => {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} sec`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} hr`;
};
