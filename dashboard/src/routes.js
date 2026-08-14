/* Canonical hash routes.
 *
 * The product previously routed `#overview` to a page labelled Dashboard, kept
 * six undocumented aliases, and silently rendered the Dashboard for anything
 * unrecognised — so a typo looked like a working page. Routing now has one
 * canonical name per page, aliases that *rewrite the hash* so a stale bookmark
 * heals itself, and an explicit fallback that also normalises.
 */

export const ROUTES = ["dashboard", "live", "review", "observations", "sources", "setup", "demo"];

export const DEFAULT_ROUTE = "dashboard";

/** Old hashes kept working. Each resolves to a canonical route and rewrites. */
export const ROUTE_ALIASES = {
  overview: "dashboard",
  insights: "dashboard",
  analytics: "dashboard",
  events: "review",
  streams: "sources",
  detections: "observations",
  configure: "setup",
};

/** Sub-views that live inside a page, addressed as `#page/sub`. */
export const SUBVIEWS = {
  review: ["alerts", "rules"],
  setup: ["space", "cameras", "advanced"],
};

/**
 * Resolve a raw hash into `{route, subview, canonical, redirected}`.
 * `redirected` tells the shell to rewrite `location.hash`, so an alias or a
 * typo becomes the canonical URL instead of lingering in the address bar.
 */
export function resolveRoute(rawHash) {
  const cleaned = String(rawHash || "").replace(/^#/, "").replace(/^\/+|\/+$/g, "");
  const [head = "", tail = ""] = cleaned.split("/");
  const name = head.toLowerCase();
  const aliased = ROUTE_ALIASES[name];
  const route = ROUTES.includes(name) ? name : aliased || DEFAULT_ROUTE;
  const allowed = SUBVIEWS[route] || [];
  const subview = allowed.includes(tail.toLowerCase()) ? tail.toLowerCase() : allowed[0] || null;
  const canonical = subview && allowed.length > 1 ? `${route}/${subview}` : route;
  return { route, subview, canonical, redirected: cleaned !== canonical };
}

export const routeHref = (route, subview = null) => `#${subview ? `${route}/${subview}` : route}`;
