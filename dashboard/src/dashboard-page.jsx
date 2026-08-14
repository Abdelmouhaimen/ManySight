/* Dashboard — what has been set up for me to watch.
 *
 * A renderer, not a calculator: every number comes from a saved question the
 * platform evaluates. That is worth knowing once, in the docs — not in a
 * subtitle above every widget.
 */
import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "./api.js";
import { AnalysisCard } from "./analytics.jsx";
import { onTourEvent } from "./demo-tour.jsx";
import { EmptyState, ErrorState, LoadingState, OverflowMenu, PageHeader } from "./ui.jsx";

export function DashboardPage({ liveTick = 0, notify, demoReplay }) {
  const [state, setState] = useState({
    loading: true, error: null, dashboards: [], queries: [], context: {},
  });
  const [selectedId, setSelectedId] = useState("");

  const load = async () => {
    try {
      const [dashboards, queries, store, zones, sources] = await Promise.all([
        api.get("/dashboards"), api.get("/queries"), api.get("/store"),
        api.get("/zones"), api.get("/sources"),
      ]);
      setState({ loading: false, error: null, dashboards, queries, context: { store, zones, sources } });
      setSelectedId((current) => (current && dashboards.some((item) => String(item.id) === current)
        ? current : String(dashboards[0]?.id || "")));
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error }));
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // The guided demo creates its dashboard while this page is open.
  useEffect(() => onTourEvent(
    (detail) => { if (detail.kind === "workspace-changed") load(); },
  ), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!liveTick) return undefined;
    const timer = window.setTimeout(load, 500);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const dashboard = state.dashboards.find((item) => String(item.id) === selectedId);
  const queryById = useMemo(
    () => new Map(state.queries.map((query) => [query.id, query])), [state.queries],
  );

  const remove = async () => {
    if (!dashboard) return;
    if (!window.confirm(`Delete "${dashboard.name}"? The questions behind it are kept.`)) return;
    await api.del(`/dashboards/${dashboard.id}`);
    notify?.("Dashboard deleted");
    await load();
  };

  if (state.loading) return <LoadingState label="Loading dashboards…" />;
  if (state.error) return <ErrorState error={state.error} retry={load} />;

  return (
    <>
      <PageHeader
        title={dashboard?.name || "Dashboard"}
        description={dashboard?.description || ""}
        actions={
          <>
            {state.dashboards.length > 1 && (
              <label className="select-control">
                <span className="sr-only">Dashboard</span>
                <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
                  {state.dashboards.map((item) => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </label>
            )}
            <button className="icon-button" onClick={load} aria-label="Refresh">
              <RefreshCw size={16} />
            </button>
            {dashboard && (
              <OverflowMenu
                label="Dashboard actions"
                items={[{ label: "Delete dashboard", destructive: true, onSelect: remove }]}
              />
            )}
          </>
        }
      />
      {!dashboard ? (
        <EmptyState title="No dashboard yet">
          Dashboards created for this workspace will appear here.
        </EmptyState>
      ) : (
        <div className="widget-grid">
          {dashboard.widgets.map((widget) => {
            const query = queryById.get(widget.query_id);
            if (!query) {
              return (
                <EmptyState key={widget.id} tone="error" title="This widget is unavailable">
                  Its data source no longer exists.
                </EmptyState>
              );
            }
            const demoKpi = demoReplay?.session && query.subject === "fused_entity"
              && query.measures?.includes("current_occupancy") ? demoReplay.replay?.kpi : null;
            const resultOverride = demoKpi ? {
              shape: "scalar", dimensions: [], measures: ["current_occupancy"],
              rows: [{
                current_occupancy: demoKpi.value, quality: demoKpi.quality,
                as_of: demoKpi.as_of, evidence: demoKpi.evidence,
              }],
              metadata: { evidence: demoKpi.evidence, source: "guided_demo_derived_cache" },
            } : null;
            return (
              <AnalysisCard
                key={widget.id}
                definition={{ ...query, name: widget.title, presentation: widget.presentation }}
                rangeSeconds={86400}
                context={state.context}
                liveTick={liveTick}
                resultOverride={resultOverride}
                tour={demoKpi ? "dashboard-kpi" : null}
              />
            );
          })}
          {!dashboard.widgets.length && (
            <EmptyState tone="no-data" title="Nothing on this dashboard yet" />
          )}
        </div>
      )}
    </>
  );
}
