import { useEffect, useId, useRef } from "react";
import { AlertTriangle, ArrowRight, Check, X } from "lucide-react";
import { formatDuration, formatTime } from "./api.js";

export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <i className="orbit orbit-a" />
      <i className="orbit orbit-b" />
      <i className="brand-core" />
    </span>
  );
}

export function Badge({ tone = "neutral", children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function EnvironmentBadge({ value }) {
  const config = {
    demo: ["Example data", "violet"],
    live: ["Live pilot", "positive"],
    setup: ["Setup incomplete", "warning"],
  }[value] || ["Setup incomplete", "warning"];
  return (
    <Badge tone={config[1]}>
      <span className="badge-dot" />
      {config[0]}
    </Badge>
  );
}

export function PageHeader({ eyebrow, title, description, actions }) {
  return (
    <div className="page-header">
      <div>
        <span className="tiny-label">{eyebrow}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function MetricCard({ label, value, note, primary = false, tone = "" }) {
  return (
    <article
      className={`metric-card ${primary ? "metric-primary" : ""} ${tone ? `metric-${tone}` : ""}`}
    >
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}

export function Panel({ title, subtitle, action, className = "", children }) {
  return (
    <article className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </article>
  );
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty-state">
      <span>
        <AlertTriangle size={18} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}

export function LoadingState({ label = "Loading workspace…" }) {
  return (
    <div className="loading-state">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function ErrorState({ error, retry }) {
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={20} />
      <div>
        <strong>Unable to load this view</strong>
        <p>{error?.message || String(error)}</p>
      </div>
      {retry && (
        <button className="button button-secondary" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Modal({ title, children, onClose, footer, wide = false }) {
  const titleId = useId();
  const closeRef = useRef(null);
  const dialogRef = useRef(null);
  const closeAction = useRef(onClose);
  closeAction.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement;
    closeRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") closeAction.current();
      if (event.key === "Tab") {
        const focusable = [
          ...(dialogRef.current?.querySelectorAll(
            'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ) || []),
        ];
        if (!focusable.length) return;
        const first = focusable[0],
          last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus?.();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className={`modal ${wide ? "modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button
            ref={closeRef}
            className="icon-button"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </section>
    </div>
  );
}

export function Toast({ toast, dismiss }) {
  if (!toast) return null;
  return (
    <div
      className={`toast toast-${toast.tone || "neutral"}`}
      role={toast.tone === "error" ? "alert" : "status"}
      aria-live={toast.tone === "error" ? "assertive" : "polite"}
    >
      <span>
        {toast.tone === "error" ? (
          <AlertTriangle size={16} />
        ) : (
          <Check size={16} />
        )}
      </span>
      <div>
        <strong>{toast.title}</strong>
        {toast.message && <small>{toast.message}</small>}
      </div>
      <button
        className="icon-button"
        onClick={dismiss}
        aria-label="Dismiss notification"
      >
        <X size={14} />
      </button>
    </div>
  );
}

function niceMax(value) {
  if (value <= 1) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / power) * power;
}

export function LineChart({
  points = [],
  unit = "",
  empty = "No observations in this period",
}) {
  if (!points.length)
    return <EmptyState title="No chart data">{empty}</EmptyState>;
  const width = 680,
    height = 210,
    left = 38,
    right = 12,
    top = 15,
    bottom = 30;
  const t0 = points[0].t,
    t1 = points.at(-1).t || t0 + 1;
  const max = niceMax(
    Math.max(...points.map((point) => Number(point.count) || 0), 1),
  );
  const x = (t) => left + ((t - t0) / (t1 - t0 || 1)) * (width - left - right);
  const y = (value) => top + (1 - value / max) * (height - top - bottom);
  const path = points
    .map(
      (point, index) =>
        `${index ? "L" : "M"} ${x(point.t).toFixed(1)} ${y(point.count).toFixed(1)}`,
    )
    .join(" ");
  const area = `${path} L ${x(t1)} ${y(0)} L ${x(t0)} ${y(0)} Z`;
  return (
    <div className="chart-wrap">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="line-chart"
        role="img"
        aria-label={`Time series ending at ${points.at(-1).count}${unit}`}
      >
        <defs>
          <linearGradient id="line-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#7059ff" stopOpacity=".28" />
            <stop offset="1" stopColor="#7059ff" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
          <g key={ratio}>
            <line
              x1={left}
              x2={width - right}
              y1={y(max * ratio)}
              y2={y(max * ratio)}
              className="chart-grid"
            />
            <text x={left - 8} y={y(max * ratio) + 3} textAnchor="end">
              {Math.round(max * ratio)}
            </text>
          </g>
        ))}
        <path d={area} fill="url(#line-fill)" />
        <path d={path} className="chart-line" />
        <circle
          cx={x(points.at(-1).t)}
          cy={y(points.at(-1).count)}
          r="4.5"
          className="chart-dot"
        />
        {[0, 0.33, 0.66, 1].map((ratio) => {
          const ts = t0 + (t1 - t0) * ratio;
          return (
            <text
              key={ratio}
              x={x(ts)}
              y={height - 8}
              textAnchor={
                ratio === 0 ? "start" : ratio === 1 ? "end" : "middle"
              }
            >
              {formatTime(ts)}
            </text>
          );
        })}
      </svg>
      <div className="chart-summary">
        <span>Latest</span>
        <strong>
          {points.at(-1).count}
          {unit}
        </strong>
      </div>
    </div>
  );
}

const SERIES_COLORS = [
  "#7059ff",
  "#0f8b8d",
  "#e07a35",
  "#d14978",
  "#4878cf",
  "#6f9e43",
];

export function MultiLineChart({
  series = [],
  unit = "",
  empty = "No observations in this period",
}) {
  const visible = series.filter((item) => item.points?.length);
  if (!visible.length)
    return <EmptyState title="No chart data">{empty}</EmptyState>;
  const width = 680,
    height = 210,
    left = 38,
    right = 12,
    top = 15,
    bottom = 30;
  const allPoints = visible.flatMap((item) => item.points);
  const t0 = Math.min(...allPoints.map((point) => point.t));
  const t1 = Math.max(...allPoints.map((point) => point.t), t0 + 1);
  const max = niceMax(
    Math.max(...allPoints.map((point) => Number(point.count) || 0), 1),
  );
  const x = (t) => left + ((t - t0) / (t1 - t0 || 1)) * (width - left - right);
  const y = (value) => top + (1 - value / max) * (height - top - bottom);
  return (
    <div className="chart-wrap multi-line-wrap">
      <div className="chart-legend" aria-label="Chart series">
        {visible.map((item, index) => (
          <span key={item.label}>
            <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
            {item.label}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="line-chart"
        role="img"
        aria-label={`Time series comparing ${visible.map((item) => item.label).join(", ")}`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
          <g key={ratio}>
            <line
              x1={left}
              x2={width - right}
              y1={y(max * ratio)}
              y2={y(max * ratio)}
              className="chart-grid"
            />
            <text x={left - 8} y={y(max * ratio) + 3} textAnchor="end">
              {Math.round(max * ratio)}
            </text>
          </g>
        ))}
        {visible.map((item, index) => {
          const color = SERIES_COLORS[index % SERIES_COLORS.length];
          const path = item.points
            .map(
              (point, pointIndex) =>
                `${pointIndex ? "L" : "M"} ${x(point.t).toFixed(1)} ${y(point.count).toFixed(1)}`,
            )
            .join(" ");
          const latest = item.points.at(-1);
          return (
            <g key={item.label}>
              <path d={path} className="chart-line" style={{ stroke: color }} />
              <circle
                cx={x(latest.t)}
                cy={y(latest.count)}
                r="4"
                className="chart-dot"
                style={{ stroke: color }}
              />
            </g>
          );
        })}
        {[0, 0.33, 0.66, 1].map((ratio) => {
          const ts = t0 + (t1 - t0) * ratio;
          return (
            <text
              key={ratio}
              x={x(ts)}
              y={height - 8}
              textAnchor={ratio === 0 ? "start" : ratio === 1 ? "end" : "middle"}
            >
              {formatTime(ts)}
            </text>
          );
        })}
      </svg>
      <div className="multi-line-latest">
        {visible.map((item, index) => (
          <span key={item.label}>
            <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
            {item.label}: <strong>{item.points.at(-1).count}{unit}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

export function BarChart({
  rows = [],
  valueKey = "value",
  labelKey = "label",
  unit = "",
  empty = "No values available",
}) {
  if (!rows.length)
    return <EmptyState title="No comparison data">{empty}</EmptyState>;
  const max = Math.max(...rows.map((row) => Number(row[valueKey]) || 0), 1);
  return (
    <div className="bar-list">
      {rows.map((row) => (
        <div className="bar-row" key={row[labelKey]}>
          <div className="bar-label">
            <span>{row[labelKey]}</span>
            <strong>
              {Math.round(row[valueKey] * 10) / 10}
              {unit}
            </strong>
          </div>
          <div className="bar-track">
            <i
              style={{ width: `${Math.max(2, (row[valueKey] / max) * 100)}%` }}
            />
          </div>
          {row.detail && <small>{row.detail}</small>}
        </div>
      ))}
    </div>
  );
}

export function ActivityMap({
  store,
  zones = [],
  sources = [],
  points = [],
  compact = false,
}) {
  const width = Number(store?.width_m) || 20,
    height = Number(store?.height_m) || 12;
  const topPoints = [...points].slice(0, compact ? 90 : 260);
  const maxWeight = Math.max(...topPoints.map((point) => point.w || 1), 1);
  const polygon = (items) =>
    items.map((point) => `${point.x},${point.y}`).join(" ");
  return (
    <div className={`activity-map ${compact ? "activity-map-compact" : ""}`}>
      <svg
        viewBox={`-.6 -.6 ${width + 1.2} ${height + 1.2}`}
        role="img"
        aria-label="Store activity map"
      >
        <defs>
          <filter id="heat-blur">
            <feGaussianBlur stdDeviation=".35" />
          </filter>
        </defs>
        <rect x="0" y="0" width={width} height={height} className="map-floor" />
        {zones.map((zone) => (
          <g key={zone.id}>
            <polygon
              points={polygon(zone.polygon)}
              fill={`${zone.color}20`}
              stroke={`${zone.color}90`}
              strokeWidth=".05"
            />
            <text
              x={
                zone.polygon.reduce((sum, p) => sum + p.x, 0) /
                zone.polygon.length
              }
              y={
                zone.polygon.reduce((sum, p) => sum + p.y, 0) /
                zone.polygon.length
              }
              className="map-zone-label"
            >
              {zone.name}
            </text>
          </g>
        ))}
        {(store?.map?.walls || []).map((wall, index) => (
          <polyline key={index} points={polygon(wall)} className="map-wall" />
        ))}
        <g filter="url(#heat-blur)">
          {topPoints.map((point, index) => (
            <circle
              key={index}
              cx={point.x}
              cy={point.y}
              r={0.35 + (point.w / maxWeight) * 0.75}
              fill={
                index < topPoints.length * 0.15
                  ? "#ff8a4c"
                  : index < topPoints.length * 0.45
                    ? "#ccff43"
                    : "#7059ff"
              }
              opacity={0.18 + (point.w / maxWeight) * 0.45}
            />
          ))}
        </g>
        {sources
          .filter((source) => source.placement)
          .map((source) => (
            <g key={source.id}>
              <circle
                cx={source.placement.x}
                cy={source.placement.y}
                r=".14"
                className="map-camera"
              />
              <text
                x={source.placement.x}
                y={source.placement.y - 0.25}
                className="map-camera-label"
              >
                {source.name}
              </text>
            </g>
          ))}
      </svg>
      <div className="map-legend">
        <span />
        <small>More activity</small>
      </div>
    </div>
  );
}

export const RANGE_OPTIONS = [
  ["1 hour", 3600],
  ["6 hours", 21600],
  ["24 hours", 86400],
  ["7 days", 604800],
  ["30 days", 2592000],
];

export function RangeSelect({ value, onChange }) {
  return (
    <label className="select-control">
      <span className="sr-only">Time range</span>
      <select
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {RANGE_OPTIONS.map(([label, seconds]) => (
          <option key={seconds} value={seconds}>
            Last {label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function FlowTable({ data }) {
  if (!data.links.length)
    return (
      <EmptyState title="No transitions yet">
        Stable track IDs need zone-enter sequences or zoned detections.
      </EmptyState>
    );
  const names = [
    ...new Set(
      data.links
        .flatMap((link) => [link.from_name, link.to_name])
        .filter(Boolean),
    ),
  ];
  const max = Math.max(...data.links.map((link) => link.count), 1);
  return (
    <div className="table-scroll">
      <table className="matrix-table">
        <thead>
          <tr>
            <th>From / to</th>
            {names.map((name) => (
              <th key={name}>{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {names.map((from) => (
            <tr key={from}>
              <th>{from}</th>
              {names.map((to) => {
                const value =
                  data.links.find(
                    (link) => link.from_name === from && link.to_name === to,
                  )?.count || 0;
                return (
                  <td
                    key={to}
                    style={{
                      backgroundColor: value
                        ? `rgba(112,89,255,${0.08 + (0.5 * value) / max})`
                        : undefined,
                    }}
                  >
                    {value || "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StateSummary({ series }) {
  if (!series.length)
    return (
      <EmptyState title="No state history">
        State-change events will create equipment or scene timelines.
      </EmptyState>
    );
  return (
    <div className="state-list">
      {series.map((item) => (
        <div key={item.source_id}>
          <strong>{item.source_name}</strong>
          <div>
            {Object.entries(item.totals).map(([label, seconds]) => (
              <Badge
                key={label}
                tone={
                  label === "open" || label === "on" ? "warning" : "neutral"
                }
              >
                {label} · {formatDuration(seconds)}
              </Badge>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DataTable({ columns, rows, empty = "No rows to display" }) {
  if (!rows.length) return <EmptyState title="No table data">{empty}</EmptyState>;
  return (
    <div className="raw-event-table table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key}>
                  {column.format
                    ? column.format(row[column.key], row)
                    : (row[column.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SignalRow({ signal, onClick }) {
  const tone =
    signal.status === "new"
      ? "warning"
      : signal.status === "in_review"
        ? "violet"
        : "positive";
  return (
    <button className="signal-row" onClick={onClick}>
      <span className={`signal-icon signal-${tone}`}>
        <span />
      </span>
      <time>{formatTime(signal.ts)}</time>
      <span className="signal-copy">
        <strong>{signal.title}</strong>
        <small>{signal.message}</small>
      </span>
      <Badge tone={tone}>{(signal.status || "new").replace("_", " ")}</Badge>
      <ArrowRight size={15} />
    </button>
  );
}
