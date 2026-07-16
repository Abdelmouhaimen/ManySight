import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  BellRing,
  Camera,
  Gauge,
  Menu,
  Settings2,
  X,
} from "lucide-react";
import { api, apiKey } from "./api.js";
import { BrandMark, EnvironmentBadge, Toast } from "./components.jsx";
import {
  ConfigurePage,
  EventsPage,
  InsightsPage,
  OverviewPage,
  StreamsPage,
} from "./pages.jsx";
import "./styles.css";

const NAV = [
  ["overview", "Overview", Gauge],
  ["insights", "Insights", Activity],
  ["events", "Events", BellRing],
  ["streams", "Streams", Camera],
  ["configure", "Configure", Settings2],
];

const SPACE_LABELS = {
  store: "Retail operations",
  school: "School operations",
  office: "Workplace operations",
  warehouse: "Warehouse operations",
  public_space: "Public-space operations",
  custom: "Physical-space intelligence",
};

function currentRoute() {
  const route = window.location.hash.replace("#", "");
  return NAV.some(([value]) => value === route) ? route : "overview";
}

function App() {
  const [route, setRoute] = useState(currentRoute());
  const [mobileOpen, setMobileOpen] = useState(false);
  const [shell, setShell] = useState({ store: null, sources: [], alerts: [] });
  const [liveStatus, setLiveStatus] = useState("connecting");
  const [liveTick, setLiveTick] = useState(0);
  const [initialSignal, setInitialSignal] = useState(null);
  const [toast, setToast] = useState(null);

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
      notify("Dashboard connection failed", error.message, "error");
    }
  };

  useEffect(() => {
    refreshShell();
    const onHash = () => {
      setRoute(currentRoute());
      setMobileOpen(false);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const suffix = apiKey() ? `?api_key=${encodeURIComponent(apiKey())}` : "";
    const stream = new EventSource(`/api/v1/stream${suffix}`);
    stream.onopen = () => setLiveStatus("live");
    stream.onerror = () => setLiveStatus("offline");
    const onData = () => setLiveTick((value) => value + 1);
    stream.addEventListener("batch_summary", onData);
    stream.addEventListener("cv_event", onData);
    stream.addEventListener("alert", (event) => {
      onData();
      try {
        const signal = JSON.parse(event.data);
        notify(
          signal.title,
          "A new signal is ready for human review.",
          "warning",
        );
        refreshShell();
      } catch {
        /* malformed optional notification */
      }
    });
    return () => stream.close();
  }, []);

  useEffect(() => {
    if (liveTick && liveTick % 5 === 0) refreshShell();
  }, [liveTick]);

  const openSignal = (signal) => {
    setInitialSignal(signal);
    window.location.hash = "events";
  };
  const openSignals = shell.alerts.filter((alert) =>
    ["new", "in_review"].includes(
      alert.status || (alert.acknowledged ? "resolved" : "new"),
    ),
  ).length;
  const online = shell.sources.filter(
    (source) => source.status === "online",
  ).length;
  const Page =
    route === "overview"
      ? OverviewPage
      : route === "insights"
        ? InsightsPage
        : route === "events"
          ? EventsPage
          : route === "streams"
            ? StreamsPage
            : ConfigurePage;

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <button
          className="mobile-menu-button"
          onClick={() => setMobileOpen((value) => !value)}
          aria-label="Toggle navigation"
        >
          {mobileOpen ? <X /> : <Menu />}
        </button>
        <a className="app-logo" href="#overview">
          <BrandMark />
          <span>ManySight</span>
        </a>
        <div className="workspace-identity">
          <strong>{shell.store?.name || "Loading workspace"}</strong>
          <small>
            {SPACE_LABELS[shell.store?.space_type] ||
              "Physical-space intelligence"}{" "}
            · POC
          </small>
        </div>
        <div className="topbar-status">
          <EnvironmentBadge value={shell.store?.environment || "setup"} />
          <span className={`live-indicator live-${liveStatus}`}>
            <i />
            {liveStatus === "live"
              ? "Event stream live"
              : liveStatus === "offline"
                ? "Event stream offline"
                : "Connecting"}
          </span>
          <span className="stream-count">
            {online}/{shell.sources.length} streams online
          </span>
        </div>
      </header>
      <aside className={`app-sidebar ${mobileOpen ? "mobile-open" : ""}`}>
        <nav aria-label="Dashboard sections">
          {NAV.map(([value, label, Icon]) => (
            <a
              key={value}
              href={`#${value}`}
              className={route === value ? "active" : ""}
            >
              <Icon size={17} />
              <span>{label}</span>
              {value === "events" && openSignals > 0 && <b>{openSignals}</b>}
            </a>
          ))}
        </nav>
        <div className="sidebar-note">
          <span>POC</span>
          <p>
            Metrics and health states require pilot validation before
            operational use.
          </p>
        </div>
      </aside>
      <main className="app-main">
        <Page
          liveTick={liveTick}
          openSignal={openSignal}
          initialSignal={initialSignal}
          clearInitial={() => setInitialSignal(null)}
          notify={notify}
          refreshShell={refreshShell}
        />
      </main>
      <Toast toast={toast} dismiss={() => setToast(null)} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
