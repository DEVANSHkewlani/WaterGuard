# WaterGuard

WaterGuard is a local FastAPI dashboard for building a simple water-network model, running WNTR simulations, and investigating pressure and flow differences with a bounded LangGraph workflow. Net1 and Net2 remain available as demonstrations.

## Run

Use Python 3.11 or newer. Install Poppler so `pdfinfo` is available for PDF page validation; `pdftotext` adds text extraction and `pdftoppm` is needed only for optional AI vision proposals. Start with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

On macOS, `./start.command` also installs dependencies, prepares Net1 signatures if needed, and starts the server. Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

Run checks with:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest -q
node tests/ui-smoke.cjs
```

## Set up a household network

1. Choose **Set Up My Water Network** on the dashboard.
2. Upload a PDF/PNG/JPG drawing, draw the network, or answer the guided questions. A diagram is retained locally. Without explicit opt-in and a configured vision model, trace its points and connections yourself. PDF text may be extracted for reference, but text alone is never treated as pipe connectivity.
3. Review the map. Add a supply, tank, branch, and outlets as needed. Choose **Connect two points** to add each pipe, pump, or valve. Click a line to correct its endpoints and dimensions. Click a point to rename it and enter supply head, elevation, tank dimensions, or expected use. Unknowns are shown explicitly; undo and reset are available.
4. Choose **Check my network**. A strict hydraulic model needs its critical measurements. Select **Use estimated values for an exploratory demo** if you do not know them; the page lists the values it will use. A model with unresolved connectivity or a failed hydraulic run cannot continue.
5. After the healthy baseline and fault catalog finish, the new network becomes active in the existing dashboard. Choose **Test my network** to simulate a fault and run the real investigator. The simulated cause is shown after the report. You can instead enter timestamped actual measurements, or register sensors for a clearly labeled simulated stream.

Pressure is entered in metres of water head; pipe flow is entered in L/s. Reports include the model version fingerprint, source of readings, baseline limitation, competing hypotheses, evidence, simulation count, audit history, and a JSON download. Source drawings, versions, catalogs, sensor settings, and investigation history can be removed with **Delete this network and its data**.

## Limits and privacy

The estimated mode uses explicit demo values (for example 30 m supply head, 10 m pipe length, 20 mm pipe diameter, and 0.1 L/s per outlet). These are assumptions, not measurements. Pipe roughness is estimated from material or a generic value. A successful structural and hydraulic check is **not calibration**. The healthy baseline is one hour-zero model snapshot, so changing demand, pump state, or tank level can resemble an anomaly. A ranked location is a hypothesis requiring field verification. Household water-loss estimates are withheld until real calibration exists. No valves or pumps are controlled.

A drawing is sent to an external AI provider only if the user checks the consent box and both `OPENAI_API_KEY` and `WATERGUARD_VISION_MODEL` are configured on the server. AI proposals require manual correction. Without these settings, upload leads to the manual tracing workflow. The simulated sensor stream is generated from WNTR; no live device adapter is currently connected. Real sensor observations are not accepted as such through the API. Local browser cookies isolate uploaded models and investigation sessions on this single-user server; deploy with authentication and secure storage before multi-user use. Optional LangSmith/AgentOps tracing is configured separately and may send investigation metadata to those services.

The catalog is cached by model bytes, WNTR version, fault types and severity grid. Published household model versions are immutable, and catalog generation has a 300-scenario budget. Editing a source draft and validating it again creates a new version.
