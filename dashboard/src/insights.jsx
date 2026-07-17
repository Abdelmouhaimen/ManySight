import { useEffect, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Bot,
  Pencil,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { api, formatDuration } from "./api.js";
import {
  ActivityMap,
  Badge,
  BarChart,
  DataTable,
  EmptyState,
  ErrorState,
  FlowTable,
  LineChart,
  LoadingState,
  MetricCard,
  Modal,
  MultiLineChart,
  PageHeader,
  Panel,
  RangeSelect,
  StateSummary,
} from "./components.jsx";

// The renderer only ever maps a definition onto these whitelisted analytics
// params and blocks — a definition can never inject markup or arbitrary queries.
const PARAM_WHITELIST = [
  "zone_id",
  "label",
  "group_by",
  "source_id",
  "bucket_s",
  "event_type",
  "job_id",
  "cell",
  "max_dwell_s",
];

function useInsightData(definition, range, liveTick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const load = async (quiet = false) => {
    if (!quiet) setState((c) => ({ ...c, loading: !c.data, error: null }));
    const until = Date.now() / 1000;
    const params = new URLSearchParams({
      since: String(until - range),
      until: String(until),
    });
    PARAM_WHITELIST.forEach((key) => {
      if (definition.params?.[key] != null && definition.params[key] !== "")
        params.set(key, definition.params[key]);
    });
    try {
      const data = await api.get(
        `/analytics/${definition.dataset}?${params.toString()}`,
      );
      setState({ loading: false, data, error: null });
    } catch (error) {
      setState((c) => ({ ...c, loading: false, error }));
    }
  };
  useEffect(() => {
    load();
  }, [range, definition.dataset, JSON.stringify(definition.params)]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!liveTick) return;
    const timer = window.setTimeout(() => load(true), 500);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}

function metricValue(definition, data) {
  if (definition.dataset === "summary") {
    const field = definition.params?.field || "tracks";
    const value = data[field];
    return {
      value: value == null ? "—" : Number(value).toLocaleString(),
      note: `summary · ${field}`,
    };
  }
  if (definition.dataset === "dwell") {
    const rows = definition.params?.zone_id
      ? data.rows.filter((row) => row.zone_id === definition.params.zone_id)
      : data.rows;
    const visits = rows.reduce((sum, row) => sum + row.visits, 0);
    const avg = visits
      ? rows.reduce((sum, row) => sum + row.avg_s * row.visits, 0) / visits
      : null;
    return {
      value: avg == null ? "—" : formatDuration(avg),
      note: visits ? `${visits} derived visits` : "No enter/exit pairs yet",
    };
  }
  if (definition.dataset === "occupancy") {
    const peak = Math.max(...data.series.map((point) => point.count), 0);
    return { value: peak.toLocaleString(), note: "Peak per interval" };
  }
  return { value: "—", note: "" };
}

function InsightBody({ definition, range, context, liveTick }) {
  const { loading, data, error } = useInsightData(definition, range, liveTick);
  if (loading && !data) return <LoadingState label="Loading insight…" />;
  if (error) return <ErrorState error={error} />;
  if (!data)
    return <EmptyState title="No data">Nothing returned for this insight.</EmptyState>;
  switch (definition.block) {
    case "metric": {
      const { value, note } = metricValue(definition, data);
      return (
        <MetricCard
          primary
          label={definition.zone_name || definition.dataset}
          value={value}
          note={note}
        />
      );
    }
    case "line": {
      if (definition.dataset === "counts") {
        if (!definition.params?.label && data.series.length > 1) {
          return (
            <MultiLineChart
              series={data.series}
              unit={definition.unit ? ` ${definition.unit}` : ""}
              empty="Post labelled count events to populate this view."
            />
          );
        }
        const series =
          data.series.find((s) => s.label === definition.params?.label) ||
          data.series[0];
        return (
          <LineChart
            points={series?.points || []}
            unit={series ? ` ${series.label}` : ""}
            empty="Post labelled count events to populate this view."
          />
        );
      }
      if (definition.dataset === "occupancy" && data.groups?.length) {
        return (
          <MultiLineChart
            series={data.groups}
            unit={definition.unit ? ` ${definition.unit}` : ""}
            empty="Post labelled detections with stable track IDs to compare classes."
          />
        );
      }
      return (
        <LineChart
          points={data.series}
          unit={definition.unit ? ` ${definition.unit}` : " objects"}
          empty="Tracking events with stable IDs will populate this chart."
        />
      );
    }
    case "bar": {
      const rows = data.rows.map((row) => ({
        label:
          definition.params?.group_by && row.group !== "all"
            ? `${row.zone_name} · ${row.group}`
            : row.zone_name,
        value: row.avg_s,
        detail: `${row.visits} visits`,
      }));
      return (
        <BarChart
          rows={rows}
          unit=" sec"
          empty="Post paired zone_enter and zone_exit events."
        />
      );
    }
    case "table": {
      if (definition.dataset === "transitions")
        return (
          <DataTable
            columns={[
              { key: "from_name", label: "From" },
              { key: "to_name", label: "To" },
              { key: "count", label: "Moves" },
            ]}
            rows={data.links}
            empty="No zone transitions in this period."
          />
        );
      return (
        <DataTable
          columns={[
            { key: "zone_name", label: "Zone" },
            { key: "group", label: "Group" },
            { key: "visits", label: "Visits" },
            { key: "avg_s", label: "Avg", format: (v) => formatDuration(v) },
            { key: "total_s", label: "Total", format: (v) => formatDuration(v) },
          ]}
          rows={data.rows}
          empty="No derived visits in this period."
        />
      );
    }
    case "heatmap_map":
      return (
        <ActivityMap
          store={context.store}
          zones={context.zones}
          sources={context.sources}
          points={data.points}
        />
      );
    case "flow_matrix":
      return <FlowTable data={data} />;
    case "state_timeline":
      return <StateSummary series={data.series} />;
    default:
      return (
        <EmptyState title="Unsupported insight type">
          This insight type isn't supported by this dashboard version.
        </EmptyState>
      );
  }
}

export function InsightCard({
  definition,
  range,
  context,
  liveTick = 0,
  onEdit,
  onDelete,
  onPin,
  onMove,
  readOnly = false,
}) {
  return (
    <Panel
      className="insight-card"
      title={definition.title}
      subtitle={definition.question}
      action={
        <div className="insight-card-actions">
          {definition.created_by === "agent" && (
            <Badge tone="violet">
              <Bot size={12} /> agent
            </Badge>
          )}
          {definition.status !== "ready" && (
            <Badge tone={definition.status === "degraded" ? "danger" : "neutral"}>
              {definition.status}
            </Badge>
          )}
          {!readOnly && (
            <>
              {onMove && (
                <>
                  <button
                    className="icon-button"
                    onClick={() => onMove(definition, -1)}
                    aria-label={`Move ${definition.title} up`}
                  >
                    <ArrowUp size={14} />
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => onMove(definition, 1)}
                    aria-label={`Move ${definition.title} down`}
                  >
                    <ArrowDown size={14} />
                  </button>
                </>
              )}
              <button
                className="icon-button"
                onClick={() => onPin(definition)}
                aria-label={
                  definition.pinned
                    ? `Unpin ${definition.title} from Overview`
                    : `Pin ${definition.title} to Overview`
                }
              >
                {definition.pinned ? <PinOff size={14} /> : <Pin size={14} />}
              </button>
              <button
                className="icon-button"
                onClick={() => onEdit(definition)}
                aria-label={`Edit ${definition.title}`}
              >
                <Pencil size={14} />
              </button>
              <button
                className="icon-button danger"
                onClick={() => onDelete(definition)}
                aria-label={`Delete ${definition.title}`}
              >
                <Trash2 size={14} />
              </button>
            </>
          )}
          {readOnly && definition.pinned && (
            <Badge tone="neutral">
              <Pin size={12} /> pinned
            </Badge>
          )}
        </div>
      }
    >
      <InsightBody
        definition={definition}
        range={range}
        context={context}
        liveTick={liveTick}
      />
      {(definition.limitations || definition.unit) && (
        <p className="definition-note insight-limitations">
          {definition.unit && <span>Unit: {definition.unit}. </span>}
          {definition.limitations}
        </p>
      )}
    </Panel>
  );
}

export function InsightsPage({ liveTick = 0, notify }) {
  const [range, setRange] = useState(86400);
  const [insights, setInsights] = useState(null),
    [context, setContext] = useState(null),
    [error, setError] = useState(null),
    [editing, setEditing] = useState(null),
    [adding, setAdding] = useState(false);
  const load = async () => {
    try {
      const [defs, store, zones, sources] = await Promise.all([
        api.get("/insights"),
        api.get("/store"),
        api.get("/zones"),
        api.get("/sources"),
      ]);
      setInsights(defs);
      setContext({ store, zones, sources });
      setError(null);
    } catch (err) {
      setError(err);
    }
  };
  useEffect(() => {
    load();
  }, []);
  const visible = (insights || []).filter(
    (definition) => definition.status !== "retired",
  );
  const pin = async (definition) => {
    await api.put(`/insights/${definition.id}`, { pinned: !definition.pinned });
    notify(
      definition.pinned ? "Unpinned from Overview" : "Pinned to Overview",
      definition.title,
    );
    load();
  };
  const remove = async (definition) => {
    if (!window.confirm(`Delete insight “${definition.title}”?`)) return;
    await api.del(`/insights/${definition.id}`);
    notify("Insight removed", definition.title);
    load();
  };
  const move = async (definition, direction) => {
    const index = visible.findIndex((item) => item.id === definition.id);
    const neighbor = visible[index + direction];
    if (!neighbor) return;
    // swap sort_order; fall back to index positions when orders are equal
    const a = definition.sort_order,
      b = neighbor.sort_order;
    await Promise.all([
      api.put(`/insights/${definition.id}`, {
        sort_order: a === b ? index + direction : b,
      }),
      api.put(`/insights/${neighbor.id}`, {
        sort_order: a === b ? index : a,
      }),
    ]);
    load();
  };
  if (error) return <ErrorState error={error} retry={load} />;
  if (!insights || !context) return <LoadingState label="Loading insights…" />;
  return (
    <>
      <PageHeader
        eyebrow="Insights"
        title="Your registered views of the space"
        description="Every card is a registered definition over derived platform analytics — added by you from a template, or by an agent over MCP."
        actions={
          <>
            <RangeSelect value={range} onChange={setRange} />
            <button
              className="icon-button"
              onClick={load}
              aria-label="Refresh insights"
            >
              <RefreshCw size={16} />
            </button>
            <button
              className="button button-dark"
              onClick={() => setAdding(true)}
            >
              <Plus size={15} />
              Add insight
            </button>
          </>
        }
      />
      {!visible.length ? (
        <EmptyState
          title="No insights registered yet"
          action={
            <button
              className="button button-dark"
              onClick={() => setAdding(true)}
            >
              <Plus size={15} />
              Add your first insight
            </button>
          }
        >
          Insights are registered definitions, not built-in guesses. Add one
          from a template, or ask your agent to call register_insight over MCP
          after it posts observations.
        </EmptyState>
      ) : (
        <div className="insight-grid">
          {visible.map((definition) => (
            <InsightCard
              key={definition.id}
              definition={definition}
              range={range}
              context={context}
              liveTick={liveTick}
              onEdit={setEditing}
              onDelete={remove}
              onPin={pin}
              onMove={move}
            />
          ))}
        </div>
      )}
      {(adding || editing) && (
        <InsightModal
          existing={editing}
          zones={context.zones}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSaved={(saved) => {
            setAdding(false);
            setEditing(null);
            notify(
              editing ? "Insight updated" : "Insight registered",
              saved.title,
            );
            load();
          }}
        />
      )}
    </>
  );
}

function InsightModal({ existing, zones, onClose, onSaved }) {
  const [templates, setTemplates] = useState(null);
  const [parameterOptions, setParameterOptions] = useState({
    detection_labels: [],
    count_labels: [],
  });
  const [form, setForm] = useState(
    existing
      ? {
          title: existing.title,
          question: existing.question,
          block: existing.block,
          dataset: existing.dataset,
          params: existing.params || {},
          unit: existing.unit,
          limitations: existing.limitations,
          pinned: existing.pinned,
        }
      : null,
  );
  const [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    api
      .get("/insights/templates")
      .then((result) => {
        setParameterOptions(result.parameters || parameterOptions);
        if (!existing) setTemplates(result.templates);
      })
      .catch((err) => setError(err.message));
  }, [existing?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (template) =>
    setForm({
      title: template.title,
      question: template.question,
      block: template.block,
      dataset: template.dataset,
      params: template.params || {},
      unit: template.unit,
      limitations: template.limitations,
      pinned: false,
    });
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.title.trim()) throw new Error("A title is required");
      const body = { ...form, title: form.title.trim() };
      const saved = existing
        ? await api.put(`/insights/${existing.id}`, body)
        : await api.post("/insights", body);
      onSaved(saved);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };
  const supportsZone =
    form && ["dwell", "occupancy", "counts", "heatmap"].includes(form.dataset);
  const classParameter =
    form && ["occupancy", "heatmap"].includes(form.dataset)
      ? "detection"
      : form?.dataset === "counts"
        ? "count"
        : null;
  const discoveredLabels = classParameter
    ? parameterOptions[`${classParameter}_labels`] || []
    : [];
  const classLabels = [
    ...new Set(
      [form?.params?.label, ...discoveredLabels].filter(
        (value) => value != null && value !== "",
      ),
    ),
  ];
  const canCompareClasses =
    form?.block === "line" && ["occupancy", "counts"].includes(form.dataset);
  const classSelection = form?.params?.group_by === "label"
    ? "__compare__"
    : form?.dataset === "counts" && !form?.params?.label
      ? "__compare__"
      : form?.params?.label || "";
  const setClassSelection = (selection) => {
    const params = { ...form.params };
    delete params.label;
    delete params.group_by;
    if (selection === "__compare__" && form.dataset === "occupancy") {
      params.group_by = "label";
    }
    else if (selection) params.label = selection;
    setForm({ ...form, params });
  };
  return (
    <Modal
      title={existing ? `Edit ${existing.title}` : "Add insight"}
      onClose={onClose}
      wide
      footer={
        form && (
          <>
            <button className="button button-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              className="button button-dark"
              onClick={save}
              disabled={saving}
            >
              {saving
                ? "Saving…"
                : existing
                  ? "Save insight"
                  : "Register insight"}
            </button>
          </>
        )
      }
    >
      {!form ? (
        !templates ? (
          <LoadingState label="Loading templates…" />
        ) : (
          <div className="template-picker">
            <p className="definition-note">
              Templates are assembled from the data actually present in this
              workspace. Greyed entries state what they need first.
            </p>
            {templates.map((template) => (
              <button
                key={template.key}
                className="template-option"
                disabled={!template.available}
                onClick={() => pick(template)}
              >
                <div>
                  <strong>{template.title}</strong>
                  <small>
                    {template.available
                      ? template.question
                      : `Needs ${template.requires}`}
                  </small>
                </div>
                <Badge tone={template.available ? "violet" : "neutral"}>
                  {template.block.replaceAll("_", " ")}
                </Badge>
              </button>
            ))}
          </div>
        )
      ) : (
        <div className="form-grid">
          <label className="field field-full">
            <span>Title</span>
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
          </label>
          <label className="field field-full">
            <span>Question this answers</span>
            <input
              value={form.question}
              onChange={(e) => setForm({ ...form, question: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Visualization</span>
            <input value={form.block.replaceAll("_", " ")} readOnly disabled />
          </label>
          <label className="field">
            <span>Dataset</span>
            <input value={form.dataset} readOnly disabled />
          </label>
          {supportsZone && (
            <label className="field">
              <span>Zone</span>
              <select
                value={form.params.zone_id ?? ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    params: {
                      ...form.params,
                      zone_id: e.target.value ? Number(e.target.value) : undefined,
                    },
                  })
                }
              >
                <option value="">All zones</option>
                {zones.map((zone) => (
                  <option key={zone.id} value={zone.id}>
                    {zone.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {classParameter && (
            <label className="field">
              <span>{classParameter === "detection" ? "Detection class" : "Count label"}</span>
              <select
                value={classSelection}
                onChange={(e) => setClassSelection(e.target.value)}
                disabled={!classLabels.length}
              >
                {classParameter === "detection" && (
                  <option value="">All classes combined</option>
                )}
                {canCompareClasses && (
                  <option value="__compare__">Compare classes as separate lines</option>
                )}
                {!classLabels.length && (
                  <option value="">No labelled {classParameter} events found</option>
                )}
                {classLabels.map((label) => (
                  <option key={label} value={label}>
                    {label}
                  </option>
                ))}
              </select>
              <small>
                Uses the top-level label posted by the worker, not an attribute.
              </small>
            </label>
          )}
          <label className="field">
            <span>Unit</span>
            <input
              value={form.unit}
              onChange={(e) => setForm({ ...form, unit: e.target.value })}
            />
          </label>
          <label className="field field-full">
            <span>Limitations (shown on the card)</span>
            <textarea
              rows="3"
              value={form.limitations}
              onChange={(e) =>
                setForm({ ...form, limitations: e.target.value })
              }
              placeholder="State honestly what this metric can and cannot claim."
            />
          </label>
          <label className="field checkbox-field">
            <span>
              <input
                type="checkbox"
                checked={!!form.pinned}
                onChange={(e) =>
                  setForm({ ...form, pinned: e.target.checked })
                }
              />{" "}
              Pin to Overview
            </span>
          </label>
        </div>
      )}
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}
