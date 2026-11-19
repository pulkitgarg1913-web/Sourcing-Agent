# Conversational Parts Search Agent (prototype)

A mechanic says *"Find an oil filter for a Tata Harrier"*. Gemini extracts the request, then Boodmo and
AutoDukan advance independently and concurrently through their own search and vehicle forms. The
graph shares mechanic-provided vehicle facts, while each adapter validates those facts against its own
options and keeps its category and selected variant separate. Boodmo can verify compatibility through its
fitment filter. AutoDukan results remain unverified because exact product compatibility is not independently confirmed.

This extends the *Hybrid Vendor Search Architecture* in `docs/`. It is the "approved browser workflow" adapter
plus the LangGraph "ask missing questions" step, moved to the point where the site actually asks.

## Repository Structure

- **`backend/`**: Core AI sourcing agent, FastAPI server, browser automation adapters, LangGraph pipeline, and test suites.
- **`frontend/`**: React 18 + Vite + Tailwind CSS dashboard with real-time SSE streaming, voice recording, and interactive procurement cards.
- **`docs/`**: Comprehensive architecture guides, workflow specifications, and onboarding documents.

## Run it

### Backend Setup

```bash
cd backend
pip3 install -r requirements.txt
python3 -m playwright install chromium
```

`.env` needs `GEMINI_API_KEY` and `GEMINI_MODEL` (configured in `backend/.env` or project root).

```bash
# Run CLI Agent (from backend directory)
python3 run_agent.py                      # type the request and answers
python3 run_agent.py --voice              # speak; it talks back (hands-free, stops on silence)
python3 run_agent.py --show-browser       # watch Chromium drive all suppliers
python3 run_agent.py --say "Harrier ka oil filter chahiye, 2021 diesel automatic" --answers "teesra"

# Run FastAPI Server (from backend directory)
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# Run Tests (from backend directory)
python3 -m unittest discover tests

# Run Performance Monitoring & Latency Benchmark Tests
python3 -m unittest backend/tests/test_performance_monitoring.py
python3 -m unittest backend/tests/test_postgres_database.py

# Run Standalone Performance Benchmark Runner
python3 backend/scripts/benchmark_runner.py --concurrency 1 --queries 7 --mode sequential
python3 backend/scripts/benchmark_runner.py --concurrency 5 --queries 7 --mode concurrent

# Connect to Database via psql
psql -h /tmp -U postgres -d parts_sourcing
# or: psql "postgresql://postgres@/parts_sourcing?host=/tmp"

# Migrate SQLite records to PostgreSQL
python3 backend/scripts/migrate_to_postgres.py
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev                               # opens Vite dev server at http://localhost:5173
```

### Performance Monitoring & Analytics Dashboard

Access the SaaS performance monitoring dashboard by clicking the **"Performance Analytics"** tab in the top navigation bar at `http://localhost:5173`.

Features:
- **KPI Overview Cards**: Total searches, success rate, average latency, P50/P95/P99 latencies, and discovery rate.
- **Latency Distribution & Trends**: Percentile calculations using NIST Type 7 linear interpolation, histogram distribution, and SVG historical trends.
- **10-Stage Component Tracking**: Granular timing across LLM parsing, supplier search wall-clock, HTML rendering, product extraction, and verification.
- **Supplier-Wise Comparison**: Dynamic performance matrix across Boodmo, AutoDukan, and future suppliers.
- **Search Quality & Extraction Completeness**: Field completeness tracking across 7 core attributes (title, price, part#, brand, compatibility, stock, URL) vs. verified fitment coverage.
- **Searchable Request History**: Filterable request log with stage-level inspection modal.
- **Integrated Load Testing**: Configurable concurrency benchmarks (1, 5, 10, 20) with realistic automotive queries.

For full architectural details, database schema, API documentation, and calculation methodologies, see [docs/PERFORMANCE_MONITORING.md](docs/PERFORMANCE_MONITORING.md).

### Production Deployment (Vercel + Container Backend)

For step-by-step instructions on deploying the React dashboard to **Vercel** and the FastAPI/Playwright agent to a persistent container (Render/Railway/Docker), see [docs/DEPLOYMENT_VERCEL.md](docs/DEPLOYMENT_VERCEL.md).

Example (real output, trimmed):

```
Mechanic: Harrier ka oil filter chahiye
Agent:    Okay, looking for an oil filter for a Tata Harrier on Boodmo and AutoDukan.
Agent:    Which year is the Harrier? Boodmo lists 2019 to 2026.
Mechanic: 2021
Agent:    Boodmo lists 16 Harrier 2021 variants. Is it automatic or manual?
Mechanic: XZ plus                      <- STT mishearing of "XZA plus", said out of turn
Agent:    Just to check: is it XZ plus?
Mechanic: no, XZA plus
Agent:    There are 3 versions. Which one is it: 1, automatic; 2, manual, kyrotec turbocharged; 3, manual?
Mechanic: automatic
Agent:    Got it, the Harrier 2.0L XZA plus, automatic, diesel. Checking oil filter prices and stock across all suppliers.
Agent:    For the Tata Harrier 2021, 2.0L XZA+ AT/Diesel/BS6, Boodmo lists 6 parts under Oil Filter that fit
          this variant. Cheapest in stock: Tata Filtering Element H5, part KLP103969AA, ₹972 from seller
          MSMEE/MTC/1, 9 in stock, dispatch in 2 days. ... Nothing has been ordered.
```

Each run writes an audit record (request, every question and answer, results) to `runs/`.

## How it works

```
understand ──► navigate ──(a site needs input)──► [pause] interpret ──► navigate ...
  (Gemini        (Boodmo and AutoDukan walk             (route answer to the
   parses the     independently; each validates         requesting adapter;
   request)       its own options)                       confirm if inferred)
                         └──(all ready)──► collect ──► present
                                      (parallel supplier searches)
```

| File | Role |
|---|---|
| `backend/server.py` | FastAPI REST & SSE backend serving real-time event streaming and dashboard APIs |
| `backend/run_agent.py` | CLI for interactive terminal runs (text or voice mode) |
| `backend/parts_agent/boodmo.py` | Browser adapter: category lookup, vehicle selector, listings, seller/stock from product pages (4 tabs in parallel) |
| `backend/parts_agent/auto_dukan.py` | Browser adapter: AutoDukan category tree, vehicle selector, product extraction, and detail enrichment (3 pages in parallel) |
| `backend/parts_agent/variants.py` | Breaks variant labels like `2.0L XZA+ AT/Diesel/BS6` into attributes and picks the next question |
| `backend/parts_agent/graph.py` | LangGraph loop, independent supplier profiles and concurrent navigation/search, summary, audit record |
| `backend/parts_agent/llm.py` | Gemini request parsing and answer matching, with async wrappers and a model fallback chain |
| `backend/parts_agent/voice.py` | Gemini TTS (rotating models, `spd-say` fallback), mic capture with silence detection, Gemini transcription |

For the current voice flow and the steps to connect it to a phone call, see [Voice and phone call integration](docs/VOICE_AND_PHONE_CALL_INTEGRATION.md).

For the app architecture, conversation flow diagrams, and current/future vendor adapter design, see [App Architecture and Vendor Adapter Design](docs/APP_ARCHITECTURE_AND_ADAPTER_DESIGN.md).

### Conversation design choices

- **Ask only what the site needs.** Questions come from the live dropdowns. Details given up front ("2021
  diesel automatic") are matched against the site's values and used without asking again.
- **Don't read out 20 variants.** Labels are split into fuel, transmission, engine, trim and version tags.
  The agent asks the easiest question that meaningfully narrows the list. For example, "Is it automatic or
  manual?" comes before trim, and version tags like "Kyrotec" or "BS6 phase 2" come last. It reads out choices
  only once 4 or fewer are left, naming only what differs between them.
- **Verify, don't assume.** Anything the model *infers* is confirmed before use. That covers "XZA plus" given
  as an answer to "manual or automatic?", and a trim said out of turn that speech-to-text may have misheard.
- **Forgiving input.** Ordinals ("teesra wala", "second one"), Hinglish, typos ("Hariar" → *Did you mean…*),
  "don't know" (moves on to a different attribute, or explains where to find the year), "cancel".
- **Honest results.** "In stock" means a seller offer was seen on the product page. Accessory parts (e.g. "Oil
  Filter Support Assy") are flagged as related. Masked part numbers are kept masked unless the product URL
  confirms them. Distance isn't ranked, because Boodmo doesn't give seller addresses. Some seller codes do
  contain a city ("Delhi/TKL", "Jaipur/SPF"), which a later distance step could use.

## Findings from building it

- **Boodmo specifics.** The variant list depends on the category page *and* refreshes asynchronously after a
  year is picked. After a variant is chosen, the dropdown rewrites its labels (the engine code is dropped), so
  the adapter tracks the selection by URL. Before that fix, re-selecting the variant crashed on the Swift. Reading it too early returned 55 variants instead of 20, so the adapter waits until the
  dropdown is stable. Some variant labels appear twice with different catalogue IDs. The adapter checks both
  and marks parts listed under only one of them as partial fits.
- **Boodmo's JSON API** (`/api/v1/customer/api/catalog/...`) rejects direct calls without the app's own
  headers. The adapter reads rendered pages only, in line with the "don't bypass access controls" rule (§6,
  §11 of the architecture doc). Boodmo's terms for automated access still need a formal check before
  production use.
- **Gemini free tier is a real limit for voice.** TTS allows 3 requests/minute and, for some models, 10/day
  per model. Today `gemini-3.8-flash` also returned 503/504 under load. The code falls back
  (`gemini-3.5-flash-lite` for text and transcription, four TTS models, then `spd-say`), but a paid key is
  needed for real use.
- **Speech-to-text can reply instead of transcribe.** Flash-lite once answered the audio ("…yeh raha oil
  filter…"). Transcription now uses a strict system instruction, and a transcript too long for the audio is
  rejected and retried on another model.

## Not done yet

- AutoDukan results are keyword matches and do not prove vehicle compatibility. Confirm fitment before purchase.
  Each supplier's result and fitment evidence is reported separately.
- Turn-based voice (about 4 s of TTS per reply). `gemini-3.8-live` (bidirectional audio) would cut latency and
  allow barge-in.
- No price/distance ranking module and no order flow. Results end with "Nothing has been ordered", as the
  ticket requires.
- The variant label parser has been tried on Tata (Harrier) and Maruti (Swift: VXI/ZXI, AGS). Other makes
  (Hyundai "SX(O)", Mahindra) are untested.


