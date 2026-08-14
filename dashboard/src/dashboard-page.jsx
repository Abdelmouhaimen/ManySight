import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Trash2 } from "lucide-react";
import { api } from "./api.js";
import { AnalysisCard } from "./analytics.jsx";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "./components.jsx";

export function GeneratedDashboardPage({ liveTick = 0, notify, demoReplay }) {
  const [state, setState] = useState({ loading: true, error: null, dashboards: [], queries: [], context: {} });
  const [selectedId, setSelectedId] = useState("");
  const load = async () => {
    try {
      const [dashboards, queries, store, zones, sources] = await Promise.all([
        api.get("/dashboards"), api.get("/queries"), api.get("/store"),
        api.get("/zones"), api.get("/sources"),
      ]);
      setState({ loading: false, error: null, dashboards, queries, context: { store, zones, sources } });
      setSelectedId((current) => current && dashboards.some((item) => String(item.id) === current)
        ? current : String(dashboards[0]?.id || ""));
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error }));
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!liveTick) return;
    const timer = window.setTimeout(load, 500);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const dashboard = state.dashboards.find((item) => String(item.id) === selectedId);
  const queryById = useMemo(() => new Map(state.queries.map((query) => [query.id, query])), [state.queries]);
  const remove = async () => {
    if (!dashboard || !window.confirm(`Delete dashboard “${dashboard.name}”? Saved queries and observations will remain.`)) return;
    await api.del(`/dashboards/${dashboard.id}`);
    notify?.("Dashboard deleted", "Its saved queries and observations were preserved.");
    await load();
  };
  if (state.loading) return <LoadingState label="Loading generated dashboards…" />;
  if (state.error) return <ErrorState error={state.error} retry={load} />;
  return <>
    <PageHeader
      eyebrow="Generated views"
      title={dashboard?.name || "Dashboards"}
      description={dashboard?.description || "Agent-created views render deterministic saved queries. StoreLens never executes generated UI code."}
      actions={<>
        {state.dashboards.length > 1 && <select aria-label="Dashboard" value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
          {state.dashboards.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>}
        <button className="icon-button" onClick={load} aria-label="Refresh dashboard"><RefreshCw size={16} /></button>
        {dashboard && <button className="icon-button danger" onClick={remove} aria-label="Delete dashboard"><Trash2 size={16} /></button>}
      </>}
    />
    {!dashboard ? <EmptyState title="No generated dashboard yet">
      Ask a StoreLens-connected coding agent to create a saved query, verify it, then add a compatible dashboard widget.
    </EmptyState> : <div className="insight-grid">
      {dashboard.widgets.map((widget) => {
        const query = queryById.get(widget.query_id);
        if (!query) return <EmptyState key={widget.id} title={widget.title}>The referenced saved query no longer exists.</EmptyState>;
        const demoKpi = demoReplay?.session && query.subject === "fused_entity"
          && query.measures?.includes("current_occupancy") ? demoReplay.replay?.kpi : null;
        const resultOverride = demoKpi ? {
          shape: "scalar", dimensions: [], measures: ["current_occupancy"],
          rows: [{ current_occupancy: demoKpi.value, quality: demoKpi.quality,
            as_of: demoKpi.as_of, evidence: demoKpi.evidence }],
          metadata: { evidence: demoKpi.evidence, source: "guided_demo_derived_cache" },
        } : null;
        return <AnalysisCard key={widget.id} definition={{ ...query, name: widget.title, presentation: widget.presentation }}
          rangeSeconds={86400} context={state.context} liveTick={liveTick} resultOverride={resultOverride}
          tour={demoKpi ? "dashboard-kpi" : null} />;
      })}
      {!dashboard.widgets.length && <EmptyState title="This dashboard has no widgets">
        Add widgets through MCP after validating their saved queries.
      </EmptyState>}
    </div>}
  </>;
}
