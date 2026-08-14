import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Circle, Compass, X,
} from "lucide-react";
import { api, demoSessionId } from "./api.js";
import {
  advanceTour, applyTourAction, evaluateTour, initialTourState, observeDemoState,
  reportTourTargetMissing, restoreTourState, retryTourStep, serializeTourState,
  skipTourStep, tourCardView, tourStepById,
} from "./demo-tour-model.js";
import {
  blockerRects, cardPlacement, isRectVisible, maskPath, resolveTourTarget,
  spotlightGeometry, targetAttemptDelay, targetAttemptsExhausted,
} from "./demo-tour-spotlight.js";

const TOUR_EVENT = "storelens-tour-event";
const STORAGE_KEY = "storelens.demo-tour";
const TICK_MS = 120;
const NARROW_CARD_WIDTH = 236;
const CARD_GUTTER = 16;

/** Report a real StoreLens UI completion to the guided tour. */
export function reportTourEvent(detail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(TOUR_EVENT, { detail }));
}

export function onTourEvent(handler) {
  const listener = (event) => handler(event.detail || {});
  window.addEventListener(TOUR_EVENT, listener);
  return () => window.removeEventListener(TOUR_EVENT, listener);
}

export function requestSetupTab(tab) {
  if (typeof window === "undefined") return;
  localStorage.setItem("storelens.setup.tab", tab);
  window.dispatchEvent(new CustomEvent("storelens-setup-tab", { detail: { tab } }));
}

function reduceEvents(current, detail) {
  switch (detail?.kind) {
    case "digitizer-open":
      return { ...current, digitizerOpen: true };
    case "digitizer-closed":
      return { ...current, digitizerOpen: false };
    case "digitizer-progress":
      return { ...current, digitizer: { ...detail } };
    case "plan-saved":
      return { ...current, digitizerOpen: false, planSavedAt: detail.at || Date.now() };
    case "plan-restored":
      return { ...current, planRestoredAt: detail.at || Date.now() };
    case "calibration-open":
      return { ...current, calibrationOpenFor: detail.cameraIndex ?? null };
    case "calibration-closed":
      return { ...current, calibrationOpenFor: null };
    case "calibration-progress":
      return { ...current, calibrationPairs: Number(detail.pairs || 0) };
    case "practice-calibration-restored":
      return { ...current, calibrationOpenFor: null, practiceCalibrationAt: detail.at || Date.now() };
    default:
      return current;
  }
}

function viewportRect() {
  return { width: window.innerWidth, height: window.innerHeight };
}

function elementRect(element) {
  const box = element.getBoundingClientRect();
  return { top: box.top, left: box.left, width: box.width, height: box.height };
}

async function loadWorkspace() {
  const [store, sources, zones, zoneViews, queries, rules, dashboards] = await Promise.all([
    api.get("/store"), api.get("/sources"), api.get("/zones"), api.get("/zone-views"),
    api.get("/queries"), api.get("/alert-rules"), api.get("/dashboards"),
  ]);
  return { store, sources, zones, zoneViews, queries, rules, dashboards };
}

/**
 * Guided-tour controller. It observes the real demo session, the real
 * demo-workspace objects, and the committed replay cache. The only demo work it
 * performs is restoring prepared demo geometry after the optional practice
 * detour, so both setup paths converge on the same validated state.
 */
export function useDemoTour({ session, cache, replay } = {}) {
  const sessionId = session?.mode === "guided" ? session.id : null;
  const [state, setState] = useState(null);
  const [events, setEvents] = useState({});
  const [workspace, setWorkspace] = useState({});
  const [targetRect, setTargetRect] = useState(null);
  const [tick, setTick] = useState(0);
  const [viewport, setViewport] = useState(() =>
    (typeof window === "undefined" ? { width: 0, height: 0 } : viewportRect()));
  const effectRun = useRef(new Set());
  const observedRef = useRef(null);

  useEffect(() => {
    if (!sessionId) { setState(null); setEvents({}); setWorkspace({}); return; }
    setState((current) => {
      if (current?.sessionId === sessionId) return current;
      const stored = restoreTourState(localStorage.getItem(STORAGE_KEY), sessionId, Date.now());
      return stored || initialTourState(sessionId, Date.now());
    });
  }, [sessionId]);

  useEffect(() => {
    if (!state?.sessionId) return;
    localStorage.setItem(STORAGE_KEY, serializeTourState(state));
  }, [state?.sessionId, state?.stepId, state?.branch, state?.minimized, state?.dismissed]);

  useEffect(() => onTourEvent((detail) => setEvents((current) => reduceEvents(current, detail))), []);

  const refreshWorkspace = useCallback(() => {
    if (!sessionId) return undefined;
    return loadWorkspace().then(setWorkspace).catch(() => {});
  }, [sessionId]);

  useEffect(() => { refreshWorkspace(); }, [refreshWorkspace]);
  useEffect(() => {
    if (!events.planSavedAt && !events.planRestoredAt && !events.practiceCalibrationAt) return;
    refreshWorkspace();
  }, [events.planSavedAt, events.planRestoredAt, events.practiceCalibrationAt, refreshWorkspace]);

  const observed = useMemo(
    () => observeDemoState({ session, cache, replay, workspace, events }),
    [session, cache, replay, workspace, events],
  );
  observedRef.current = observed;
  const step = state ? tourStepById(state.stepId) : null;
  const dismissed = Boolean(state?.dismissed);

  // One light tick drives presentation pacing and re-evaluation from observed
  // truth. It is intentionally independent of the replay master clock.
  useEffect(() => {
    if (!state || dismissed || !step || step.type === "terminal") return undefined;
    const timer = window.setInterval(() => {
      setTick((value) => value + 1);
      setState((current) => (current
        ? evaluateTour(current, observedRef.current, Date.now()) : current));
    }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [Boolean(state), dismissed, step?.id, step?.type]);

  // Steps declare the route they teach on; entering one navigates a single time.
  useEffect(() => {
    if (!step || dismissed || !step.route) return;
    if (step.setupTab) requestSetupTab(step.setupTab);
    if (window.location.hash.replace("#", "") !== step.route) window.location.hash = step.route;
  }, [step?.id, dismissed]);

  const restorePracticeSpace = useCallback(async () => {
    const id = demoSessionId();
    if (!id) return;
    try {
      const result = await api.post(`/demo/sessions/${id}/restore-practice-space`, {});
      reportTourEvent({ kind: "plan-restored", at: Date.now(), result });
    } catch (error) {
      console.warn("guided tour could not restore the prepared demo space", error);
      reportTourEvent({ kind: "plan-restored", at: Date.now() });
    }
  }, []);

  useEffect(() => {
    if (!step?.effect || dismissed || effectRun.current.has(step.id)) return;
    effectRun.current.add(step.id);
    if (step.effect === "restorePracticeSpace") restorePracticeSpace();
  }, [step?.id, step?.effect, dismissed, restorePracticeSpace]);

  // Target resolution: keep retrying while a route change or lazy view renders,
  // then report a safe fallback instead of advancing a required interaction.
  useEffect(() => {
    setTargetRect(null);
    if (!step?.target || dismissed) return undefined;
    let attempt = 0;
    let timer = 0;
    let observer = null;
    let cancelled = false;
    const measure = () => {
      const element = resolveTourTarget(step.target, document);
      if (!element) return false;
      setTargetRect(elementRect(element));
      if (!observer && typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(() => {
          const current = resolveTourTarget(step.target, document);
          if (current) setTargetRect(elementRect(current));
        });
        observer.observe(element);
      }
      return true;
    };
    const attemptResolve = () => {
      if (cancelled || measure()) return;
      attempt += 1;
      if (targetAttemptsExhausted(attempt)) {
        console.warn(`guided tour target "${step.target}" was not found for step "${step.id}"`);
        setState((current) => (current && current.stepId === step.id
          ? reportTourTargetMissing(current, step.fallback
            || "That control is not on screen yet. Open it in StoreLens to continue.")
          : current));
        return;
      }
      timer = window.setTimeout(attemptResolve, targetAttemptDelay(attempt));
    };
    attemptResolve();
    const track = () => { if (!cancelled) measure(); };
    const onResize = () => { setViewport(viewportRect()); track(); };
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", track, true);
    const follow = window.setInterval(track, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.clearInterval(follow);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", track, true);
      observer?.disconnect();
    };
  }, [step?.id, step?.target, dismissed]);

  useEffect(() => {
    const onResize = () => setViewport(viewportRect());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!step?.target || !targetRect || !viewport.height) return;
    if (isRectVisible(targetRect, viewport)) return;
    resolveTourTarget(step.target, document)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [step?.target, targetRect?.top, viewport.height]);

  return useMemo(() => {
    if (!state || !step) return null;
    const hole = step.target && step.dim !== false
      ? spotlightGeometry(targetRect, { viewport, padding: step.padding })
      : null;
    const spotlit = Boolean(hole) && !hole.offscreen;
    return {
      state, step, observed, viewport, hole, tick,
      view: tourCardView(state, observed, Math.max(0, Date.now() - state.enteredAt)),
      dim: step.dim !== false && !state.minimized && !dismissed,
      block: step.type === "user_action" && spotlit && !state.minimized && state.status !== "error",
      act: (actionId) => setState((current) => (current
        ? applyTourAction(current, actionId, Date.now()) : current)),
      skip: () => setState((current) => (current ? skipTourStep(current, Date.now()) : current)),
      retry: () => setState((current) => (current ? retryTourStep(current, Date.now()) : current)),
      advance: () => setState((current) => (current ? advanceTour(current, Date.now()) : current)),
      minimize: (value) => setState((current) => (current ? { ...current, minimized: value } : current)),
      dismiss: (value) => setState((current) => (current ? { ...current, dismissed: value } : current)),
    };
  }, [state, step, observed, viewport, targetRect, dismissed, tick]);
}

function StatusIcon({ status }) {
  if (status === "complete") return <CheckCircle2 size={14} aria-hidden="true" />;
  if (status === "error") return <AlertTriangle size={14} aria-hidden="true" />;
  if (status === "active" || status === "loading") return <span className="demo-tour-dot" aria-hidden="true" />;
  if (status === "waiting_for_user") return <Compass size={14} aria-hidden="true" />;
  return <Circle size={13} aria-hidden="true" />;
}

const STATUS_WORD = {
  pending: "pending",
  active: "in progress",
  loading: "in progress",
  waiting_for_user: "waiting for you",
  complete: "complete",
  error: "needs attention",
};

const EXPLORE_LINKS = [
  ["overview", "Dashboard"], ["live", "Live"], ["review", "Review"],
  ["sources", "Sources"], ["setup", "Setup"],
];

export function TourCard({ tour }) {
  const { state, view } = tour;
  const { checklist, progress, title, description, detail, hint, finished } = view;
  const minimized = state.minimized;
  return (
    <aside
      className={`demo-tour-card ${minimized ? "minimized" : ""} ${finished ? "finished" : ""}`}
      aria-label="Guided demo progress"
      data-demo-tour="tour-card"
    >
      <header>
        <span className="tiny-label">Guided demo · {progress.complete}/{progress.total}</span>
        <div className="demo-tour-card-controls">
          <button
            className="icon-button"
            onClick={() => tour.minimize(!minimized)}
            aria-label={minimized ? "Expand guided demo progress" : "Collapse guided demo progress"}
            aria-expanded={!minimized}
          >
            {minimized ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
          </button>
          <button
            className="icon-button"
            onClick={() => tour.dismiss(true)}
            aria-label="Hide guided demo progress"
          >
            <X size={15} />
          </button>
        </div>
      </header>
      <div className="demo-tour-step" role="status" aria-live="polite">
        <h2>{title}</h2>
        {!minimized && (
          <>
            {view.quote && <blockquote className="demo-tour-quote">“{view.quote}”</blockquote>}
            {description && <p>{description}</p>}
            {detail.length > 0 && (
              <ul className="demo-tour-detail">
                {detail.map((item) => (
                  <li key={item.label} className={`is-${item.status}`}>
                    <StatusIcon status={item.status} />
                    <span className="demo-tour-label">{item.label}</span>
                    <i className="sr-only">{STATUS_WORD[item.status]}</i>
                  </li>
                ))}
              </ul>
            )}
            {hint && <p className="demo-tour-hint">{hint}</p>}
            {view.error ? (
              <div className="demo-tour-problem">
                <AlertTriangle size={14} />
                <p>{view.error}</p>
                <div className="demo-tour-actions">
                  <button className="button button-secondary" onClick={tour.retry}>Look again</button>
                  <button className="button button-ghost" onClick={tour.skip}>Skip this step</button>
                </div>
              </div>
            ) : view.actions.length > 0 && (
              <div className="demo-tour-actions">
                {view.actions.map((action) => (
                  <button
                    key={action.id}
                    className={`button ${action.primary ? "button-dark" : "button-secondary"}`}
                    data-demo-tour={`tour-action-${action.id}`}
                    onClick={() => (action.id === "exit"
                      ? reportTourEvent({ kind: "request-exit" })
                      : tour.act(action.id))}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            )}
            {finished && (
              <div className="demo-tour-explore">
                {EXPLORE_LINKS.map(([route, label]) => (
                  <a key={route} className="button button-ghost" href={`#${route}`}>{label}</a>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      {!minimized && (
        <ol className="demo-tour-checklist">
          {checklist.map((row) => (
            <li key={row.id} className={`is-${row.status}`}>
              <StatusIcon status={row.status} />
              <span className="demo-tour-label">{row.label}</span>
              <i className="sr-only">{STATUS_WORD[row.status]}</i>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

/**
 * Dimming mask, spotlight ring, optional click blockers, and the progress card,
 * in one portal layer that sits below StoreLens modals and toasts so real
 * dialogs keep working without per-page z-index juggling.
 */
export function DemoTourLayer({ tour }) {
  const anchorRef = useRef(null);
  const [cardSize, setCardSize] = useState({ width: 328, height: 300 });
  const [chrome, setChrome] = useState({ dialog: null, sidebarRight: 0 });

  // A real StoreLens dialog is an obstacle for the card, never something the
  // tour covers or replaces, and the app's own navigation must stay clickable.
  // Watching the DOM keeps the card from sitting on either for a frame.
  useEffect(() => {
    const measure = () => {
      const dialog = document.querySelector(".modal-backdrop .modal");
      const sidebar = document.querySelector(".app-sidebar");
      const sidebarBox = sidebar?.getBoundingClientRect();
      const next = {
        dialog: dialog ? elementRect(dialog) : null,
        sidebarRight: sidebarBox && sidebarBox.right > 0 && sidebarBox.width > 0
          ? sidebarBox.right : 0,
      };
      setChrome((current) => {
        const same = current.sidebarRight === next.sidebarRight
          && Boolean(current.dialog) === Boolean(next.dialog)
          && (!current.dialog || (current.dialog.top === next.dialog.top
            && current.dialog.left === next.dialog.left
            && current.dialog.width === next.dialog.width
            && current.dialog.height === next.dialog.height));
        return same ? current : next;
      });
    };
    measure();
    const observer = new MutationObserver(measure);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, []);
  useEffect(() => {
    const dialog = document.querySelector(".modal-backdrop .modal");
    if (dialog) setChrome((current) => ({ ...current, dialog: elementRect(dialog) }));
  }, [tour?.tick]);

  useEffect(() => {
    const measure = () => {
      const box = anchorRef.current?.getBoundingClientRect();
      if (box?.width) setCardSize({ width: box.width, height: box.height });
    };
    measure();
    if (!anchorRef.current || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(anchorRef.current);
    return () => observer.disconnect();
  }, [tour?.step?.id, tour?.state?.minimized, tour?.state?.dismissed]);

  const minimize = tour?.minimize;
  const escapable = Boolean(tour?.dim || tour?.block);
  useEffect(() => {
    if (!minimize || !escapable) return undefined;
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      if (document.querySelector(".modal-backdrop")) return; // real dialogs own Escape
      minimize(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [minimize, escapable]);

  if (!tour) return null;
  if (tour.state.dismissed) {
    return createPortal(
      <button
        className="demo-tour-restore"
        onClick={() => tour.dismiss(false)}
        data-demo-tour="tour-restore"
      >
        <Compass size={14} /> Guided demo
      </button>,
      document.body,
    );
  }
  const { hole, viewport, dim, block } = tour;
  // A wide StoreLens dialog leaves only a narrow gutter, so the card slims down
  // rather than sitting on top of the controls the step is talking about.
  const beside = Boolean(chrome.dialog) && !hole;
  const placement = cardPlacement({
    card: beside ? { width: NARROW_CARD_WIDTH, height: cardSize.height } : cardSize,
    obstacles: [hole, chrome.dialog],
    viewport,
    // A dialog backdrop already makes the navigation inert, so the card may use
    // the full gutter beside it instead of being pushed onto the dialog.
    inset: {
      top: 84,
      left: beside ? CARD_GUTTER : (chrome.sidebarRight || 0) + CARD_GUTTER,
      right: CARD_GUTTER,
      bottom: CARD_GUTTER,
    },
    preferred: "top-left",
  });
  // Two portals, because `position: fixed` always creates a stacking context:
  // the dimming layer must stay *below* StoreLens dialogs (120) while the card
  // stays above them, and one shared parent could not do both.
  return <>{createPortal(
    <div className={`demo-tour-layer ${dim ? "is-dimming" : ""}`}>
      {dim && (
        <svg
          className="demo-tour-mask"
          width={viewport.width}
          height={viewport.height}
          viewBox={`0 0 ${viewport.width} ${viewport.height}`}
          aria-hidden="true"
        >
          <path d={maskPath(hole, viewport)} fillRule="evenodd" />
        </svg>
      )}
      {dim && hole && !hole.offscreen && (
        <div
          className="demo-tour-ring"
          style={{
            top: `${hole.top}px`, left: `${hole.left}px`,
            width: `${hole.width}px`, height: `${hole.height}px`,
            borderRadius: `${hole.radius}px`,
          }}
          aria-hidden="true"
        />
      )}
      {block && blockerRects(hole, viewport).map((rect) => (
        <div
          key={`${rect.top}-${rect.left}-${rect.width}-${rect.height}`}
          className="demo-tour-blocker"
          style={{
            top: `${rect.top}px`, left: `${rect.left}px`,
            width: `${rect.width}px`, height: `${rect.height}px`,
          }}
          aria-hidden="true"
        />
      ))}
    </div>,
    document.body,
  )}{createPortal(
    <div className="demo-tour-card-layer">
      <div
        ref={anchorRef}
        className={`demo-tour-card-anchor ${beside ? "beside-dialog" : ""}`}
        style={placement ? { top: `${placement.top}px`, left: `${placement.left}px` } : undefined}
      >
        <TourCard tour={tour} />
      </div>
    </div>,
    document.body,
  )}</>;
}
