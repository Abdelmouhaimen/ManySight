import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, formatDuration } from "./api.js";
import {
  BarChart,
  DataTable,
  EmptyState,
  ErrorState,
  FlowTable,
  LoadingState,
  MetricCard,
  Modal,
  MultiLineChart,
  Panel,
  PageHeader,
  RangeSelect,
  StateSummary,
  ActivityMap,
} from "./components.jsx";

const SUBJECT_LABELS = { detection: "Detections", measurement: "Measurements", state: "States" };

function rangeToQuery(rangeSeconds) {
  const until = Date.now() / 1000;
  return { since: until - rangeSeconds, until };
}

function useAnalyticsQuery(definition, rangeSeconds, liveTick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    let cancelled = false;
    setState((current) => ({ ...current, loading: true }));
    api
      .post("/analytics/query", {
        subject: definition.subject,
        measures: definition.measures,
        filters: definition.filters || {},
        grouping: definition.grouping || {},
        range: rangeToQuery(rangeSeconds),
      })
      .then((data) => {
        if (!cancelled) setState({ loading: false, data, error: null });
      })
      .catch((error) => {
        if (!cancelled) setState({ loading: false, data: null, error });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeSeconds, JSON.stringify(definition.measures), JSON.stringify(definition.filters),
     JSON.stringify(definition.grouping), definition.subject, liveTick]);
  return state;
}

function scalarValue(row, measure) {
  const value = row?.[measure];
  if (value == null) return "—";
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (!entries.length) return "—";
    return entries.map(([k, v]) => `${k}: ${typeof v === "number" ? v : v}`).join(", ");
  }
  return value;
}

function measureLabel(definition, measure) {
  if (measure !== "active_entities") return measure.replaceAll("_", " ");
  const entityType = definition.filters?.entity_types?.[0] || "entity";
  return entityType === "person" ? "People present" : `${entityType} present`;
}

function AnalysisBody({ definition, context, result, loading, error }) {
  if (loading && !result) return <LoadingState label="Loading analysis…" />;
  if (error) return <ErrorState error={error} />;
  if (!result) return <EmptyState title="No data">Nothing returned for this analysis.</EmptyState>;
  const { shape, rows, dimensions, measures } = result;

  if (shape === "heatmap") {
    return <ActivityMap store={context.store} zones={context.zones} sources={context.sources} points={rows} />;
  }
  if (dimensions.includes("zone_from") && dimensions.includes("zone_to")) {
    return (
      <FlowTable
        data={{ links: rows.map((r) => ({ from: r.zone_from, to: r.zone_to, from_name: r.zone_from_name,
                                          to_name: r.zone_to_name, count: r.transition_count })) }}
      />
    );
  }
  if (definition.subject === "state") {
    const zoneNames = Object.fromEntries((context.sources || []).map((s) => [s.id, s.name]));
    const series = rows.map((r) => ({
      source_id: r.source_id, source_name: zoneNames[r.source_id] || `source ${r.source_id}`,
      name: r.name, entity_id: r.entity_id,
      totals: r.duration || r.average_duration || {},
      stale: false,
    }));
    return <StateSummary series={series} />;
  }
  if (shape === "scalar") {
    if (measures.length === 1) {
      return (
        <MetricCard primary label={definition.name} value={scalarValue(rows[0], measures[0])}
                   note={definition.unit || ""} />
      );
    }
    return (
      <div className="metric-grid">
        {measures.map((m) => (
          <MetricCard key={m} label={m.replaceAll("_", " ")} value={scalarValue(rows[0], m)} />
        ))}
      </div>
    );
  }
  if (shape === "timeseries") {
    const splitKey = dimensions.find((d) => d !== "t");
    if (splitKey) {
      const groups = {};
      rows.forEach((row) => {
        const key = String(row[splitKey] ?? "—");
        (groups[key] ||= []).push({ t: row.t, count: row[measures[0]] ?? 0 });
      });
      const sourceNames = Object.fromEntries((context.sources || []).map((source) => [String(source.id), source.name]));
      const series = Object.entries(groups).map(([label, points]) => ({
        label: splitKey === "source_id" ? sourceNames[label] || `Source ${label}` : label,
        points,
      }));
      return <MultiLineChart series={series} empty="No data in this period." />;
    }
    return <MultiLineChart
      series={[{ label: measureLabel(definition, measures[0]), points: rows.map((r) => ({ t: r.t, count: r[measures[0]] ?? 0 })) }]}
      empty="No processed samples in this period."
      gapAfterSeconds={result.metadata?.display_gap_s ?? null}
    />;
  }
  // categorical: dimensions typically ["zone_id"] or ["measurement_name"] etc.
  const dimensionKey = dimensions[0];
  if (dimensionKey === "zone_id" && measures.length === 1) {
    return (
      <BarChart
        rows={rows.map((r) => ({ label: r.zone_name || `zone ${r.zone_id}`, value: r[measures[0]] || 0,
                                 detail: r.visits != null ? `${r.visits} visits` : undefined }))}
        empty="No data for this grouping."
      />
    );
  }
  const columns = [
    ...dimensions.map((d) => ({ key: d, label: d.replaceAll("_", " ") })),
    ...measures.map((m) => ({
      key: m, label: m.replaceAll("_", " "),
      format: (v) => (typeof v === "object" && v ? Object.entries(v).map(([k, n]) => `${k}: ${n}`).join(", ") : v ?? "—"),
    })),
  ];
  return <DataTable columns={columns} rows={rows} empty="No rows for this analysis." />;
}

export function AnalysisCard({ definition, rangeSeconds, context, liveTick, onEdit, onDelete }) {
  const { loading, data, error } = useAnalyticsQuery(definition, rangeSeconds, liveTick);
  return (
    <Panel
      title={definition.name}
      subtitle={definition.question || SUBJECT_LABELS[definition.subject]}
      action={
        <div className="card-actions">
          {onEdit && (
            <button className="icon-button" onClick={() => onEdit(definition)} aria-label="Edit analysis">
              ⚙
            </button>
          )}
          {onDelete && (
            <button className="icon-button danger" onClick={() => onDelete(definition)} aria-label="Delete analysis">
              <Trash2 size={14} />
            </button>
          )}
        </div>
      }
    >
      <AnalysisBody definition={definition} context={context} result={data} loading={loading} error={error} />
      {definition.migration_note && (
        <p className="definition-note">Migrated from a legacy insight: {definition.migration_note}</p>
      )}
    </Panel>
  );
}

function analysisName(entityType, zone) {
  const subject = entityType === "person"
    ? "People"
    : `${entityType.charAt(0).toUpperCase()}${entityType.slice(1)}`;
  return zone ? `${subject} in ${zone.name}` : `${subject} over time`;
}

function BuilderModal({ existing, capabilities, zones, onClose, onSaved }) {
  const [entityType, setEntityType] = useState(existing?.filters?.entity_types?.[0] || "");
  const [zoneId, setZoneId] = useState(existing?.filters?.zone_ids?.[0] ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const entityTypes = capabilities?.entity_types || [];

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!entityType) throw new Error("Choose an entity type");
      const zone = zones.find((item) => item.id === Number(zoneId));
      const body = {
        name: analysisName(entityType, zone),
        question: zone
          ? `How many ${entityType} tracks were present at the same time in ${zone.name}?`
          : `How many ${entityType} tracks were present at the same time?`,
        subject: "detection",
        measures: ["active_entities"],
        filters: {
          entity_types: [entityType],
          ...(zone ? { zone_ids: [zone.id] } : {}),
        },
        grouping: { primary: "time", split_by: [] },
        presentation: "line",
        pinned: existing?.pinned || false,
      };
      const saved = existing
        ? await api.patch(`/analyses/${existing.id}`, body)
        : await api.post("/analyses", body);
      onSaved(saved);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={existing ? "Edit entity count" : "Add entity count"}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-dark" onClick={save} disabled={saving || !entityType}>
            {saving ? "Saving…" : existing ? "Save" : "Show count"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        {error && <p className="field-error field-full">{error}</p>}
        <label className="field field-full">
          <span>Entity type *</span>
          <select value={entityType} onChange={(event) => setEntityType(event.target.value)} required>
            <option value="">Choose an entity type</option>
            {entityTypes.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          {!entityTypes.length && (
            <small>No entity types recorded yet. They appear after a worker submits detections.</small>
          )}
        </label>
        <label className="field field-full">
          <span>Zone (optional)</span>
          <select value={zoneId} onChange={(event) => setZoneId(event.target.value)}>
            <option value="">Everywhere</option>
            {zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
          </select>
        </label>
      </div>
    </Modal>
  );
}

export function AnalyticsPage({ notify }) {
  const [analyses, setAnalyses] = useState([]);
  const [capabilities, setCapabilities] = useState(null);
  const [context, setContext] = useState({ store: null, zones: [], sources: [] });
  const [rangeSeconds, setRangeSeconds] = useState(86400);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [showBuilder, setShowBuilder] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [analysesList, caps, store, zones, sources] = await Promise.all([
        api.get("/analyses?include_hidden=false"),
        api.get("/analytics/capabilities"),
        api.get("/store"),
        api.get("/zones"),
        api.get("/sources"),
      ]);
      setAnalyses(analysesList);
      setCapabilities(caps);
      setContext({ store, zones, sources });
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const remove = async (definition) => {
    if (!window.confirm(`Delete ${definition.name}?`)) return;
    try {
      await api.del(`/analyses/${definition.id}`);
      setAnalyses((current) => current.filter((a) => a.id !== definition.id));
      notify("Entity count deleted", definition.name);
    } catch (err) {
      notify("Couldn't delete entity count", err.message, "error");
    }
  };

  if (loading && !analyses.length) return <LoadingState label="Loading entity counts…" />;
  if (error && !analyses.length) return <ErrorState error={error} retry={load} />;

  return (
    <>
      <PageHeader
        eyebrow="Analytics"
        title="Entity counts over time"
        description="Choose an entity type and, optionally, one zone. Each point is the count submitted for one processed camera frame at its exact timestamp; zero is explicit and neighboring timestamps are never merged."
        actions={
          <>
            <RangeSelect value={rangeSeconds} onChange={setRangeSeconds} />
            <button className="button button-dark" onClick={() => { setEditing(null); setShowBuilder(true); }}>
              <Plus size={14} /> Add entity count
            </button>
          </>
        }
      />
      {!analyses.length ? (
        <EmptyState title="No entity counts yet">
          Add a count by selecting an entity type. You can optionally restrict it to a zone.
        </EmptyState>
      ) : (
        <div className="insight-grid">
          {analyses.map((definition) => (
            <AnalysisCard
              key={definition.id}
              definition={definition}
              rangeSeconds={rangeSeconds}
              context={context}
              onEdit={(d) => { setEditing(d); setShowBuilder(true); }}
              onDelete={remove}
            />
          ))}
        </div>
      )}
      {showBuilder && (
        <BuilderModal
          existing={editing}
          capabilities={capabilities}
          zones={context.zones}
          onClose={() => setShowBuilder(false)}
          onSaved={(saved) => {
            setShowBuilder(false);
            setAnalyses((current) => {
              const others = current.filter((a) => a.id !== saved.id);
              return [...others, saved].sort((a, b) => a.sort_order - b.sort_order);
            });
            notify(editing ? "Entity count updated" : "Entity count added", saved.name);
          }}
        />
      )}
    </>
  );
}
