# CredResolve SmartDialer - Architecture & State Machine Diagrams

## 1. System Pipeline Architecture

```mermaid
flowchart TD
    subgraph Campaign Management
        CAMP[Campaign Model<br/>Progressive / Predictive]
    end

    subgraph Pacing Layer
        CAMP -->|Campaign Metrics & Queue| PACE{Pacing Engine}
        PACE -->|Progressive: 1:1 Slots| PROP_PROG[Proposal: N Dials]
        PACE -->|Predictive: Little's Law / Erlang| PROP_PRED[Proposal: N Dials]
    end

    subgraph Safety Boundary
        PROP_PROG --> SC[Independent Safety Controller]
        PROP_PRED --> SC
        SC -->|Checks: Abandon Rate &lt; 3%, Agent Pool, Circuit Breaker| DECISION{Safety Decision}
        DECISION -->|Approve| ALLOW[Allowed Dials]
        DECISION -->|Reduce / Clamp| CLAMP[Clamped Dials]
        DECISION -->|Fallback| FALLBACK[1:1 Progressive Fallback]
        DECISION -->|Reject| DROP[0 Calls / Abort]
    end

    subgraph Atomic Allocation Layer
        ALLOW --> ALLOC[Atomic Call Allocator]
        CLAMP --> ALLOC
        FALLBACK --> ALLOC
        ALLOC -->|Database Atomic CAS| AGENT_DB[(Agent Table)]
        ALLOC -->|Database Priority Lock| BORROWER_DB[(Borrower Table)]
    end

    subgraph Telecom Provider Integration
        ALLOC --> CB{Provider Circuit Breaker}
        CB -->|CLOSED / HALF-OPEN| PROV[Telecom Provider Interface]
        CB -->|OPEN| FAIL[Immediate Circuit Reject]
        PROV --> PROV_A[Provider A<br/>Fast, Reliable]
        PROV --> PROV_B[Provider B<br/>Slow, Duplicates, Out-of-Order]
    end

    subgraph Ingestion & Recovery
        PROV_A -.->|Webhook Events| EP[Idempotent Event Processor]
        PROV_B -.->|Webhook Events| EP
        EP -->|Deduplication & Monotonicity| CALL_DB[(Call & Event Log DB)]
        REC[State Reconciler] -.->|Periodic Audit| CALL_DB
        REC -.->|Clear Stale Reservations| AGENT_DB
    end
```

---

## 2. Agent State Machine

```mermaid
stateDiagram-v2
    [*] --> OFFLINE
    OFFLINE --> AVAILABLE: Login / Go Online
    OFFLINE --> PAUSED: Login on Pause

    AVAILABLE --> RESERVED: Atomic CAS Allocation
    AVAILABLE --> PAUSED: Agent Break
    AVAILABLE --> OFFLINE: Logout

    RESERVED --> DIALING: Call Initiated with Provider
    RESERVED --> AVAILABLE: Reservation Timeout / Cancellation
    RESERVED --> OFFLINE: Network Drop

    DIALING --> CONNECTED: Borrower Answered & Bridged
    DIALING --> AVAILABLE: Busy / No Answer / Failed
    DIALING --> WRAP_UP: Fast Disposition
    DIALING --> OFFLINE: Disconnect

    CONNECTED --> WRAP_UP: Call Terminated
    CONNECTED --> AVAILABLE: Immediate Release
    CONNECTED --> OFFLINE: Crash / Drop

    WRAP_UP --> AVAILABLE: Disposition Saved
    WRAP_UP --> PAUSED: Take Break
    WRAP_UP --> OFFLINE: Shift Ended

    PAUSED --> AVAILABLE: Unpause
    PAUSED --> OFFLINE: Logout
```

---

## 3. Call State Machine & Monotonic Progression

```mermaid
stateDiagram-v2
    [*] --> QUEUED: Created in Batch
    QUEUED --> RESERVED: Agent & Borrower Bound
    QUEUED --> CANCELLED: Campaign Stopped

    RESERVED --> INITIATED: Dispatched to Provider API
    RESERVED --> FAILED: Provider Rejected Request
    RESERVED --> CANCELLED: Reservation Aborted

    INITIATED --> RINGING: Provider Ringing Callback
    INITIATED --> ANSWERED: Fast Pickup (Skipped Ring)
    INITIATED --> COMPLETED: Fast Complete
    INITIATED --> FAILED: Fast Fail / Network Error
    INITIATED --> CANCELLED: Timeout Cancel

    RINGING --> ANSWERED: Borrower Answered
    RINGING --> FAILED: Line Busy / No Answer
    RINGING --> CANCELLED: Abandoned / Timeout

    ANSWERED --> CONNECTED: Audio Stream Bridged to Agent
    ANSWERED --> COMPLETED: Immediate Disconnect
    ANSWERED --> FAILED: Early Drop
    ANSWERED --> CANCELLED: Abandoned (No Free Agent)

    CONNECTED --> COMPLETED: Normal Call Conclusion
    CONNECTED --> FAILED: Mid-call Network Drop

    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]

    note right of COMPLETED
        TERMINAL SINK:
        Late-arriving RINGING or ANSWERED
        callbacks are safely ignored via
        EventProcessor Monotonic Rank check.
    end note
```

---

## 4. Crash Recovery & Stale State Reconciliation

```mermaid
sequenceDiagram
    autonumber
    participant W1 as Worker 1 (Active)
    participant DB as Central Database
    participant REC as State Reconciler
    participant AGT as Agent 101

    W1->>DB: Atomic CAS: Reserve Agent 101 (status=AVAILABLE -> RESERVED)
    DB-->>W1: 1 Row Updated (Worker 1 holds reservation)
    W1->>DB: Atomic CAS: Reserve Borrower (status=PENDING -> RESERVED)
    DB-->>W1: 1 Row Updated
    Note over W1: WORKER 1 PROCESS CRASHES SUDDENLY!
    Note over AGT,DB: Agent 101 stuck in RESERVED with no active call

    REC->>DB: Periodic Sweep: SELECT * FROM agents WHERE status='RESERVED' AND reserved_at &lt; NOW() - 15s
    DB-->>REC: Returns Agent 101
    REC->>DB: UPDATE agents SET status='AVAILABLE', reserved_by=NULL WHERE id=101
    REC->>DB: UPDATE borrowers SET status='PENDING' WHERE id=borrower_id
    Note over REC,DB: System recovered cleanly; zero leaked locks or orphaned resources!
```
