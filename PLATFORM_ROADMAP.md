# ManySight / StoreLens platform roadmap

Date: 2026-07-17
Status: proposed execution plan
Horizon: first design partner through a four-week production pilot, then productization

## 1. Executive direction

The current StoreLens repository is a strong end-to-end proof of concept: it can store
camera sources, model a physical space, calibrate cameras, ingest generic events,
calculate several analytics, render a ManySight dashboard, and expose an MCP surface
for Codex.

It is not yet a working customer platform because workers are external and
unsupervised, job status is not runtime truth, extracted knowledge is not modeled as
persistent state, insights are still partly hard-coded, and security, tenancy,
observability, retention, and deployment operations remain POC-level.

The recommended strategy is:

1. Keep **retail operations** as the first commercial wedge, following the ManySight
   decision log and twelve-week design-partner objective.
2. Build a **general physical-space intelligence core** underneath that wedge:
   observations, entities, state, worker checkpoints, dynamic insights, provenance,
   and scoped MCP access.
3. Add horizontal capability only when it is required by the selected pilot or when a
   second use case proves that the abstraction is real.
4. Treat warehouse medicine tracking as an architectural stress test, not an initial
   safety or inventory-accuracy promise.

This avoids two opposite mistakes: producing a one-off retail dashboard that cannot
grow, or spending months building an abstract “analyze anything” platform before a
customer proves what matters.

## 2. Definition of a working v1 platform

ManySight v1 is working when a technician and operations user can complete this flow:

1. Create an organization, site, workspace, and purpose-bound workflow.
2. Enroll two to five existing cameras without exposing credentials to ordinary users
   or agents.
3. Draw zones, place cameras, calibrate them, and pass an acceptance check.
4. Select an approved analysis recipe and deploy a versioned worker to an edge or
   approved cloud runtime.
5. Observe real worker health, logs, model version, throughput, and last processed
   frame—not merely a registered job.
6. Restart the worker or edge host without losing counts, current state, or processing
   position.
7. Accumulate immutable observations and derive persistent, explainable current state.
8. Let Codex discover existing knowledge before proposing or creating another
   analysis.
9. Register a structured insight that appears without a frontend deployment.
10. Show freshness, definition, provenance, quality, limitations, and responsible
    workflow on every operational insight.
11. Raise a narrow reviewable signal, record the human decision, and preserve an audit
    trail.
12. Run for at least seven unattended days before the customer pilot and for four weeks
    in the agreed pilot, within measured accuracy, uptime, support, privacy, and cost
    limits.

## 3. Product and architecture principles

### 3.1 Narrow promise, reusable core

- Market and validate one retail decision first: queue response, entry/zone traffic,
  or anonymous flow and dwell.
- Keep the underlying nouns general: workspace, source, zone, observation, entity,
  state, analysis, insight, signal, and review.
- Do not display irrelevant retail panels in a school or warehouse workspace.
- Do not call a generic event stream a business outcome until its metric definition is
  validated.

### 3.2 Observations are not truth

- An observation records what a model reported, including confidence and provenance.
- Current state is a derived projection and may be stale or uncertain.
- A human correction or reconciliation is a new auditable fact; it does not erase
  history.
- High-impact warehouse or medicine decisions must not rely on visual tracking alone.

### 3.3 Workers are replaceable extractors

- Workers do computer vision; the platform owns lifecycle, state, history, and
  presentation.
- A track ID is local to a worker run or source unless stronger evidence resolves it to
  a persistent entity.
- Checkpoints accelerate recovery but are not the source of business truth.
- The observation ledger must be replayable to rebuild derived state.

### 3.4 Agents use contracts, not unrestricted access

- Codex discovers sources, schemas, catalogues, approved recipes, existing datasets,
  and current state through bounded MCP tools.
- It registers versioned analysis and insight definitions rather than injecting UI
  code or editing production databases.
- Camera secrets, unrelated tenants, raw personal data, and destructive operations are
  outside the normal MCP surface.
- Deployments, sensitive classifiers, external webhooks, and data deletion require
  explicit authority and audit records.

## 4. Recommended target architecture

Start with a **modular monolith plus a separate edge runtime**. Do not begin with a
microservice fleet, Kubernetes, Kafka, or a model marketplace.

```text
ManySight web application
  ├─ operational overview and review queue
  ├─ dynamic insight catalogue
  └─ setup, calibration, health, and administration
                  │
ManySight control plane (FastAPI modular monolith)
  ├─ organizations, sites, users, roles, purposes
  ├─ sources, maps, zones, calibrations
  ├─ analysis and insight definitions
  ├─ worker deployments and health
  ├─ observations, entities, state projections
  ├─ signals, reviews, audit, retention
  └─ MCP and public API contracts
          │                    │
PostgreSQL + object storage    │ secure command/telemetry channel
          │                    │
          └──────────── ManySight edge agent
                           ├─ camera connectors
                           ├─ supervised worker processes/containers
                           ├─ encrypted local configuration
                           ├─ local buffer and checkpoints
                           └─ health, logs, update, rollback
```

Recommended first infrastructure choices:

- **PostgreSQL** for transactional platform data, observations, state, and audit.
- **Object storage** for approved evidence frames, clips, model artifacts, and exports.
- A transactional outbox and background task runner before adopting a distributed
  event bus.
- Optional Redis only when a measured need appears for ephemeral coordination or
  caching.
- Optional vector search later for product/reference-image discovery; never as the
  source of truth.
- One deployable control-plane application until load or team ownership proves a split
  is necessary.

## 5. Canonical data and knowledge model

The platform needs explicit layers rather than one increasingly overloaded `events`
table.

| Layer | Purpose | Important records |
|---|---|---|
| Tenancy and purpose | Scope data and permissions | organization, site, workspace, user, role, workflow purpose |
| Physical model | Describe the environment | source, map, zone, calibration, device placement |
| Analysis definition | State what should run | recipe, model version, configuration, sources, output schema, quality target |
| Runtime | State what is actually running | deployment, worker run, heartbeat, checkpoint, log, processing cursor |
| Observation ledger | Preserve model outputs | observation ID, idempotency key, timestamp, source, model/run, geometry, confidence, attributes |
| Tracking | Represent temporary continuity | track segment, source/run scope, start/end, associations |
| Entity knowledge | Represent durable objects | entity type, SKU/catalog item, serialized asset, aliases, reference evidence |
| Entity resolution | Link evidence to entities | track/entity association, method, confidence, reviewer |
| Current state | Answer “what is true now?” | state projection, value, location, freshness, uncertainty, provenance |
| Insight | Describe a user-facing question | insight definition, dataset query, visualization block, unit, filters, limitations |
| Review and governance | Record human decisions | signal, review, correction, reconciliation, audit log, retention policy |

Every ingesting worker should provide a stable idempotency key. Late or repeated
events must not double-count. State projections should track a processing watermark
and be rebuildable from observations and reconciliations.

### Persistent tracking rule

- `track_id`: local and temporary.
- `track_segment_id`: durable record of one local tracking interval.
- `entity_id`: persistent only when a workflow has sufficient identity evidence.
- People should not receive persistent cross-session entity identities in the initial
  product.
- Products may be resolved to a SKU using barcode/OCR/reference evidence; an individual
  package requires a serial identifier or equivalent evidence.

### Warehouse medicine example

The system may know that Shelf A was last observed with 22 boxes of SKU X at 10:31,
with 91% confidence and Camera 4 as provenance. A restarted worker loads the last
processing cursor and state, processes new frames, and reconciles differences. If the
camera is blocked, the state becomes stale; it does not become zero. Any safety,
dispensing, or regulated inventory claim requires stronger controls and independent
reconciliation.

## 6. Dynamic insight platform

The Insights page should be an initially empty catalogue populated by structured
insight definitions. The API may compose supported blocks; it may not post arbitrary
React, HTML, or JavaScript.

Initial block registry:

- Metric/KPI
- Time-series line or area chart
- Bar comparison
- Table
- Floor-map points, zones, or heatmap
- Flow matrix
- State timeline
- Reviewable event list
- Text definition or limitation

Each insight definition must include:

- User question and title
- Owning workflow and analysis version
- Dataset/query reference and dimensions
- Unit, aggregation, time grain, and filters
- Freshness and stale-data policy
- Quality status and validation result
- Provenance and model/configuration version
- Human-readable definition and limitations
- Supported visualization and table fallback
- Visibility, ordering, and Overview pinning

An insight lifecycle should be visible: `draft → collecting → validating → ready →
degraded → retired`.

## 7. Worker and edge lifecycle

The current SDK and example scripts should evolve into a supervised worker contract.

A worker run should:

1. Receive a signed, versioned analysis configuration.
2. Load its last committed cursor, checkpoint, catalogue, and recent state.
3. Connect to authorized sources through the edge agent.
4. Emit schema-validated observations in idempotent batches.
5. Commit its checkpoint only after observations are accepted.
6. Send heartbeat, FPS, latency, queue depth, model version, and structured logs.
7. Buffer safely during short network outages and report gaps.
8. Restart with bounded backoff and expose the actual failure reason.
9. Support staged update, rollback, pause, and removal.

The platform—not a dashboard label—decides whether a worker is healthy.

## 8. MCP roadmap

Organize MCP tools around five bounded capabilities.

### Discover

- List accessible workspaces, sources, maps, zones, purposes, and schemas.
- Inspect redacted source metadata and approved snapshots.
- List recipes, models, capability requirements, and validation status.

### Read knowledge

- Search entity catalogues and reference evidence.
- Query observations, track segments, current state, freshness, and history.
- List existing datasets and insights before creating duplicates.
- Explain the provenance of a state, metric, or signal.

### Configure

- Propose/register versioned analysis definitions.
- Register structured insight definitions and validation requirements.
- Create narrow review rules and webhooks within policy.

### Operate

- Request an approved deployment, pause, restart, or rollback.
- Inspect worker health, checkpoints, gaps, and logs.
- Operations that affect production remain permissioned and auditable.

### Verify and reconcile

- Query test windows and ground-truth samples.
- Compare output against agreed metrics.
- Record human corrections and reconciliation observations.
- Mark an insight ready only after its validation gate passes.

## 9. Phased execution roadmap

Timing is an estimate for a small focused team. Customer discovery and design-partner
work run in parallel and take priority over speculative breadth.

### Phase 0 — workflow and architecture lock (weeks 0–3)

Deliver:

- Complete the ManySight discovery plan: ten interviews and three representative
  camera/footage samples.
- Select one retail workflow and identify its user, decision, frequency, current
  workaround, owner, and measurable value.
- Agree pilot accuracy, false-alert, uptime, usage, business, privacy, and exit measures.
- Write architecture decisions for deployment topology, tenancy, data retention,
  evidence handling, and the canonical vocabulary above.
- Freeze non-essential dashboard polish and additional vertical features.

Exit gate:

- A named design partner or strong candidate accepts a written one-store pilot brief.
- The workflow has approved sample data and a measurable success definition.
- The architecture is sufficient for that workflow without claiming to solve every
  sector.

### Phase 1 — persistent platform core (weeks 2–6)

Deliver:

- Introduce schema migrations and PostgreSQL while keeping a simple local development
  profile.
- Add organizations/sites/workspaces and scope every source, zone, job, observation,
  insight, signal, and audit record.
- Replace the generic event-only assumption with versioned observations,
  idempotency, provenance, confidence, processing watermarks, and retention metadata.
- Add entity catalogue, track segments, associations, state projections,
  reconciliations, and worker checkpoints needed by the chosen workflow.
- Provide replay tests that rebuild current state from the ledger.

Exit gate:

- Duplicate batches do not double-count.
- A restart resumes from the last committed cursor rather than zero.
- Current state can be rebuilt and every value can be traced to observations.
- One workspace cannot read another workspace’s data.

### Phase 2 — supervised edge and worker runtime (weeks 5–10)

Deliver:

- Build one installable edge agent for Windows/Linux or the pilot’s actual environment.
- Add secure enrollment, source connectivity checks, local buffering, secret handling,
  heartbeat, logs, metrics, and remote diagnostics.
- Define the worker package/manifest contract and versioned configuration.
- Supervise start, stop, restart, update, rollback, and checkpoint recovery.
- Record real last-frame time, FPS, latency, queue depth, gaps, model version, and worker
  health in the control plane.
- Establish CPU/GPU minimum profiles and measure compute per camera.

Exit gate:

- A worker runs for seven days in staging without manual babysitting.
- Network and process restarts recover without silent data loss or count resets.
- The dashboard distinguishes camera, edge-agent, and analysis-worker failures.

### Phase 3 — dynamic insights and expanded MCP (weeks 8–12)

Deliver:

- Add the insight registry, dataset contract, lifecycle, renderer blocks, table fallback,
  ordering, hiding, and Overview pinning.
- Make Insights honestly empty when no insight is registered.
- Display definition, unit, freshness, quality, provenance, model version, and
  limitations.
- Add bounded MCP discovery, knowledge, analysis, insight, runtime, and verification
  tools.
- Prevent arbitrary UI code, raw database access, cross-tenant search, and normal agent
  access to source credentials.

Exit gate:

- Codex can create a labelled count curve or zone-dwell insight without a frontend
  code change.
- Restarting or replacing its worker preserves history and current state.
- Every rendered number is traceable and stale data is visible.

### Phase 4 — selected retail workflow and evaluation (weeks 10–14)

Deliver only the workflow selected by evidence, most likely one of:

- Entry/zone traffic
- Queue-zone pressure
- Anonymous flow and dwell

For that workflow:

- Define camera/view requirements and an installation profile.
- Build or select the detector/tracker and any required business logic.
- Create annotated peak/off-peak evaluation samples from approved footage.
- Measure accuracy by operating condition, not only an overall score.
- Tune zones and thresholds using separate validation data.
- Create acceptance checks and a manager-facing insight/action loop.

Exit gate:

- The workflow passes its pre-agreed technical thresholds on representative data.
- Failure modes and unsupported camera conditions are documented.
- A manager can explain what action the insight would change.

### Phase 5 — pilot hardening and installation operations (weeks 13–17)

Deliver:

- Authentication, role-based access, least-privilege service identities, and secure
  secret storage.
- Encryption in transit, backup/restore, deletion and retention jobs, audit logs, and
  incident/escalation procedures.
- Operational monitoring for API, database, storage, edge agents, sources, workers,
  data gaps, and webhooks.
- Validated installation checklist: commercial handoff, site survey, approvals,
  enrollment, zones, calibration, acceptance sampling, training, and removal.
- Pilot support rota, remote-resolution procedure, change freeze, and rollback plan.

Exit gate:

- Security/privacy review and purpose-specific data-flow record are approved.
- Backup restore and edge rollback are tested.
- Installation succeeds from the playbook and the actual labor time is recorded.
- No critical failure is hidden behind a generic “online” state.

### Phase 6 — four-week customer pilot (weeks 18–22)

Operate one store, two to five cameras, and one workflow.

Measure weekly:

- Workflow accuracy and false/missed output rate
- Camera, edge, and worker uptime separately
- Event delay, gaps, stale state, and recovery time
- Manager usage and reviewed signals
- Decisions or actions influenced
- Installation and support hours
- Compute, storage, network, and travel cost
- Privacy/security incidents or concerns

Exit gate:

- Continue only if the agreed business and technical measures pass and the customer
  wants continued use or expansion.
- If they fail, record whether the problem was demand, camera suitability, model
  performance, workflow design, usability, operations, or economics before building
  more features.

### Phase 7 — productization after pilot evidence

Prioritize based on measured demand:

- Multi-site fleet views and tenant administration
- Standard deployment profiles and remote upgrades
- Scheduled reports and exports
- Usage/cost controls and commercial packaging
- Stronger model/recipe registry and compatibility testing
- Additional retail workflows
- A second vertical only when a customer and data justify it

Warehouse inventory can become a later module by adding product catalogues, shelf/bin
state, movement ledgers, barcode/OCR evidence, reconciliation workflows, and stricter
quality controls on top of the same core.

## 10. Priority backlog

### P0 — required before a credible pilot

1. Select and define one retail workflow.
2. Write the architecture and data-contract decisions.
3. Add migrations, PostgreSQL, tenancy scope, and purpose records.
4. Add idempotent observations with provenance and replay.
5. Add worker runs, checkpoints, heartbeats, logs, and true health.
6. Build a supervised edge agent and offline buffer.
7. Add persistent state/reconciliation required by the selected workflow.
8. Add the dynamic insight registry and explainable renderer.
9. Expand MCP around scoped knowledge and lifecycle tools.
10. Build the representative evaluation set and acceptance harness.
11. Add authentication, roles, secrets, audit, retention, backup, and monitoring.
12. Validate the installation and rollback playbooks.

### P1 — required after the first end-to-end slice

- Worker package signing and staged rollout
- Dataset exports and scheduled reports
- Alert delivery retries and webhook observability
- Human correction and reconciliation UX
- Data-quality dashboards and gap visualization
- Model/configuration comparison and rollback evidence
- Site templates that reduce installation labor

### P2 — only after pilot evidence

- Multi-site customer administration
- Billing and usage metering
- Distributed event infrastructure
- Kubernetes or multi-service extraction
- Broad model marketplace
- Warehouse/school vertical packages
- Cross-camera durable product identity beyond the selected need

## 11. Metrics that govern progress

### Customer and product

- Recurring problem incidents reported during discovery
- Accountable user and budget owner identified
- Weekly active operational users during pilot
- Percentage of insights or signals that lead to a documented review/action
- Customer willingness to continue, pay, or expand

### Computer vision and data

- Workflow-specific precision/recall or count error
- Error by crowding, lighting, view, occlusion, and camera condition
- Association/identity confidence where relevant
- Reconciliation frequency and state drift
- Percentage of state that is fresh, stale, uncertain, or unverified

### Platform reliability

- Source, edge-agent, and worker uptime measured separately
- Event acceptance and end-to-end latency
- Duplicate, lost, late, and rejected observation rates
- Checkpoint recovery time and recovery-point objective
- Insight freshness and query latency

### Operations and economics

- Installation hours per site and per camera
- Percentage of incidents resolved remotely
- Support hours per site per month
- Compute, storage, and bandwidth cost per camera/workflow
- Non-standard customization effort

## 12. Risk controls and non-goals

Required controls:

- One documented, proportionate purpose and data flow per workflow
- Minimum data collection and explicit retention/deletion
- Encryption, least privilege, credential vaulting, access logs, and audit trail
- Conservative language and human review for consequential signals
- Model/version provenance and visible uncertainty
- Site/camera suitability checks before promising performance
- No cross-customer “global knowledge”; shared knowledge is workspace/tenant scoped

Non-goals for the first pilot:

- Facial recognition or persistent person identity
- Autonomous accusations, enforcement, staffing, or safety decisions
- Arbitrary code supplied as an insight
- A general model marketplace
- Every sector or every camera protocol
- A regulated medicine inventory or dispensing system
- Complex distributed infrastructure without measured need

## 13. First 30 days

Allocate roughly 60% of founder/product effort to customer and pilot evidence and 40%
to the smallest persistent platform slice.

### Week 1

- Conduct the first five retail interviews and qualify camera access.
- Draft the workflow scorecard: pain, frequency, owner, action, data, risk, and ROI.
- Write ADRs for the modular-monolith/edge boundary and canonical vocabulary.

### Week 2

- Complete ten interviews and choose the leading workflow if evidence is sufficient.
- Obtain approved representative footage/specifications.
- Finalize the v1 observation, state, checkpoint, and insight contracts.
- Create the PostgreSQL migration and tenancy plan.

### Week 3

- Build one vertical slice in development: source → supervised simulated worker →
  idempotent observation → persistent state → dynamic insight.
- Prove restart, replay, duplicate handling, stale-state behavior, and provenance.
- Draft the pilot brief and success thresholds with the design-partner candidate.

### Week 4

- Exercise the slice with representative footage.
- Record camera suitability and model failure modes.
- Decide whether to proceed, narrow the workflow, or stop based on evidence.
- Lock the Phase 1/2 backlog and remove work not required by the pilot.

## 14. Immediate decisions

1. **First workflow:** recommend entry/zone traffic unless interviews show queue response
   has a clearer owner and action.
2. **Initial architecture:** approve modular FastAPI control plane + PostgreSQL + object
   storage + separate edge agent.
3. **Identity boundary:** no persistent person identity; durable product/entity identity
   only with workflow-appropriate evidence.
4. **Dynamic insights:** approve the structured registry and supported block model; no
   arbitrary UI code.
5. **Repository role:** keep StoreLens as the executable platform prototype during the
   first pilot; move or rename it only after product and deployment decisions stabilize.
6. **Pilot gate:** no customer deployment until representative evaluation, security/data
   flow review, recovery test, and installation acceptance pass.

## 15. Source decisions used

This roadmap incorporates:

- `manysight/company/CONTEXT.md`
- `manysight/company/CURRENT_FOCUS.md`
- `manysight/company/DECISION_LOG.md`
- `manysight/departments/03-product/BACKLOG.md`
- `manysight/departments/03-product/EXPERIMENTS.md`
- `manysight/departments/04-computer-vision/MODEL_CAPABILITY_MAP.md`
- `manysight/departments/06-sales/PILOT_OFFER.md`
- `manysight/departments/08-operations/INSTALLATION_PLAYBOOK.md`
- `manysight/departments/09-privacy-legal-security/RISK_REGISTER.md`
- `manysight/departments/10-finance-fundraising/ASSUMPTIONS.md`
- The current StoreLens codebase, API, MCP tools, dashboard, SDK, examples, and agent
  operating contract
