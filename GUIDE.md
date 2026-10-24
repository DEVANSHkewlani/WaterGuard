# WaterGuard — implementation and system guide

**Current version:** plain HTML/CSS/JavaScript interface, FastAPI backend, WNTR hydraulics and LangGraph investigator. **Scope:** a reproducible synthetic demonstration on WNTR's Net1 network.

## Start here: why the buttons appeared inert

The earlier package used `/app.js` and `/styles.css`. Opening `frontend/index.html` directly from a ZIP or Finder resolves those absolute paths incorrectly, so the JavaScript does not load and clicks do nothing. This version uses relative asset paths and shows a persistent connection message if the backend is unavailable. The WNTR fault simulator still requires FastAPI; a `file://` page or a generic static server cannot run the project.

On macOS, extract the ZIP, double-click `start.command` (or run `bash start.command` in Terminal), wait for the server message, then open `http://127.0.0.1:8000/`. Keep the Terminal window open. On other systems, use the Python commands in section 7. A first run installs dependencies and may take several minutes. If another process uses port 8000, stop it or choose a different port and open the matching URL.

## 1. The idea in everyday language

A water distribution system has a source, storage, a pump, junctions where water is used, and pipes connecting them. Sensors can report **pressure** at junctions and **flow** through pipes. A leak, restriction, or unusually high demand changes several readings at once. WaterGuard asks: *which proposed physical change best explains the readings we actually have?*

The application first checks readings against a healthy hydraulic model. It uses a precomputed catalog to locate promising fault **types and locations**, estimates fault strength between catalog samples, then runs WNTR on fresh copies of the network to verify those hypotheses. A workflow graph controls the steps and the simulation budget. It reports ranked explanations, sensor evidence, ambiguity, and suggested human checks. It does not observe a real utility, infer a calibrated probability, or operate valves.

### A useful mental model

| Term in the screen | Meaning | In this implementation |
| --- | --- | --- |
| Junction J-12 | An actual node ID in WNTR's Net1 model; `J-` is a display prefix | Pressure sensor location; demand may also be injected there |
| Pipe P-11 | An actual link ID in Net1; `P-` is a display prefix | Flow sensor location; leak or restriction may be injected there |
| Reservoir 9 | Fixed supply boundary in the model | Feeds the network through pump 9 |
| Tank 2 | Stored water with level that can evolve | Influences pressures over time |
| Pressure | Pressure head relative to a junction's elevation | Metres of water head (`m`), not pipe flow |
| Flow | Volume passing a pipe cross section per unit time | Screen: litres/second (`L/s`); API and WNTR: cubic metres/second (`m³/s`); 1 L/s = 0.001 m³/s |
| Negative flow | Water moves opposite the model link's start-to-end direction | A directional result, not necessarily a bad sensor |
| Baseline | WNTR prediction with no injected fault | Same initial simulation time as the alert snapshot |
| Observed | Readings supplied to the investigator | Synthetic WNTR output in demos, or manual input; only supplied sensors enter the score |
| Tested model | A fresh WNTR simulation of one shortlisted hypothesis | Compared with observed readings in the evidence table |

IDs such as 11, 12, 21 and 22 are **Net1 identifiers**, not random severity values or real addresses. Pipe 11 and junction 11 are different objects. The diagram uses Net1's topology and schematic coordinates. The displayed layout is **not a geographic map**, and a high change at a sensor does not prove the physical fault is there.

## 2. What happens when you use the page

1. Open `/` and load **Pipe leak**, **Pipe restriction**, or **Demand surge**, or use **Inject a custom synthetic fault** to choose a type, Net1 location and strength. The three preset examples use hidden severity `0.55`; a custom fault uses your chosen strength. The backend injects the fault into a fresh Net1 model and returns all original junction pressures and pipe flows at hour 0. The frontend receives ground truth for a post-run demonstration disclosure, but the **investigation POST includes only sensor readings**. The separate visible **enter actual sensor readings** form lets you submit junction pressure in m or pipe flow in L/s; adding one after a demo clears the demo values.
2. Inspect the complete map and table. The map draws all Net1 junctions, twelve pipes, reservoir, tank and pump. It prints observed pressure beside junctions and observed flow beside pipes; click one for baseline, observation and difference. The table lists every supplied sensor. Changed sensors are highlighted; after investigation, red and amber show ranked hypothesis locations. They do not reveal the synthetic ground truth before the report.
3. Examine the two whole-network profile charts. One plots the nine junction pressures, the other the twelve pipe flows. The x-axis contains **sensor IDs, not time**. Each chart overlays the healthy hour-0 WNTR baseline (blue) and the observed fault-injected hour-0 readings (orange). For manual partial readings, the missing orange locations stay blank. The investigator scores those same supplied readings. Hover over a marker for its exact value and change.
4. Click **Investigate readings**. The browser posts `{network_name, observed, budget: 8}` to FastAPI. The server returns a session ID, and browser `EventSource` consumes Server-Sent Events (SSE). These events contain actual graph step names, details, and elapsed wall-clock milliseconds. On a small local network the steps may complete in a second; the interface replays the recorded events and invents no waiting time.
5. Read the result. The best tested candidates show mismatch scores and relative weights. The evidence table compares healthy, observed and leading-model values for the supplied sensors. If the model fit or separation is weak, the result says **Ambiguous** even when a candidate ranks first. Recommendations describe measurements or field checks for an operator; no physical action is automated.

### The physical network

Net1 contains **9 demand junctions**, **12 pipes**, **one reservoir**, **one tank**, and **one pump**. The source path is reservoir 9 → pump 9 → junction 10. Junctions 10–13 form the upper pipe run; junctions 21–23 and 31–32 form branches. Tank 2 joins junction 12 through pipe 110. A pipe's ID and its start/end node IDs are separate fields; click a pipe to see its endpoints.

The map reads `/api/network/Net1`: `nodes` carry WNTR ID, type and schematic position; `edges` carry link ID, source, target and type. The SVG is generated by `frontend/app.js` and scales the schematic coordinates to avoid the old overlapping map. The pump is drawn as a special link; it is not a fault candidate. Reservoir and tank are not junction pressure sensors.

## 3. The hydraulic simulator: where the numbers come from

`backend/hydraulics.py` creates a **fresh WNTR WaterNetworkModel** for every run. WNTR solves the hydraulic network using the modeled pipe, pump, tank and demand properties. The code copies the original junction and pipe lists before mutating a fault model, so generated leak nodes or split pipe segments are not presented as original sensors.

| Hypothesis | What code changes in the model | Demo meaning and caveat |
| --- | --- | --- |
| Pipe leak at P-11 | Split original pipe 11, add a pressure-dependent leak at the inserted node; leak area is `0.00035 × severity` m² | Synthetic pipe loss; no exact geographic leak point is inferred |
| Pipe restriction at P-21 | Increase its `minor_loss` by `300 × severity / max(1.01 − severity, 0.01)` | Represents resistance similar to a partly closed valve; Net1 does not establish a real valve there |
| Demand surge at J-12 | Add junction demand `0.012 × severity` m³/s | Models extra consumption; may resemble a leak |

`simulate()` sets duration zero and returns the first hydraulic snapshot. The main page plots these **network-wide snapshot readings** across junction and pipe IDs. `simulate_series()` remains available through the API for a separate 12-hour model projection, but it does not supply the two main charts or the diagnosis score. Its hour-0 values are tested against `simulate()`. A physical time history would require real timestamped measurements.

## 4. Why both a catalog and fresh simulations exist

There are 12 pipes × 2 pipe fault types × 3 strengths, plus 9 junctions × 1 demand fault type × 3 strengths: **99 stored signatures**. The strengths are `0.35`, `0.70`, and `1.00`. The healthy baseline adds one run: **100 one-time offline WNTR runs**. `data/Net1_fingerprints.json` stores the model predictions, candidate labels, WNTR version and schema. Rebuild the catalog when its simulator version or implementation changes.

At alert time `fingerprints.py` scores the observed sensor subset against all stored signatures, groups by **type + location** so three strengths do not fill three ranks, and retains at most eight promising type-and-location families. For each family, it projects the readings onto the segment between adjacent stored strengths to estimate a **continuous severity**. This is a cheap numerical guess, not a WNTR run. The hidden demo's `0.55` severity is absent from the catalog.

`graph.py` then takes up to two families per batch, runs **new WNTR models** at their estimated strengths in parallel, scores their predictions, assesses whether the best is distinct, and either tests the next batch or stops. It does not merely return the nearest catalog row. It also cannot promise to discover a cause outside its three modeled fault families, especially with sparse sensors, an inaccurate network model or simultaneous faults.

The mismatch used for the healthy check, shortlist and verification is:

```text
mean over supplied sensors of ((observed − predicted) / sensor_scale)²
sensor_scale = 0.8 m for pressure; 0.001 m³/s (= 1 L/s) for flow
```

For example, a 1.6 m pressure difference contributes `(1.6 / 0.8)² = 4` before averaging. Only keys present in `observed` count; missing sensors neither become zero nor secretly enter the result. A baseline mismatch above `0.02` triggers candidate investigation. Scales, threshold and stop rule are **demo assumptions**, not calibrated field error bars. A lower candidate mismatch means its predicted sensor pattern fits better. `relative_weight` is an exponential weight **among the tested candidates**, not a Bayesian posterior or physical probability. The graph flags ambiguity when fit is poor, comparable to baseline, or insufficiently separated from the next candidate.

## 5. What each “agent” actually does

The stages are LangGraph **nodes with assigned tasks**, not independent people or eight language models. The optional LLM only paraphrases an evidence sentence; it never changes WNTR readings, ranking, or field action.

| Stage | Input → output | Work it performs |
| --- | --- | --- |
| `monitor` | readings → healthy mismatch and alert | Loads the versioned catalog and detects deviation |
| `context` | network name → maintenance, demand and weather records | Reads three scoped SQLite tables in parallel; empty records mean unknown context |
| `hypotheses` | observations → shortlist and estimated strengths | Retrieves 99 signatures, groups type + location, interpolates severity |
| `simulate` | next two candidates → predicted readings | Runs two fresh independent WNTR models in a worker pool; bounded by budget |
| `evidence` | WNTR rows → ranked, ambiguous or distinct | Computes fit, relative display weights and loop/stop conditions |
| `diagnose` | ranking and context → explanations and recommendation | Builds per-sensor comparisons; calls `decisions.py`; may run separate isolation what-if |
| `sensor_planner` | two leading simulations → next pressure location | If ambiguous, suggests an unmeasured junction where predictions differ most |
| `report` | accumulated state → JSON report | Includes counts and actual elapsed time |

The `no_alert` route does not spend simulations. `context` can change recommendation wording if planned work matches a location; its empty default tables do not verify that no maintenance exists. The recommendation rules advise leak inspection, restriction/valve checks, or demand verification. An isolation what-if, when present, **assumes the candidate pipe is closable** and checks a synthetic 14 m minimum pressure threshold; it is an extra simulation, not proof of an available safe valve. The graph never actuates a device.

### Evidence and diagnosis on the page

The **sensor comparison** table is measured evidence relative to the healthy model: values, units and difference. The **diagnosis** is a proposed explanation after testing a fault model. In the leading candidate's evidence table, each row is a sensor and its healthy, observed and tested-model values. If observed is closer to the tested prediction than to baseline across supplied sensors, that supports the hypothesis, but model error or another cause may still explain the pattern. The top rows show the largest standardized observed-versus-baseline changes; **all** supplied sensors contribute to the score.

The timeline is a record of actual nodes, shortlists, simulation batches, current leaders and advice. Expand a step to see its recorded JSON. The report can be downloaded as JSON. `backend/main.py` streams events with `time.monotonic()`. A very fast local demonstration is expected; speed by itself is not evidence that hydraulic verification was skipped. The report distinguishes `offline_simulation_count` (100) from `simulation_count` (up to 8 online) and an optional `isolation_simulations` (0 or 1).

## 6. Repository structure and connections

```text
waterguard/
├── GUIDE.md                     This current explanation and runbook
├── start.command                macOS/Linux start script (creates venv, starts API)
├── requirements.txt             Runtime Python dependencies
├── requirements-dev.txt         pytest and HTTP test client
├── requirements-optional.txt    Optional AgentOps, LangSmith, OpenAI, dotenv
├── .env.example                 Optional environment variable names
├── evaluate.py                  Ten hidden clean/noisy synthetic injections
├── manage_context.py            Enter optional maintenance, demand, weather context
├── data/
│   └── Net1_fingerprints.json   Offline signatures and healthy baseline
├── backend/
│   ├── __init__.py
│   ├── models.py                Candidate and severity grid
│   ├── hydraulics.py            Net1/Net2 model, faults, snapshots, series and what-if
│   ├── fingerprints.py          Catalog build/load, shortlist and interpolation
│   ├── scoring.py               Scaled sensor mismatch and explanation
│   ├── context.py               Network-scoped SQLite context access
│   ├── decisions.py             Deterministic human-facing recommendations
│   ├── narrative.py             Optional restricted LLM paraphrase
│   ├── observability.py         AgentOps spans and truthful trace status
│   ├── graph.py                 LangGraph state, nodes, loop, report
│   └── main.py                  FastAPI routes, validation, sessions and SSE
├── frontend/
│   ├── index.html               Content and semantic sections
│   ├── styles.css               Responsive layout, map/chart and result styles
│   └── app.js                   Fetch, map, charts, units, SSE and DOM updates
└── tests/
    ├── test_waterguard.py       Physics, catalog, graph, API, SSE and projections
    └── ui-smoke.cjs             DOM event wiring smoke test without npm
```

`main.py` mounts `frontend/` at `/` **after** the `/api/...` routes. The same server serves both; no Node, npm, React, Vite or cross-origin proxy is required. The dependency path is browser → FastAPI → graph → catalog/context/WNTR → report/SSE → browser. The three frontend files contain all current page code. The catalog is generated data; the full source is in these files rather than duplicated in a long guide, so the repository is the authoritative implementation.

| API route | Purpose |
| --- | --- |
| `GET /api/network/Net1` | Original nodes, links, locations and topology |
| `GET /api/baseline/Net1` | Healthy hour-0 catalog values and offline count |
| `GET /api/observability` | Local timeline and external tracing configuration status; no keys returned |
| `GET /api/demo/{leak,valve,surge}` | Synthetic hour-0 observations and separately disclosed ground truth |
| `GET /api/demo/{kind}/series` | Healthy and injected-fault 12-hour projections |
| `POST /api/demo/custom` | Validate fault type, location and severity; simulate custom readings and curves |
| `POST /api/compare-series` | Projection for a tested manual-input candidate; visualization only |
| `POST /api/investigations` | Validate readings and start an asynchronous in-memory session |
| `GET /api/investigations/{id}/events` | SSE graph progress and completion |
| `GET /api/investigations/{id}` | Final report/status |
| `POST /api/investigate` | Synchronous alternative for scripts and tests |

The session dictionary is in memory with a two-worker server pool; it is not durable or shared across processes. A real deployment needs a persistent queue/results store, sensor authentication, monitoring, hydraulic calibration and retention controls. The available `Net2` model has **no bundled catalog**; build one before investigating it. The web interface is deliberately wired to Net1.

## 7. Install, run and demonstrate

Use Python 3.11 or later from the `waterguard/` directory. Check `python3 --version` first; older Python versions can fail while building WNTR's NumPy dependency:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m backend.fingerprints Net1
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The generated catalog is bundled with the project, but rebuilding ensures its WNTR version matches the installed one. `python -m backend.fingerprints Net2` is optional for backend Net2 experiments. Do not open `index.html` directly from disk: it fetches `/api/...` from the server. No `npm install` step is needed.

**Suggested demo:** load Pipe leak → inspect all junction and pipe profile curves plus J-12 and P-11 map readings → click Investigate → show the timeline's shortlisted causes and fresh verification count → compare healthy/observed/tested values → reveal hidden ground truth → review the operator's checks. Repeat for Pipe restriction and Demand surge. For a sparse/ambiguous example, enter one junction pressure manually and show that the system requests more evidence instead of confidently naming a pipe. The fault location shown on a diagram is a **model location**, not field coordinates.

Run checks from the repository root:

```bash
python -m pytest -q
python evaluate.py
node --check frontend/app.js
node tests/ui-smoke.cjs
```

At the current implementation, the 18 backend tests and the JavaScript control smoke test pass. In `evaluate.py`, ten hidden severity-0.55 cases use four leaks, three restrictions and three demand surges. Clean: exact type + location is top one in **10/10**, top two **10/10**, distinct **9/10**. With seeded Gaussian noise (pressure standard deviation 0.02 m, flow 0.00003 m³/s): top one **9/10**, top two **10/10**, distinct **9/10**, averaging **4.8 online verification runs**. One noisy surge ranks second and remains ambiguous. These cases are selected Net1 locations with full synthetic sensor coverage and shared simulator assumptions. They establish the demo's regression behavior, **not real-world fault localization accuracy**.

## 8. Limits and next experiments

- **Sensor coverage and quality:** Sparse or biased sensors can leave causes indistinguishable. Test realistic sparse layouts and missing data; calibrate pressure/flow scales from historical data.
- **Model mismatch:** Net1 supplies topology and parameters. A real network needs GIS/model alignment, pump and valve statuses, time-aligned demand, calibrated roughness and baseline updating.
- **Fault diversity:** Current search includes one modeled fault at a time. Multiple simultaneous faults, sensor drift, pump faults and unmodeled causes need separate hypotheses and rejection rules.
- **Time-series inference:** The main plots compare locations at one time. A separate 12-hour WNTR projection endpoint exists, but diagnosis does not score time histories. To do that, accept timestamped measurements, align operating conditions, and score multiple steps.
- **Scale and latency:** A large utility should partition by pressure zone, first detect affected region from telemetry, retrieve an indexed local shortlist, simulate a small candidate set with cached/calibrated models, and use a persistent job queue. Network-wide 99-row Net1 timings cannot be extrapolated to thousands of pipes.
- **Uncertainty:** Display weights are not Bayesian probabilities. Estimate calibrated confidence on held-out networks and fault families, not just severity 0.55 from the same simulator.

### External tracing and local context

The UI always has a local in-memory SSE step log. To enable optional external traces, install optional packages, create `.env` from `.env.example`, and enter keys you obtained from the services:

```bash
python -m pip install -r requirements-optional.txt
cp .env.example .env
# Edit .env: LANGSMITH_TRACING=true, LANGSMITH_API_KEY=..., LANGSMITH_PROJECT=waterguard
# Optionally: AGENTOPS_API_KEY=...
# Then restart WaterGuard and run an investigation.
```

LangSmith records LangGraph execution when tracing is configured. AgentOps wraps the workflow and its named node operations. The page reports only whether these are configured locally; check the service dashboards for actual delivery. Do not put keys in the browser or commit `.env`. External traces may contain graph state and synthetic sensor readings. Without keys, the local timeline still shows stages, shortlist candidates, verification batches, scores and elapsed times. The optional LLM is off unless `WATERGUARD_USE_LLM=1` and `OPENAI_API_KEY` are set; it only paraphrases evidence and cannot select fault locations or change numeric scores.

Run the optional install from the activated Python 3.11+ `.venv` used to run WaterGuard. On macOS, `python3` may still point to an older system Python; in that case, install Python 3.11 or newer and create the environment with its versioned command (for example, `python3.11 -m venv .venv`). The startup script prefers an available Python 3.11+ interpreter and stops with a clear message if an existing `.venv` is too old.

You can enter known operational context without inventing sensor evidence:

```bash
python manage_context.py add-maintenance --pipe 21 --planned --note "Demo planned valve inspection"
python manage_context.py add-demand --junction 12 --multiplier 1.2 --note "Demo schedule"
python manage_context.py show
```

These are labeled local records in SQLite. Maintenance can change the wording of a restriction recommendation. Demand schedule and weather are shown as context counts but do not alter the current hydraulic baseline or candidate ranking. If you have no genuine records, leave the tables empty and say so. The hypothesis engine works anyway: its inputs are the network model, 99 offline signatures and the sensor readings. For deployment, add timestamped and sourced maintenance, operating state, demand, sensor calibration and a durable trace store, then measure their effect on held-out real events.

### A critical repair in the uploaded `pro.zip`

That copy's `hydraulics.py` used a synthetic network and returned catalog interpolation as if it were a fresh simulation; its isolation and 12-hour outputs were also derived from stored numbers. This version restores actual WNTR model loading and fault injection, rejects a catalog built under a different WNTR version, and includes a test that would fail if interpolation replaced live verification. Rebuild the catalog after changing WNTR or hydraulic code. The bundled catalog was regenerated with WNTR 1.5.0.

This version is an explainable **local synthetic decision-support demonstration**. Its strongest claim is that it makes its hypotheses, simulator calls, evidence, ambiguity and human recommendations visible and testable.
