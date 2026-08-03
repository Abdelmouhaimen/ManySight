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
      const series = Object.entries(groups).map(([label, points]) => ({ label, points }));
      return <MultiLineChart series={series} empty="No data in this period." />;
    }
    return <MultiLineChart series={[{ label: measures[0], points: rows.map((r) => ({ t: r.t, count: r[measures[0]] ?? 0 })) }]}
                           empty="No data in this period." />;
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

function BuilderModal({ existing, capabilities, zones, onClose, onSaved, notify }) {
  const [form, setForm] = useState(
    existing || { name: "", question: "", subject: "detection", measures: [], filters: {},
                 grouping: { primary: null, bucket: "1h", split_by: [] }, presentation: "", pinned: false },
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const availableMeasures = capabilities?.measures_by_subject?.[form.subject] || [];

  const toggleMeasure = (measure) =>
    setForm((current) => ({
      ...current,
      measures: current.measures.includes(measure)
        ? current.measures.filter((m) => m !== measure)
        : [...current.measures, measure],
    }));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.name.trim()) throw new Error("A name is required");
      if (!form.measures.length) throw new Error("Pick at least one measure");
      const body = { ...form, name: form.name.trim() };
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
      title={existing ? `Edit ${existing.name}` : "New analysis"}
      onClose={onClose}
      wide
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-dark" onClick={save} disabled={saving}>
            {saving ? "Saving…" : existing ? "Save analysis" : "Create analysis"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        {error && <p className="field-error field-full">{error}</p>}
        <label className="field field-full">
          <span>Name</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="field field-full">
          <span>Question this answers</span>
          <input value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} />
        </label>
        <label className="field">
          <span>Subject</span>
          <select
            value={form.subject}
            onChange={(e) => setForm({ ...form, subject: e.target.value, measures: [] })}
          >
            {Object.entries(SUBJECT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Grouping</span>
          <select
            value={form.grouping.primary || ""}
            onChange={(e) => setForm({ ...form, grouping: { ...form.grouping, primary: e.target.value || null } })}
          >
            <option value="">No grouping (KPI)</option>
            <option value="time">Over time</option>
            <option value="zone">By zone</option>
          </select>
        </label>
        {form.grouping.primary === "time" && (
          <label className="field">
            <span>Bucket</span>
            <select
              value={form.grouping.bucket}
              onChange={(e) => setForm({ ...form, grouping: { ...form.grouping, bucket: e.target.value } })}
            >
              {["1m", "5m", "15m", "1h", "1d"].map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
        )}
        <div className="field field-full">
          <span>Measures</span>
          <div className="chip-row">
            {availableMeasures.map((measure) => (
              <button
                key={measure}
                type="button"
                className={`chip ${form.measures.includes(measure) ? "chip-active" : ""}`}
                onClick={() => toggleMeasure(measure)}
              >
                {measure.replaceAll("_", " ")}
              </button>
            ))}
            {!availableMeasures.length && <small>Loading capabilities…</small>}
          </div>
        </div>
        {zones?.length > 0 && (
          <label className="field field-full">
            <span>Zone filter</span>
            <select
              value={form.filters.zone_ids?.[0] ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  filters: { ...form.filters, zone_ids: e.target.value ? [Number(e.target.value)] : undefined },
                })
              }
            >
              <option value="">All zones</option>
              {zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
            </select>
          </label>
        )}
        <label className="field checkbox-field field-full">
          <input type="checkbox" checked={form.pinned}
                onChange={(e) => setForm({ ...form, pinned: e.target.checked })} />
          <span>Pin to Dashboard</span>
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
      notify("Analysis deleted", definition.name);
    } catch (err) {
      notify("Couldn't delete analysis", err.message, "error");
    }
  };

  if (loading && !analyses.length) return <LoadingState label="Loading analyses…" />;
  if (error && !analyses.length) return <ErrorState error={error} retry={load} />;

  return (
    <>
      <PageHeader
        eyebrow="Analytics"
        title="Saved analyses"
        description="A saved analysis is a data question — subject, measures, filters, grouping — never a chart. Switching how it renders never creates a second record."
        actions={
          <>
            <RangeSelect value={rangeSeconds} onChange={setRangeSeconds} />
            <button className="button button-dark" onClick={() => { setEditing(null); setShowBuilder(true); }}>
              <Plus size={14} /> New analysis
            </button>
          </>
        }
      />
      {!analyses.length ? (
        <EmptyState title="No analyses yet">
          Create one from a subject (detection, measurement, or state) and the measures that answer your question.
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
          notify={notify}
          onClose={() => setShowBuilder(false)}
          onSaved={(saved) => {
            setShowBuilder(false);
            setAnalyses((current) => {
              const others = current.filter((a) => a.id !== saved.id);
              return [...others, saved].sort((a, b) => a.sort_order - b.sort_order);
            });
            notify(editing ? "Analysis updated" : "Analysis created", saved.name);
          }}
        />
      )}
    </>
  );
}
