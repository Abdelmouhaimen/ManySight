# StoreLens → ManySight dashboard rewrite plan

Date: 2026-07-16  
Status: full frontend migration implemented; backend P1/P2 items remain  
Owner: founder/product, with Codex implementing after scope approval

## 1. Outcome

Turn StoreLens from a generic computer-vision workbench into a credible, clearly
labelled ManySight retail pilot/demo dashboard that uses the visual system and
information hierarchy shown in `manysight/apps/landing`.

The default experience should help a store or operations manager answer:

1. What changed today?
2. Where is operational pressure building?
3. What needs human review?
4. Are the cameras and analyses healthy?

Camera connection, floor-map calibration, worker jobs, API details, and Codex tooling
remain available, but move into a separate technical configuration area rather than
competing with operational insights on the main screen.

## 2. Evidence and constraints

### Recorded ManySight direction

- Current wedge: retail operations analytics.
- Initial workflows: visitor/zone traffic, queue monitoring, anonymous flow,
  heatmaps, dwell, operational reports, and threshold alerts.
- Pilot hypothesis: one store, two to five cameras, four weeks, one measurable
  operational problem.
- Current non-goals include a general platform for every sector, facial recognition,
  persistent identity tracking, and autonomous accusations.
- Current stage is problem discovery; the resulting app must be labelled as a POC or
  illustrative demo, not production-ready or validated.

Sources:

- `manysight/company/CONTEXT.md`
- `manysight/company/CURRENT_FOCUS.md`
- `manysight/company/DECISION_LOG.md`
- `manysight/departments/03-product/BACKLOG.md`
- `manysight/departments/04-computer-vision/MODEL_CAPABILITY_MAP.md`
- `manysight/departments/09-privacy-legal-security/RISK_REGISTER.md`

### Landing-page dashboard language

The landing preview establishes:

- ManySight name and orbit-style brand mark.
- Warm paper background, ink-black surfaces, violet primary accent, lime positive
  accent, orange warning accent, and blue focus state.
- White dashboard shell, subtle grey dividers, compact metric cards, rounded panels,
  small uppercase labels, and restrained shadows.
- Store identity and stream health in the top bar.
- Left navigation: Overview, Insights, Events, Streams, Configure.
- Overview metrics: Visits, Average wait, Engagement, To review.
- Primary panels: visitor traffic, activity map, and recent signals.
- Explicit `Illustrative product preview · Example data` labelling.

Source:

- `manysight/apps/landing/src/main.jsx`
- `manysight/apps/landing/src/styles.css`

### What StoreLens already proves

- Camera source CRUD for RTSP, HTTP, WebRTC, webcams, and files.
- Snapshot capture and basic source status.
- Floor plan, named polygon zones, camera placement, FOV, and homography calibration.
- Generic event ingestion, automatic projection, zone assignment, and SSE updates.
- Heatmap, occupancy, dwell, flow, count, and state analytics.
- Job metadata and alert rules/webhooks.
- MCP bridge and worker SDK.

These should be reused rather than rewritten during the first UI phase.

## 3. Product correction

### Default product surface: retail operations

The customer-facing experience should use ManySight language and focus on an agreed
retail workflow. It should not lead with “Turn any camera into an answer,” model
selection, event schemas, API jobs, or arbitrary sector selection.

Recommended top-level navigation:

| Section | Customer question | StoreLens capability used |
|---|---|---|
| Overview | What changed and what needs attention? | summary, occupancy, dwell, heatmap, alerts |
| Insights | What trends and spatial patterns matter? | counts, heatmap, dwell, occupancy, transitions |
| Events | Which signals require human review? | alerts and matching source events |
| Streams | Are the required camera views healthy? | sources, snapshots, placement/calibration state |
| Configure | How is this pilot set up? | store, zones, calibration, analyses, rules |

### Technical surface: configuration/lab

Put advanced controls behind Configure, with a clear `Technical` or `Lab` label:

- Source credentials and full connection URL.
- Floor-map drawing and homography calibration.
- Analysis jobs and raw event feed.
- Alert-rule JSON-level details and webhooks.
- API documentation and API-key settings.
- Codex/MCP analysis brief and worker-development guidance.

Codex is part of how ManySight configures and maintains analyses; it should not be
presented to a store manager as a general autonomous assistant. If retained in the
UI, rename the action to `Request an analysis` and constrain it to approved pilot
workflows, showing that setup is handled by ManySight.

### Demo versus live state

Every page needs one authoritative environment badge:

- `Example data` for seeded/illustrative data.
- `Live pilot` only when connected to real streams and workers.
- `Setup incomplete` when prerequisites are missing.

Never mix seeded metrics with live metrics without an obvious warning and separate
time range.

## 4. Recommended technical approach

### Phase-one architecture

Keep the FastAPI/SQLite/API implementation in StoreLens and replace only the static
frontend with a React + Vite dashboard that consumes the existing `/api/v1` API.

Suggested layout:

```text
storelens/
  dashboard/
    src/
      app/
      components/
      features/
        overview/
        insights/
        events/
        streams/
        configure/
      lib/api.js
      styles/
    package.json
    vite.config.js
  server/
    static/        # Vite build output served by FastAPI
```

Why this path:

- It preserves the proven POC backend and avoids an unnecessary API rewrite.
- It allows direct reuse/translation of the React dashboard patterns from the landing
  page.
- It creates a maintainable component boundary instead of expanding the current
  template-string/imperative DOM frontend.
- It remains reversible. A later validated product can move the dashboard into
  `manysight/apps/dashboard` and split services deliberately.

Do not create a production multi-service architecture yet. ManySight has no recorded
architecture decision, and customer/deployment requirements remain unvalidated.

### API compatibility rule

Freeze the existing API during the visual rewrite. Add endpoints only for a visible,
defined dashboard need. Do not change worker event schemas in the same phase unless
an acceptance criterion cannot be met otherwise.

## 5. Design system to carry over

Create reusable dashboard tokens based on the landing page:

- `--ink: #11110f`
- `--paper: #f4f2ec`
- `--paper-light: #fbfaf6`
- `--violet: #7059ff`
- `--violet-deep: #5840ec`
- `--lime: #ccff43`
- `--orange: #ff8a4c`
- `--blue: #76d6ff`
- Borders: low-contrast ink at approximately 10–14% opacity.
- Radii: 9–14px for dashboard controls/cards; 24–26px for major shells.
- Typography: Inter/system sans, tight metric/title tracking, small uppercase section
  labels.

Build these shared primitives before feature pages:

1. `BrandMark` and `WorkspaceIdentity`.
2. `AppShell`, `Sidebar`, `MobileNav`, and `PageHeader`.
3. `StatusDot`, `Badge`, `Button`, `IconButton`, `Select`, and form controls.
4. `MetricCard`, `Panel`, `EmptyState`, `Skeleton`, and `ErrorState`.
5. `TimeRangePicker`, `EnvironmentBadge`, and `LastUpdated`.
6. `LineChart`, `BarChart`, `ActivityMap`, `SignalRow`, and `DataTable` wrappers.
7. `Modal`, `Drawer`, `Toast`, and destructive-action confirmation.

Use Lucide icons as in the landing page. Include visible focus states, keyboard
navigation, semantic headings, reduced-motion support, and mobile layouts from the
start.

## 6. Page-by-page rewrite

### A. App shell

- Rebrand StoreLens UI to ManySight, while keeping `StoreLens` as an internal backend
  or repository name if desired.
- Use the landing dashboard top bar: store name/location on the left, stream health
  and environment badge on the right.
- Replace horizontal tabs with the landing-style left sidebar.
- Add responsive mobile navigation that collapses below approximately 700px.
- Add route-level loading and error boundaries.
- Keep API key configuration in Configure, not global primary navigation.

Acceptance:

- Visually coherent with the landing preview at desktop and mobile breakpoints.
- All sections are keyboard reachable and the current section is announced.
- No horizontal page overflow at 390px width.

### B. Overview

Replace the current Codex-first hero with:

- Store/date/time-range heading.
- Four metric cards using only defined data.
- Traffic/occupancy curve.
- Activity heatmap.
- Recent reviewable signals.
- Setup or health callout only when something requires action.

Initial metric mapping:

| Landing metric | POC source | Decision before implementation |
|---|---|---|
| Visits | distinct tracks or explicit entry count | Define “visit” and de-duplication window |
| Average wait | queue-zone dwell | Define queue zone and exclude non-queuing presence |
| Engagement | no safe current definition | Remove until product defines it |
| To review | unacknowledged alerts | Rename to reviewable signals |

Do not display comparison percentages until a backend endpoint calculates a real
comparable previous period.

### C. Insights

Break the current single long dashboard into focused tabs or views:

1. Traffic & occupancy.
2. Queue intelligence.
3. Flow & dwell.
4. Activity map.

Each view gets:

- One primary question and short explanation.
- Time range, zone, and source filters.
- One primary chart, supporting metrics, and data-quality state.
- Clear definitions/tooltips for every metric.
- Empty states that explain which camera, zone, calibration, or worker prerequisite is
  missing.

Move state-monitoring and generic classifier-count charts into an `Experimental`
group unless they support the selected pilot workflow.

### D. Events

Convert alerts into a human-review queue:

- New, in review, resolved, and dismissed states.
- Signal type, time, store/zone, source, threshold, and confidence/quality context
  where available.
- Detail drawer with event metadata and nearby timeline.
- Acknowledge/resolve action with optional note.
- Audit history.
- Language must say signal/event, never accusation or conclusion.

Backend gap: current alerts support only `acknowledged`. A later backend phase must add
review status, notes, assignee (if needed), resolution timestamp, and traceability to
the triggering event(s).

### E. Streams

Turn source cards into an operational health view:

- Snapshot, camera name, location, protocol, and last frame time.
- Online/degraded/offline status.
- Analysis coverage and calibration status.
- Filter by health and required pilot workflow.
- Add/edit source in a drawer with credentials hidden by default.

Backend gap: manual snapshot status is not true health monitoring. Add worker/stream
heartbeats, last-frame timestamp, optional FPS/latency, and a reason for degraded
status before calling this production monitoring.

### F. Configure

Group configuration into a guided sequence:

1. Pilot/workspace details.
2. Streams.
3. Store map and named zones.
4. Camera placement and calibration.
5. Analysis configuration.
6. Thresholds and notifications.
7. Acceptance checks.

Show completion status and prerequisites. The React floor-map workbench now owns wall,
polygon-zone, label, camera-placement/FOV, homography calibration, and projection-test
interactions with labelled controls and accessible status feedback.

Move raw jobs, raw events, MCP details, connection URLs, and API links into a
collapsed `Technical details` section.

## 7. Backend work required after visual parity

Prioritize only gaps needed by the selected pilot workflow:

### P0 — truthful demo behavior

- Add explicit demo/live environment metadata.
- Define canonical visit, queue wait, occupancy, and review-count metrics.
- Add a dashboard aggregate endpoint that returns current and previous-period values
  consistently.
- Add zone/source filters consistently across analytics endpoints.
- Add structured API errors and frontend retry behavior.

### P1 — operational credibility

- Add source and worker heartbeats.
- Separate analysis-job registration from actual worker runtime state.
- Add worker logs/status/restart information, or clearly state that runtime management
  is external.
- Extend alerts into reviewable signals with status history.
- Add retention configuration and basic audit logging.

### P2 — only after pilot need is validated

- Multi-store selection and tenant isolation.
- Authentication, roles, and least-privilege access.
- Edge/cloud deployment enrollment and remote diagnostics.
- Reports, scheduled summaries, and customer notification preferences.
- Video evidence/clip handling, only after a precise purpose and privacy review.

## 8. Implementation sequence

### Phase 0 — product and terminology lock (0.5–1 day)

- Confirm StoreLens is an internal POC and ManySight is the displayed product name.
- Select one primary demo workflow: recommended traffic/occupancy or queue monitoring.
- Define each displayed metric in one sentence.
- Decide whether seeded data is preserved, reset, or isolated from live data.
- Approve the customer-versus-technical navigation split.

Deliverable: approved screen map, metric glossary, and demo/live rule.

### Phase 1 — foundation and shell (1–2 days)

- Scaffold React/Vite dashboard inside StoreLens.
- Add API client, routing, SSE subscription, and query/loading state.
- Port the ManySight tokens, brand mark, sidebar, top bar, panels, and form primitives.
- Add responsive and accessibility baseline.

Deliverable: functional shell with all routes and placeholder states.

### Phase 2 — operational overview (2–3 days)

- Build Overview metric cards, traffic chart, activity map, and recent signals.
- Wire them to existing endpoints.
- Add explicit example/live labelling and metric definitions.
- Add empty, loading, degraded, and error states.

Deliverable: landing-preview-level visual parity using real POC data.

### Phase 3 — focused insights and events (3–4 days)

- Split analytics into focused workflow pages.
- Reuse/adapt existing canvas charts behind React components.
- Build event review list and detail drawer using current alert capability.
- Clearly label unsupported review lifecycle controls until backend work lands.

Deliverable: usable traffic/queue/flow exploration plus review queue.

### Phase 4 — streams and guided configuration (3–5 days)

- Rebuild camera source UI in the ManySight design system.
- Move and restyle map, zones, placement, and calibration.
- Add setup checklist and acceptance-state messaging.
- Move technical jobs, raw feed, MCP, API, and webhooks into advanced sections.

Deliverable: one coherent setup path from camera to insight.

### Phase 5 — backend truthfulness gaps (scope after selected workflow)

- Implement only the P0/P1 endpoints and states required by the agreed demo.
- Add tests for metric definitions, filters, review state, and health semantics.

Deliverable: dashboard labels and statuses backed by defined behavior.

### Phase 6 — QA and demo packaging (1–2 days)

- Desktop checks at 1440px and 1024px.
- Mobile checks at 390px and 768px.
- Keyboard/focus and reduced-motion checks.
- Empty, seeded demo, offline camera, worker failure, API failure, and live-event tests.
- Update README/tutorial and provide a deterministic seed/demo script.

Deliverable: repeatable POC demo with known limitations shown in-product.

## 9. File-level migration map

| Current StoreLens file | Planned destination/action |
|---|---|
| Previous static application | Removed after complete feature parity; no fallback route remains |
| App shell, routing, and SSE | `dashboard/src/main.jsx` |
| Overview, insights, events, streams, setup | `dashboard/src/pages.jsx` |
| Floor-map, camera placement, and calibration | `dashboard/src/space-workbench.jsx` |
| Raw events, connection details, API and agent contract | `dashboard/src/technical-config.jsx` |
| Charts, map rendering, modal, toast, shared states | `dashboard/src/components.jsx` |
| ManySight tokens, responsive layout, workbench styling | `dashboard/src/styles.css` |
| API client and asset authentication | `dashboard/src/api.js` |

The FastAPI routers should remain unchanged through Phase 2 except for explicitly
approved metric-truthfulness additions.

## 10. Acceptance criteria for the rewrite

The rewrite is complete when:

- A first-time viewer understands within 10 seconds that this is retail operational
  video intelligence.
- The UI visually matches the landing dashboard's brand, shell, density, colors,
  typography, navigation, and panel hierarchy.
- Overview answers what changed, where, and what needs review without exposing raw CV
  implementation details.
- Demo/example data is unmistakable and never presented as a validated live result.
- Every metric has a definition and traceable API source.
- Reviewable events use neutral, human-review language.
- Cameras, zones, calibration, rules, and technical details remain usable under
  Configure.
- Empty/offline/uncalibrated/worker-failed/API-failed states are designed and tested.
- The app works at 390px without horizontal page overflow and is keyboard navigable.
- Existing source, event, analytics, alert, SDK, and MCP smoke tests still pass.

## 11. Unresolved decisions and risks

1. **Repository destination:** keep the rewritten UI in StoreLens for the POC, or
   create `manysight/apps/dashboard` now. Recommendation: keep it in StoreLens until
   the first workflow is selected; avoid presenting it as production architecture.
2. **Primary demo workflow:** traffic/occupancy versus queue monitoring. This determines
   the metric cards and page order.
3. **Metric semantics:** current distinct tracks are not automatically equivalent to
   visits, and zone dwell is not automatically queue wait.
4. **Demo/live separation:** the current seed writes into the same database as live
   events.
5. **Health claims:** online snapshots and registered jobs do not prove continuous
   stream or worker health.
6. **Broad platform drift:** the school/general-space UI recently added to StoreLens
   conflicts with ManySight's recorded retail focus. Keep it as a technical POC
   capability, not the ManySight customer-facing default.
7. **Privacy/security:** current credentials, retention, auditability, and access model
   are POC-level and must not be described as production-ready.

## 12. Recommended next action

Approve these three choices before implementation:

1. Display name: `ManySight`, with StoreLens retained only as the internal repository.
2. First dashboard workflow: `Traffic & occupancy` (recommended) or `Queue monitoring`.
3. UI location: React/Vite frontend inside StoreLens for the POC (recommended), with
   later migration to `manysight/apps/dashboard` only after validation.

After approval, implement Phase 1 and Phase 2 as the first bounded build milestone.
