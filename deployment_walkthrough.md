# 🎬 Alfaleus Portfolio Showcase & Recording Blueprint

This guide outlines the precise steps, scripts, and commands required to execute a flawless 3-minute video presentation demonstrating the engineering depth, containerized architecture, and diagnostic telemetry of the Alfaleus aggregation engine.

---

## 🛠️ Step 1: Clean-Slate Docker Environment Setup

Start your video with a clean terminal screen. Run these commands to demonstrate environment parity and containerization reliability:

```bash
# 1. Bring down all active containers and clean named volumes
docker compose down -v

# 2. Rebuild and launch the service mesh in detached background mode
docker compose up --build -d
```

> [!TIP]
> This single-command setup proves to evaluators that the entire system (FastAPI, MongoDB, Streamlit) is fully isolated and boots seamlessly without local machine dependencies.

---

## 🎙️ Step 2: The 3-Minute Presentation Script

### ⏱️ Phase 1: The Hook (0:00 - 0:30)
- **Visual**: Show the running Streamlit dashboard filled with opportunity records.
- **Narrative**: 
  > "Hello! Today I'm showcasing Alfaleus: a containerized, resilient data analytics and ingestion pipeline. It aggregates, deduplicates, and profiles startup opportunities. The entire platform runs in an isolated Docker mesh network using local MongoDB and custom AI circuit-breaker integration."

---

### 💻 Phase 2: Deep Engineering & Code Walkthrough (0:30 - 1:30)
- **Visual**: Switch to your IDE (VS Code / Cursor) and walk through three critical code blocks.
- **Narrative**:
  - **1. Text Analytics & Profile Calculations (`utils.py`)**:
    > "First, we look at [utils.py](file:///c:/0001_Project/Alfaleus/utils.py). To enable deep diagnostic analysis, the ingestion scraper calculates raw character counts, word counts, and structural HTML-to-text density ratios *prior* to tag stripping, preserving layout metadata."
  - **2. 3-Tier Deduplication (`dedup.py` & `scrapers.py`)**:
    > "In the scrapers, we leverage a high-performance 3-tier deduplication engine: URL checking, deterministic SHA-256 content hashing, and token-based fuzzy string matching, preventing redundant operations."
  - **3. Resilient Quota-Aware LLM Circuit Breaker (`ai_enrichment.py`)**:
    > "In [ai_enrichment.py](file:///c:/0001_Project/Alfaleus/ai_enrichment.py), the enrichment pipeline features a circuit breaker. If Google Gemini hits API quota limits or returns 429 errors, the scheduler dynamically intercepts the request and reroutes the payload to Groq (Llama-3.1) instantly, ensuring zero-downtime tagging."

---

### 📊 Phase 3: Advanced Dashboard Analytics (1:30 - 2:30)
- **Visual**: Switch back to the Streamlit UI browser.
- **Narrative**:
  - **Dynamic Scoring Demo**:
    > "In the sidebar, we can customize analytical weights for Urgency, Stage, and Complexity. As I adjust these sliders, the vectorized Pandas engine dynamically computes a custom 'Opportunity Priority Score' on-the-fly, instantly re-ranking the control room records."
  - **Diagnostic & Velocity Telemetry**:
    > "If we transition to the Ingestion Analytics & Ingestion Velocity tabs, we see Plotly visual charts tracking processing throughput (items processed per second) over time, and scatter plots correlating raw HTML complexity against AI stage outputs."

---

### 🏁 Phase 4: Verification & Close (2:30 - 3:00)
- **Visual**: Bring up your terminal bubble inside the IDE and execute the pytest suite.
- **Narrative**:
  ```bash
  # Execute pytest in system environment
  venv\Scripts\python -m pytest
  ```
  > "As you can see, the test suite executes with a 100% success rate across all 29 tests—validating schemas, analytics equations, and API endpoints. The system is fully containerized, robustly validated, and production-ready. Thank you!"

---

## 📷 Recording Checklist & Visual Specs

- [ ] **Webcam corner bubble**: Keep active throughout to showcase personality and professional communication.
- [ ] **External Microphone**: Eliminate echoes or host machine fan noise for pristine sound clarity.
- [ ] **Data Ready**: Ensure MongoDB has ingested opportunities beforehand, so charts load instantly when demoing.
