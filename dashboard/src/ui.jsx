/* Shared presentation primitives.
 *
 * Every page used to invent its own header shape, empty state and status chip,
 * which is how one camera ended up described three different ways. These are
 * the only components pages should reach for when showing state.
 */
import { useEffect, useId, useRef, useState } from "react";
import {
  AlertTriangle, Check, ChevronDown, CircleDashed, CircleMinus, Info, MoreHorizontal, X,
} from "lucide-react";
import { TONE, resultQuality, resultValue } from "./status.js";

/* --------------------------------------------------------------- header */

/**
 * One page-header shape. `description` is optional and deliberately capped to a
 * single short sentence — pages that need to explain themselves at length are
 * usually pages that need simplifying.
 */
export function PageHeader({ title, description = "", actions = null, breadcrumb = null }) {
  return (
    <div className="page-header">
      <div className="page-header-text">
        {breadcrumb && <nav className="page-breadcrumb" aria-label="Breadcrumb">{breadcrumb}</nav>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

/** Tab strip for a page's sub-views (Review, Setup). */
export function SubNav({ items, active, onSelect, ariaLabel = "Sections" }) {
  return (
    <div className="sub-nav" role="tablist" aria-label={ariaLabel}>
      {items.map(([value, label, badge]) => (
        <button
          key={value}
          role="tab"
          aria-selected={active === value}
          className={active === value ? "active" : ""}
          data-demo-tour={`subnav-${value}`}
          onClick={() => onSelect(value)}
        >
          {label}
          {badge != null && badge !== 0 && <span className="sub-nav-badge">{badge}</span>}
        </button>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- status */

const TONE_ICON = {
  [TONE.good]: Check,
  [TONE.warn]: AlertTriangle,
  [TONE.bad]: AlertTriangle,
  [TONE.idle]: CircleMinus,
  [TONE.info]: Info,
};

/**
 * The single status chip. Never colour-only: it always carries an icon and the
 * word itself, and the optional explanation is exposed as a title tooltip.
 */
export function StatusPill({ status, compact = false }) {
  if (!status) return null;
  const Icon = TONE_ICON[status.tone] || CircleDashed;
  return (
    <span
      className={`status-pill tone-${status.tone} ${compact ? "compact" : ""}`}
      title={status.help || undefined}
    >
      <Icon size={compact ? 12 : 13} aria-hidden="true" />
      {status.label}
    </span>
  );
}

/**
 * A result with its confidence. An unknown result renders a dash — never the
 * underlying zero — because a confident zero and no evidence are different
 * answers to the same question.
 */
export function ResultValue({ value, quality, unit = "", size = "large" }) {
  const presentation = resultQuality(quality);
  return (
    <div className={`result-value result-${size}`}>
      <strong className={presentation.hasValue ? "" : "is-unknown"}>
        {resultValue(value, quality)}
        {presentation.hasValue && unit ? <span className="result-unit">{unit}</span> : null}
      </strong>
      <StatusPill status={presentation} compact />
    </div>
  );
}

/* ---------------------------------------------------------- empty states */

/**
 * Empty states come in three flavours and must not look alike: "nothing has
 * been set up yet" is not a failure, and neither is "no rows match a filter".
 */
export function EmptyState({ title, children, action = null, tone = "empty", icon = null }) {
  const Icon = icon || (tone === "error" ? AlertTriangle : tone === "no-data" ? CircleDashed : Info);
  return (
    <div className={`empty-state empty-${tone}`} role={tone === "error" ? "alert" : undefined}>
      <span className="empty-icon"><Icon size={18} aria-hidden="true" /></span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }) {
  return (
    <div className="loading-state" role="status" aria-live="polite">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function ErrorState({ error, retry = null, title = "Something went wrong" }) {
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={20} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{error?.message || String(error)}</p>
      </div>
      {retry && <button className="button button-secondary" onClick={retry}>Try again</button>}
    </div>
  );
}

/* --------------------------------------------------- technical disclosure */

/**
 * The one place raw identifiers, payloads and internal vocabulary belong.
 * Collapsed by default so a normal user never meets a UUID by accident.
 */
export function TechnicalDetails({ summary = "Technical details", children }) {
  return (
    <details className="technical-details">
      <summary>{summary}</summary>
      <div className="technical-body">{children}</div>
    </details>
  );
}

export function DefinitionList({ rows }) {
  return (
    <dl className="definition-rows">
      {rows.filter(Boolean).map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/* --------------------------------------------------------- overflow menu */

/** Destructive and rare actions live here rather than beside a primary button. */
export function OverflowMenu({ items, label = "More actions" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => { if (!ref.current?.contains(event.target)) setOpen(false); };
    const onKey = (event) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const usable = items.filter(Boolean);
  if (!usable.length) return null;
  return (
    <div className="overflow-menu" ref={ref}>
      <button
        className="icon-button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div className="overflow-items" role="menu">
          {usable.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              className={item.destructive ? "destructive" : ""}
              onClick={() => { setOpen(false); item.onSelect(); }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- panels */

export function Panel({ title, subtitle = "", action = null, className = "", tour = null, children }) {
  return (
    <section className={`panel ${className}`} data-demo-tour={tour || undefined}>
      {(title || action) && (
        <div className="panel-heading">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/* ---------------------------------------------------------------- dialogs */

export function Modal({ title, children, onClose, footer = null, wide = false, description = "" }) {
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
        const first = focusable[0];
        const last = focusable.at(-1);
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
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section
        ref={dialogRef}
        className={`modal ${wide ? "modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header>
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p className="modal-description">{description}</p>}
          </div>
          <button ref={closeRef} className="icon-button" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </section>
    </div>
  );
}

/** Side drawer for record detail. Same focus contract as Modal. */
export function Drawer({ title, eyebrow = "", children, onClose, footer = null }) {
  const titleId = useId();
  const closeRef = useRef(null);
  const drawerRef = useRef(null);
  const closeAction = useRef(onClose);
  closeAction.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement;
    closeRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") closeAction.current();
      if (event.key === "Tab") {
        const focusable = [
          ...(drawerRef.current?.querySelectorAll(
            'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ) || []),
        ];
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable.at(-1);
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
      className="drawer-backdrop"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <aside ref={drawerRef} className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header>
          <div>
            {eyebrow && <span className="tiny-label">{eyebrow}</span>}
            <h2 id={titleId}>{title}</h2>
          </div>
          <button ref={closeRef} className="icon-button" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </aside>
    </div>
  );
}

/* ----------------------------------------------------------------- misc */

export function Toast({ toast, dismiss }) {
  if (!toast) return null;
  return (
    <div
      className={`toast toast-${toast.tone || "neutral"}`}
      role={toast.tone === "error" ? "alert" : "status"}
      aria-live={toast.tone === "error" ? "assertive" : "polite"}
    >
      <span>{toast.tone === "error" ? <AlertTriangle size={16} /> : <Check size={16} />}</span>
      <div>
        <strong>{toast.title}</strong>
        {toast.message && <small>{toast.message}</small>}
      </div>
      <button className="icon-button" onClick={dismiss} aria-label="Dismiss notification">
        <X size={14} />
      </button>
    </div>
  );
}

export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <i className="orbit orbit-a" />
      <i className="orbit orbit-b" />
      <i className="brand-core" />
    </span>
  );
}

/** Short contextual help, used instead of permanent explanatory paragraphs. */
export function HelpHint({ children, label = "What does this mean?" }) {
  return (
    <span className="help-hint" title={children} aria-label={`${label} ${children}`}>
      <Info size={13} aria-hidden="true" />
    </span>
  );
}

export function Collapsible({ title, children, defaultOpen = false, summaryExtra = null }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`collapsible ${open ? "open" : ""}`}>
      <button className="collapsible-summary" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <ChevronDown size={15} aria-hidden="true" />
        <span>{title}</span>
        {summaryExtra}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </section>
  );
}
