# CredResolve SmartDialer

A robust, backend-first **SmartDialer** prototype built for high concurrency, zero-abandon safety compliance, and fault tolerance across volatile telecom networks.

---

## 🎯 Architecture & Pipeline

```
Campaign Management 
   -> Pacing Engine (Progressive / Predictive) 
   -> Independent Safety Controller 
   -> Atomic Call Allocator 
   -> Telecom Provider Interface
```

### Core Invariants:
1. **Isolated Safety Controller**: The pacing algorithm can propose calls, but the Safety Controller has sole authority to `APPROVE`, `REDUCE`, `REJECT`, or force `FALLBACK_PROGRESSIVE`. The dialer algorithm cannot disable or bypass safety.
2. **Safe Atomic Allocation**: Multi-worker concurrency uses atomic Compare-And-Swap (CAS) at the database layer (`UPDATE ... WHERE status='AVAILABLE'`). Stale in-memory counters or cache values can never cause double-reservation.
3. **Strict Monotonic State Machines**: Event processing is idempotent. Out-of-order or duplicate telecom events (e.g. `COMPLETED` followed by late `ANSWERED` or `RINGING`) can never regress call or agent state.
4. **Self-Healing Recovery**: Background `StateReconciler` detects crashed workers, expired reservations, and hanging initiated calls without manual intervention.

---

## 📂 Repository Structure

```
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI endpoints & live monitoring dashboard
│   │   ├── models/              # SQLAlchemy models (Agent, Borrower, Call, EventLog, Campaign)
│   │   ├── state_machines/      # Validated Agent & Call lifecycle state machines
│   │   ├── safety/              # Independent Safety Controller
│   │   ├── pacing/              # Progressive (1:1) and Predictive pacing engines
│   │   ├── allocation/          # Atomic database CAS allocator
│   │   ├── providers/           # Provider A, Provider B, and Circuit Breaker
│   │   ├── events/              # Idempotent deduplicating event processor
│   │   ├── recovery/            # Crash reconciler & stale reservation recovery
│   │   └── engine/              # Dialer Worker cycle orchestrator
│   └── tests/                   # 27 automated tests covering all failure & edge cases
├── scripts/
│   ├── simulate.py              # Multi-scenario simulator (Scenarios A, B, C, D)
│   └── load_test.py             # Concurrent multi-worker load test
├── docs/
│   ├── PROGRESS.md              # Requirements checklist & verification log
│   ├── ARCHITECTURE.md          # Architecture decisions & technical Q&A
│   └── DIAGRAMS.md              # Mermaid flowcharts & state diagrams
└── README.md
```

---

## 🚀 Quickstart & Setup

The virtual environment and dependencies are already configured in `backend/.venv`.

### 1. Run Automated Test Suite (27 Tests)
```powershell
& ".\backend\.venv\Scripts\pytest.exe" -v
```
All tests execute against an isolated test database in seconds.

### 2. Run Multi-Scenario Simulation (Scenarios A, B, C, D)
```powershell
& ".\backend\.venv\Scripts\python.exe" scripts/simulate.py
```
This runs:
- **Scenario A**: Low Answer Rate (20%), 120s Talk Time
- **Scenario B**: Moderate Answer Rate (50%), 90s Talk Time
- **Scenario C**: High Answer Rate (70%), 180s Talk Time
- **Scenario D**: Volatile Dynamic Conditions + Provider B Latency & Failures

### 3. Run Multi-Worker Concurrency Load Test
```powershell
& ".\backend\.venv\Scripts\python.exe" scripts/load_test.py
```
Spins up 6 concurrent worker threads competing for the exact same agent/borrower pool and verifies zero duplicate agent clashes and zero duplicate borrower calls.

### 4. Launch Live Server & Dashboard
```powershell
& ".\backend\.venv\Scripts\uvicorn.exe" app.api.main:app --app-dir backend --reload --port 8000
```
Open your browser at **`http://localhost:8000`** to access:
- Live Agent status monitor
- Campaign controls (trigger dial cycles, toggle Progressive/Predictive mode)
- Real-time Safety Controller audit log
- State reconciler controls
- Interactive API documentation at **`http://localhost:8000/docs`**

---

## 🧪 What We Tested & Verified

| Test Suite | File | What is Verified |
| :--- | :--- | :--- |
| **Agent State Machine** | `test_state_machines.py` | All legal transitions, rejects illegal transitions (e.g. OFFLINE -> CONNECTED). |
| **Call State Machine** | `test_state_machines.py` | Monotonic rank ordering, terminal lock on COMPLETED/FAILED/CANCELLED. |
| **Atomic Allocation** | `test_allocation.py` | Two workers racing for same agent; batch CAS reservations. |
| **Pacing Engines** | `test_pacing.py` | Progressive 1:1 slot calculations; Predictive Little's Law formulas. |
| **Safety Controller** | `test_safety_controller.py` | Rejections on provider outage; clamp on overdial; emergency fallback on high abandon. |
| **Telecom Providers** | `test_providers.py` | Provider A reliability, Provider B noisy events, Circuit Breaker trip & half-open recovery. |
| **Event Idempotency** | `test_idempotency.py` | Repeated duplicate callbacks (`ANSWERED` x 3), out-of-order callbacks (`COMPLETED` then late `ANSWERED`). |
| **Crash Recovery** | `test_recovery.py` | Worker crashes after reservation; hanging initiated call timeout. |
| **End-to-End** | `test_end_to_end.py` | Full multi-agent, multi-call dialing cycles through to completion. |
| **REST API** | `test_api.py` | API endpoints, stats aggregation, campaign controls, webhook ingestion. |

---

## 📖 Key Documentation
- [Architecture Decision Record & Interview Questions](docs/ARCHITECTURE.md)
- [System Architecture & State Machine Diagrams](docs/DIAGRAMS.md)
- [Progress & Implementation Tracking](docs/PROGRESS.md)
