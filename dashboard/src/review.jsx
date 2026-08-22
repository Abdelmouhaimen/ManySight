/* Review — what needs attention.
 *
 * Two sub-views: fired Alerts and the Alert rules that produce them. Rules used
 * to live under Setup › Thresholds, three words away from the alerts they
 * create; they belong beside them.
 */
import { useEffect, useMemo, useState } from "react";
import { Plus, RefreshCw, Save } from "lucide-react";
import { api, formatDateTime, formatTime } from "./api.js";
import { RULE_KINDS, alertFacts, alertQuality, describeRule, ruleScope } from "./alerts.js";
import { ALERT_REVIEW_STATES, alertStatus, isOpenAlert, resultQuality, ruleStatus } from "./status.js";
import {
  DefinitionList, Drawer, EmptyState, ErrorState, LoadingState, Modal, OverflowMenu, PageHeader,
  Panel, StatusPill, SubNav, TechnicalDetails,
} from "./ui.jsx";

const ALERT_FILTERS = [
  ["open", "Open", (alert) => isOpenAlert(alert)],
  ["resolved", "Resolved", (alert) => alertStatus(alert).value === "resolved"],
  ["dismissed", "Dismissed", (alert) => alertStatus(alert).value === "dismissed"],
  ["all", "All", () => true],
];

export function ReviewPage({ liveTick = 0, subview = "alerts", initialAlert, clearInitial, notify }) {
  const [context, setContext] = useState({ zones: [], sources: [], queries: [] });
  useEffect(() => {
    Promise.all([api.get("/zones"), api.get("/sources"), api.get("/queries")])
      .then(([zones, sources, queries]) => setContext({ zones, sources, queries }))
      .catch(() => {});
  }, []);
  const [alerts, setAlerts] = useState([]);
  const loadAlerts = () => api.get("/alerts?limit=200").then(setAlerts).catch(() => {});
  useEffect(() => { loadAlerts(); }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps
  const openCount = alerts.filter(isOpenAlert).length;

  const go = (value) => { window.location.hash = `review/${value}`; };

  return (
    <>
      <PageHeader title="Review" />
      <SubNav
        ariaLabel="Review sections"
        items={[["alerts", "Alerts", openCount], ["rules", "Rules", null]]}
        active={subview}
        onSelect={go}
      />
      {subview === "rules"
        ? <RulesView context={context} notify={notify} />
        : (
          <AlertsView
            alerts={alerts}
            reload={loadAlerts}
            context={context}
            initialAlert={initialAlert}
            clearInitial={clearInitial}
          />
        )}
    </>
  );
}

/* ---------------------------------------------------------------- alerts */

function AlertsView({ alerts, reload, context, initialAlert, clearInitial }) {
  const [filter, setFilter] = useState("open");
  const [selected, setSelected] = useState(initialAlert || null);
  const [loading, setLoading] = useState(!alerts.length);
  const [error, setError] = useState(null);

  useEffect(() => { if (initialAlert) setSelected(initialAlert); }, [initialAlert]);
  useEffect(() => { if (alerts.length) setLoading(false); }, [alerts.length]);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try { await reload(); } catch (err) { setError(err); } finally { setLoading(false); }
  };
  const counts = Object.fromEntries(
    ALERT_FILTERS.map(([value, , match]) => [value, alerts.filter(match).length]),
  );
  const visible = alerts.filter(ALERT_FILTERS.find(([value]) => value === filter)[2]);

  const save = async (status, note) => {
    const saved = await api.put(`/alerts/${selected.id}`, { status, note });
    setSelected(saved);
    await reload();
  };

  return (
    <>
      <div className="filter-row">
        {ALERT_FILTERS.map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {label}
            <span>{counts[value]}</span>
          </button>
        ))}
        <button className="button button-secondary filter-row-action" onClick={refresh}>
          <RefreshCw size={15} aria-hidden="true" /> Refresh
        </button>
      </div>
      {error ? <ErrorState error={error} retry={refresh} /> : loading ? (
        <LoadingState label="Loading alerts…" />
      ) : (
        <Panel title="Alerts" subtitle={visible.length === 1 ? "1 alert" : `${visible.length} alerts`}>
          <div className="alert-list">
            {visible.map((alert) => {
              const status = alertStatus(alert);
              return (
                <button key={alert.id} className="alert-row" onClick={() => setSelected(alert)}>
                  <time dateTime={new Date(alert.ts * 1000).toISOString()}>{formatTime(alert.ts)}</time>
                  <span className="alert-row-copy">
                    <strong>{alert.title}</strong>
                    {/* A rule is often named after what it reports, so only say
                        it twice when the two actually differ. */}
                    {!alert.rule_name
                      ? <small>Rule removed</small>
                      : alert.rule_name !== alert.title && <small>{alert.rule_name}</small>}
                  </span>
                  <StatusPill status={status} compact />
                </button>
              );
            })}
            {!visible.length && (
              <EmptyState
                tone={filter === "open" ? "empty" : "no-data"}
                title={filter === "open" ? "No open alerts" : "Nothing here"}
              >
                {filter === "open"
                  ? "Alerts that need attention will appear here."
                  : "No alerts match this filter."}
              </EmptyState>
            )}
          </div>
        </Panel>
      )}
      {selected && (
        <AlertDrawer
          alert={selected}
          context={context}
          onClose={() => { setSelected(null); clearInitial?.(); }}
          onSave={save}
        />
      )}
    </>
  );
}

function AlertDrawer({ alert, context, onClose, onSave }) {
  const [status, setStatus] = useState(alertStatus(alert).value);
  const [note, setNote] = useState(alert.note || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const facts = alertFacts(alert, context);
  const quality = alertQuality(alert);

  const save = async () => {
    setSaving(true);
    setError("");
    try { await onSave(status, note); } catch (err) { setError(err.message); } finally { setSaving(false); }
  };

  return (
    <Drawer
      title={alert.title}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-primary" disabled={saving} onClick={save}>
            <Save size={15} aria-hidden="true" /> {saving ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <StatusPill status={alertStatus({ status })} />
      <DefinitionList
        rows={[
          ...facts,
          ["Rule", alert.rule_name || "Rule removed"],
          ["Observed at", formatDateTime(alert.ts)],
          quality ? ["Quality", <StatusPill key="q" status={resultQuality(quality)} compact />] : null,
        ]}
      />
      <label className="field">
        <span>Review status</span>
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          {ALERT_REVIEW_STATES.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Note</span>
        <textarea
          rows="4"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="What did you check?"
        />
      </label>
      {error && <div className="form-error" role="alert">{error}</div>}
      <TechnicalDetails>
        <DefinitionList rows={[["Alert ID", `#${alert.id}`], ["Rule ID", alert.rule_id ?? "—"]]} />
        <p className="technical-note">{alert.message}</p>
        <pre>{JSON.stringify(alert.payload || {}, null, 2)}</pre>
      </TechnicalDetails>
    </Drawer>
  );
}

/* ----------------------------------------------------------------- rules */

function RulesView({ context, notify }) {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const load = async () => {
    try {
      setRules(await api.get("/alert-rules"));
      setError(null);
    } catch (err) { setError(err); } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (rule) => {
    try {
      await api.put(`/alert-rules/${rule.id}`, { enabled: !rule.enabled });
      await load();
    } catch (err) { notify?.("Couldn't update the rule", err.message, "error"); }
  };
  const remove = async () => {
    if (!deleting) return;
    const rule = deleting;
    setDeleteBusy(true);
    try {
      await api.del(`/alert-rules/${rule.id}`);
      setDeleting(null);
      await load();
      notify?.("Rule deleted", rule.name);
    } catch (err) {
      notify?.("Couldn't delete the rule", err.message, "error");
    } finally {
      setDeleteBusy(false);
    }
  };

  if (loading) return <LoadingState label="Loading rules…" />;
  if (error) return <ErrorState error={error} retry={load} />;
  return (
    <>
      <Panel
        title="Alert rules"
        action={
          <button className="button button-primary" onClick={() => setCreating(true)}>
            <Plus size={15} aria-hidden="true" /> New alert rule
          </button>
        }
      >
        <div className="rule-list">
          {rules.map((rule) => (
            <div className="rule-row" key={rule.id}>
              <div className="rule-copy">
                <strong>{rule.name}</strong>
                <small>{describeRule(rule, context)}</small>
                {ruleScope(rule, context) && <small className="rule-scope">{ruleScope(rule, context)}</small>}
              </div>
              <StatusPill status={ruleStatus(rule)} compact />
              <OverflowMenu
                label={`Actions for ${rule.name}`}
                items={[
                  { label: rule.enabled ? "Pause" : "Enable", onSelect: () => toggle(rule) },
                  { label: "Delete", destructive: true, onSelect: () => setDeleting(rule) },
                ]}
              />
            </div>
          ))}
          {!rules.length && (
            <EmptyState
              title="No alert rules yet"
              action={
                <button className="button button-primary" onClick={() => setCreating(true)}>
                  New alert rule
                </button>
              }
            >
              A rule watches for one condition and creates an alert when it is met.
            </EmptyState>
          )}
        </div>
      </Panel>
      {creating && (
        <RuleModal
          context={context}
          onClose={() => setCreating(false)}
          onSaved={async () => {
            setCreating(false);
            await load();
            notify?.("Rule created", "New alerts will appear in Review.");
          }}
        />
      )}
      {deleting && (
        <Modal
          title="Delete alert rule?"
          description="Alerts already created by this rule stay in Review."
          onClose={() => { if (!deleteBusy) setDeleting(null); }}
          footer={
            <>
              <button
                className="button button-secondary"
                disabled={deleteBusy}
                onClick={() => setDeleting(null)}
              >
                Cancel
              </button>
              <button className="button danger" disabled={deleteBusy} onClick={remove}>
                {deleteBusy ? "Deleting…" : "Delete rule"}
              </button>
            </>
          }
        >
          <p>Delete <strong>{deleting.name}</strong>?</p>
        </Modal>
      )}
    </>
  );
}

function RuleModal({ context, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: "", kind: "query_condition", query_id: "", operator: ">", value: 2,
    zone_id: "", source_id: "", state_name: "door_state", state_label: "open",
    seconds: 60, count: 5, window: 60, cooldown: 60, webhook_url: "", allow_partial: false,
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (patch) => setForm((current) => ({ ...current, ...patch }));
  const kindHelp = useMemo(
    () => RULE_KINDS.find(([value]) => value === form.kind)?.[2] || "", [form.kind],
  );

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.name.trim()) throw new Error("Give the rule a name.");
      const body = {
        name: form.name.trim(), kind: form.kind, params: {}, condition: null,
        cooldown_s: Number(form.cooldown) || 0, webhook_url: form.webhook_url.trim(),
      };
      if (form.kind === "query_condition") {
        if (!form.query_id) throw new Error("Choose the saved result to watch.");
        body.params = { query_id: Number(form.query_id) };
        body.condition = {
          operator: form.operator, value: Number(form.value),
          for_seconds: 0, allow_partial: form.allow_partial,
        };
      } else if (form.kind === "occupancy_exceeds") {
        body.params = { count: Number(form.count), window_s: Number(form.window) };
        if (form.zone_id) body.params.zone_id = Number(form.zone_id);
      } else if (form.kind === "dwell_exceeds") {
        body.params = { seconds: Number(form.seconds) };
        if (form.zone_id) body.params.zone_id = Number(form.zone_id);
      } else {
        body.params = { name: form.state_name, label: form.state_label,
                        min_seconds: Number(form.seconds) };
        if (form.source_id) body.params.source_id = Number(form.source_id);
      }
      await api.post("/alert-rules", body);
      await onSaved();
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  };

  return (
    <Modal
      title="New alert rule"
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-primary" disabled={saving} onClick={save}>
            {saving ? "Creating…" : "Create rule"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Name</span>
          <input
            autoFocus
            value={form.name}
            onChange={(event) => set({ name: event.target.value })}
            placeholder="Too many people in Aisle 04"
          />
        </label>
        <label className="field field-full">
          <span>Watch for</span>
          <select value={form.kind} onChange={(event) => set({ kind: event.target.value })}>
            {RULE_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          {kindHelp && <small>{kindHelp}</small>}
        </label>

        {form.kind === "query_condition" && (
          <>
            <label className="field field-full">
              <span>Saved result</span>
              <select value={form.query_id} onChange={(event) => set({ query_id: event.target.value })}>
                <option value="">Choose a saved result…</option>
                {context.queries.map((query) => (
                  <option key={query.id} value={query.id}>{query.question || query.name}</option>
                ))}
              </select>
              {!context.queries.length && (
                <small>No saved results exist yet in this workspace.</small>
              )}
            </label>
            <label className="field">
              <span>When the value is</span>
              <select value={form.operator} onChange={(event) => set({ operator: event.target.value })}>
                {/* Wording matches the operator exactly — "more than 2" is not "at least 2". */}
                <option value=">">more than</option>
                <option value=">=">at least</option>
                <option value="<">fewer than</option>
                <option value="<=">at most</option>
                <option value="==">exactly</option>
              </select>
            </label>
            <label className="field">
              <span>Value</span>
              <input
                type="number"
                value={form.value}
                onChange={(event) => set({ value: event.target.value })}
              />
            </label>
            <label className="check-field field-full">
              <input
                type="checkbox"
                checked={form.allow_partial}
                onChange={(event) => set({ allow_partial: event.target.checked })}
              />
              Also alert when only some cameras are reporting
            </label>
          </>
        )}

        {(form.kind === "occupancy_exceeds" || form.kind === "dwell_exceeds") && (
          <label className="field">
            <span>Zone</span>
            <select value={form.zone_id} onChange={(event) => set({ zone_id: event.target.value })}>
              <option value="">Any zone</option>
              {context.zones.map((zone) => <option key={zone.id} value={zone.id}>{zone.name}</option>)}
            </select>
          </label>
        )}
        {form.kind === "occupancy_exceeds" && (
          <>
            <label className="field">
              <span>More than (people)</span>
              <input type="number" value={form.count}
                     onChange={(event) => set({ count: event.target.value })} />
            </label>
            <label className="field">
              <span>Within (seconds)</span>
              <input type="number" value={form.window}
                     onChange={(event) => set({ window: event.target.value })} />
            </label>
          </>
        )}
        {form.kind === "dwell_exceeds" && (
          <label className="field">
            <span>Longer than (seconds)</span>
            <input type="number" value={form.seconds}
                   onChange={(event) => set({ seconds: event.target.value })} />
          </label>
        )}
        {form.kind === "state_alert" && (
          <>
            <label className="field">
              <span>Camera</span>
              <select value={form.source_id} onChange={(event) => set({ source_id: event.target.value })}>
                <option value="">Any camera</option>
                {context.sources.map((source) => (
                  <option key={source.id} value={source.id}>{source.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>State name</span>
              <input value={form.state_name}
                     onChange={(event) => set({ state_name: event.target.value })} />
            </label>
            <label className="field">
              <span>Stays at</span>
              <input value={form.state_label}
                     onChange={(event) => set({ state_label: event.target.value })} />
            </label>
            <label className="field">
              <span>For (seconds)</span>
              <input type="number" value={form.seconds}
                     onChange={(event) => set({ seconds: event.target.value })} />
            </label>
          </>
        )}

        <label className="field">
          <span>Wait between alerts (seconds)</span>
          <input type="number" min="0" value={form.cooldown}
                 onChange={(event) => set({ cooldown: event.target.value })} />
        </label>
        <label className="field">
          <span>Webhook URL (optional)</span>
          <input type="url" value={form.webhook_url}
                 onChange={(event) => set({ webhook_url: event.target.value })}
                 placeholder="https://automation.example/webhook" />
        </label>
      </div>
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}
