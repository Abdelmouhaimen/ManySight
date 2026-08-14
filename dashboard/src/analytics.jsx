import { useEffect, useState } from "react";
import { api } from "./api.js";
import {
  ActivityMap, BarChart, DataTable, FlowTable, MetricCard, MultiLineChart, StateSummary,
} from "./components.jsx";
import { EmptyState, ErrorState, LoadingState, Panel, ResultValue } from "./ui.jsx";

function useQueryResult(definition, rangeSeconds, liveTick, enabled = true) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const until = Date.now() / 1000;
    setState((current) => ({ ...current, loading: true }));
    api.post("/analytics/query", {
      subject: definition.subject,
      measures: definition.measures,
      filters: definition.filters || {},
      grouping: definition.grouping || {},
      range: { since: until - rangeSeconds, until },
    }).then((data) => {
      if (!cancelled) setState({ loading: false, data, error: null });
    }).catch((error) => {
      if (!cancelled) setState({ loading: false, data: null, error });
    });
    return () => { cancelled = true; };
  }, [rangeSeconds, JSON.stringify(definition.measures), JSON.stringify(definition.filters),
    JSON.stringify(definition.grouping), definition.subject, liveTick, enabled]); // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}

function scalarValue(row, measure) {
  const value = row?.[measure];
  if (value == null) return "—";
  if (typeof value !== "object") return value;
  const entries = Object.entries(value);
  return entries.length ? entries.map(([key, item]) => `${key}: ${item}`).join(", ") : "—";
}

function QueryBody({ definition, context, result, loading, error }) {
  if (loading && !result) return <LoadingState label="Loading query…" />;
  if (error) return <ErrorState error={error} />;
  if (!result) return <EmptyState tone="no-data" title="No data yet" />;
  const { shape, rows, dimensions, measures } = result;
  if (shape === "heatmap") {
    return <ActivityMap store={context.store} zones={context.zones} sources={context.sources} points={rows} />;
  }
  if (dimensions.includes("zone_from") && dimensions.includes("zone_to")) {
    return <FlowTable data={{ links: rows.map((row) => ({
      from: row.zone_from, to: row.zone_to, from_name: row.zone_from_name,
      to_name: row.zone_to_name, count: row.transition_count,
    })) }} />;
  }
  if (definition.subject === "state") {
    const sourceNames = Object.fromEntries((context.sources || []).map((source) => [source.id, source.name]));
    return <StateSummary series={rows.map((row) => ({
      source_id: row.source_id, source_name: sourceNames[row.source_id] || `source ${row.source_id}`,
      name: row.name, entity_id: row.entity_id,
      totals: row.duration || row.average_duration || {}, stale: false,
    }))} />;
  }
  if (shape === "scalar") {
    if (measures.length === 1) {
      const value = rows[0]?.[measures[0]];
      // Quality is part of the answer, not a footnote: an unknown result must
      // not be shown as the zero the row happens to carry.
      return <ResultValue value={typeof value === "object" ? scalarValue(rows[0], measures[0]) : value}
        quality={rows[0]?.quality} />;
    }
    return <div className="metric-grid">{measures.map((measure) =>
      <MetricCard key={measure} label={measure.replaceAll("_", " ")} value={scalarValue(rows[0], measure)} />,
    )}</div>;
  }
  if (shape === "timeseries") {
    const timeKey = dimensions.includes("timestamp") ? "timestamp" : "t";
    const splitKey = dimensions.find((dimension) => dimension !== timeKey);
    const makePoint = (row) => ({ t: row[timeKey], count: row[measures[0]] ?? 0 });
    if (splitKey) {
      const groups = {};
      rows.forEach((row) => { (groups[String(row[splitKey] ?? "—")] ||= []).push(makePoint(row)); });
      return <MultiLineChart series={Object.entries(groups).map(([label, points]) => ({ label, points }))}
        empty="No samples in this period." />;
    }
    return <MultiLineChart series={[{ label: measures[0].replaceAll("_", " "), points: rows.map(makePoint) }]}
      empty="No samples in this period." gapAfterSeconds={result.metadata?.display_gap_s ?? null} />;
  }
  const dimensionKey = dimensions[0];
  if (dimensionKey === "zone_id" && measures.length === 1) {
    return <BarChart rows={rows.map((row) => ({
      label: row.zone_name || `zone ${row.zone_id}`, value: row[measures[0]] || 0,
      detail: row.quality && row.quality !== "known" ? row.quality : undefined,
    }))} empty="No data for this grouping." />;
  }
  const columns = [
    ...dimensions.map((key) => ({ key, label: key.replaceAll("_", " ") })),
    ...measures.map((key) => ({ key, label: key.replaceAll("_", " ") })),
    ...(rows.some((row) => row.quality) ? [{ key: "quality", label: "quality" }] : []),
  ];
  return <DataTable columns={columns} rows={rows} empty="No rows for this query." />;
}

export function AnalysisCard({ definition, rangeSeconds, context, liveTick, resultOverride = null, tour = null }) {
  const { loading, data, error } = useQueryResult(definition, rangeSeconds, liveTick, !resultOverride);
  // The saved question is the useful subtitle; the internal subject is not.
  return <Panel tour={tour} title={definition.name} subtitle={definition.question || ""}>
    <QueryBody definition={definition} context={context} result={resultOverride || data}
      loading={resultOverride ? false : loading} error={resultOverride ? null : error} />
  </Panel>;
}
