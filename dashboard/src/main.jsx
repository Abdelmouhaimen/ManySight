import { lazy, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BellRing, Camera, Gauge, Menu, PlayCircle, Radio, ScanSearch, Settings2, X } from "lucide-react";
import { api, apiKey, demoSessionId } from "./api.js";
import { DEFAULT_ROUTE, resolveRoute, routeHref } from "./routes.js";
import { dataHealth, isOpenAlert } from "./status.js";
import { BrandMark, LoadingState, Toast } from "./ui.jsx";
import { ObservationsPage } from "./observations.jsx";
import { ReviewPage } from "./review.jsx";
import { SetupPage } from "./setup.jsx";
import { SourcesPage } from "./sources.jsx";
import { DashboardPage } from "./dashboard-page.jsx";
import { DemoPage } from "./demo.jsx";
import { useDemoReplay } from "./demo-replay.js";
import { DemoTourLayer, useDemoTour } from "./demo-tour.jsx";
import "./styles.css";

const LivePage = lazy(() =>
  import("./live.jsx").then((module) => ({ default: module.LivePage })),
);

/* The permanent navigation. The guided demo is deliberately absent: Try Demo in
 * the top bar is the single entry point, so the product does not advertise two
 * doors into the same thing. */
const NAV = [
  ["dashboard", "Dashboard", Gauge],
  ["live", "Live", Radio],
  ["review", "Review", BellRing],
  ["observations", "Observations", ScanSearch],
  ["sources", "Sources", Camera],
  ["setup", "Setup", Settings2],
];

const PAGES = {
  dashboard: DashboardPage,
  live: LivePage,
  review: ReviewPage,
  observations: ObservationsPage,
  sources: SourcesPage,
  setup: SetupPage,
  demo: DemoPage,
};

function App() {
  const [location, setLocation] = useState(() => resolveRoute(window.location.hash));
  const [mobileOpen, setMobileOpen] = useState(false);
  const [shell, setShell] = useState({ store: null, sources: [], alerts: [] });
  const [streamConnected, setStreamConnected] = useState(false);
  const [liveTick, setLiveTick] = useState(0);
  const [initialAlert, setInitialAlert] = useState(null);
  const [toast, setToast] = useState(null);
  const [demoId, setDemoId] = useState(demoSessionId());
  const demoReplay = useDemoReplay(demoId);
  // Tour state lives here so navigation between routes never resets it.
  const demoTour = useDemoTour(demoReplay);

  const notify = (title, message = "", tone = "success") => {
    setToast({ title, message, tone });
    window.setTimeout(() => setToast(null), 5000);
  };

  const refreshShell = async () => {
    try {
      const [store, sources, alerts] = await Promise.all([
        api.get("/store"),
        api.get("/sources"),
        api.get("/alerts?limit=100"),
      ]);
      setShell({ store, sources, alerts });
    } catch (error) {
      notify("Couldn't reach StoreLens", error.message, "error");
    }
  };

  // An alias or a typo rewrites the address bar instead of quietly rendering
  // something else, so a stale bookmark heals the first time it is used.
  useEffect(() => {
    if (location.redirected) window.location.replace(`#${location.canonical}`);
  }, [location.canonical, location.redirected]);

  useEffect(() => {
    refreshShell();
    const onHash = () => {
      setLocation(resolveRoute(window.location.hash));
      setMobileOpen(false);
    };
    window.addEventListener("hashchange", onHash);
    const onDemo = () => { setDemoId(demoSessionId()); refreshShell(); };
    window.addEventListener("storelens-demo-session", onDemo);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("storelens-demo-session", onDemo);
    };
  }, []);

  useEffect(() => {
    const parameters = [];
    if (apiKey()) parameters.push(`api_key=${encodeURIComponent(apiKey())}`);
    if (demoId) parameters.push(`demo_session=${encodeURIComponent(demoId)}`);
    const suffix = parameters.length ? `?${parameters.join("&")}` : "";
    const stream = new EventSource(`/api/v1/stream${suffix}`);
    stream.onopen = () => setStreamConnected(true);
    stream.onerror = () => setStreamConnected(false);
    const onData = () => setLiveTick((value) => value + 1);
    stream.addEventListener("batch_summary", onData);
    stream.addEventListener("cv_event", onData);
    stream.addEventListener("alert", (event) => {
      onData();
      try {
        const alert = JSON.parse(event.data);
        notify(alert.title, "A new alert is waiting for review.", "warning");
        refreshShell();
      } catch {
        /* malformed optional notification */
      }
    });
    return () => stream.close();
  }, [demoId]);

  useEffect(() => {
    if (liveTick && liveTick % 5 === 0) refreshShell();
  }, [liveTick]);

  const openAlert = (alert) => {
    setInitialAlert(alert);
    window.location.hash = routeHref("review", "alerts").slice(1);
  };
  const openAlertCount = shell.alerts.filter(isOpenAlert).length;
  const liveSources = shell.sources.filter(
    (source) => dataHealth(source).label === "Live",
  ).length;
  const Page = PAGES[location.route] || PAGES[DEFAULT_ROUTE];

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <button
          className="mobile-menu-button"
          onClick={() => setMobileOpen((value) => !value)}
          aria-label="Toggle navigation"
          aria-expanded={mobileOpen}
        >
          {mobileOpen ? <X /> : <Menu />}
        </button>
        <a className="app-logo" href={routeHref(DEFAULT_ROUTE)}>
          <BrandMark />
          <span>ManySight</span>
        </a>
        <span className="workspace-name">{shell.store?.name || "Workspace"}</span>
        <div className="topbar-status">
          {shell.sources.length > 0 && (
            <span
              className="source-counter"
              title={`${liveSources} of ${shell.sources.length} sources are sending data.`}
            >
              <i className={liveSources ? "is-live" : ""} />
              {liveSources}/{shell.sources.length} live
            </span>
          )}
          {/* Truthful and subtle: this reports the update channel, not data health. */}
          {!streamConnected && (
            <span className="stream-offline" title="Updates will resume automatically.">
              Reconnecting…
            </span>
          )}
          <a
            className={`button ${demoId ? "button-demo-active" : "button-secondary"}`}
            href={routeHref("demo")}
            data-demo-tour="try-demo"
          >
            <PlayCircle size={14} /> {demoId ? "In demo" : "Try Demo"}
          </a>
        </div>
      </header>
      <aside className={`app-sidebar ${mobileOpen ? "mobile-open" : ""}`}>
        <nav aria-label="Sections">
          {NAV.map(([value, label, Icon]) => (
            <a
              key={value}
              href={routeHref(value)}
              className={location.route === value ? "active" : ""}
              aria-current={location.route === value ? "page" : undefined}
              data-demo-tour={`nav-${value}`}
            >
              <Icon size={17} aria-hidden="true" />
              <span>{label}</span>
              {value === "review" && openAlertCount > 0 && (
                <b aria-label={`${openAlertCount} open`}>{openAlertCount}</b>
              )}
            </a>
          ))}
        </nav>
      </aside>
      <main className="app-main">
        <Suspense fallback={<LoadingState label="Loading…" />}>
          <Page
            liveTick={liveTick}
            subview={location.subview}
            openAlert={openAlert}
            initialAlert={initialAlert}
            clearInitial={() => setInitialAlert(null)}
            notify={notify}
            refreshShell={refreshShell}
            demoReplay={demoReplay}
          />
        </Suspense>
      </main>
      <Toast toast={toast} dismiss={() => setToast(null)} />
      <DemoTourLayer tour={demoTour} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
