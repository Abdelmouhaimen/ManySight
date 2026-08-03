const BASE = "/api/v1";

export const apiKey = () => localStorage.getItem("storelens_api_key") || "";

async function request(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (apiKey()) headers["X-API-Key"] = apiKey();
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
  upload: async (path, file) => {
    const form = new FormData();
    form.append("bundle", file);
    const headers = apiKey() ? { "X-API-Key": apiKey() } : {};
    const response = await fetch(BASE + path, { method: "POST", headers, body: form });
    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail || response.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return response.json();
  },
};

export function assetUrl(path) {
  const separator = path.includes("?") ? "&" : "?";
  return `${BASE}${path}${apiKey() ? `${separator}api_key=${encodeURIComponent(apiKey())}` : ""}`;
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
