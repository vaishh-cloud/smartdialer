"""
CredResolve SmartDialer Failure Replay Lab.

Interactive and automated demonstration of failure detection, safety clamping,
state machine transitions, and automated resource recovery using the existing architecture:
1. Baseline: Normal dial cycle (Pacing -> Safety APPROVE -> Call Placement).
2. Failure 1: Worker crash mid-cycle, leaving Agent & Borrower stuck in RESERVED.
3. Safety Invariant: Concurrent dial cycle sees constrained capacity, Safety Controller
   clamps/reduces dials, and atomic CAS blocks double-reservation.
4. Failure 2: Telecom provider outage cascade trips Circuit Breaker (CLOSED -> OPEN);
   Safety Controller detects downstream outage and enforces immediate REJECT.
5. Self-Healing: Background StateReconciler sweeps stale reservations and hanging calls,
   recovering 100% of orphaned resources without human intervention.
6. Verification: Restored capacity allows subsequent dial cycles to succeed cleanly.
"""

import sys
import os
from pathlib import Path
import datetime
from dataclasses import dataclass

backend_path = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState
from app.providers.provider_a import ProviderA
from app.providers.circuit_breaker import ProviderCircuitBreaker, CircuitState
from app.safety.safety_controller import SafetyController
from app.engine.dialer_worker import DialerWorker
from app.events.event_processor import EventProcessor
from app.recovery.reconciler import StateReconciler


@dataclass
class FailureReplayLabResult:
    baseline_calls_placed: int
    circuit_breaker_opened: bool
    circuit_breaker_state_during_outage: str
    safety_decision_during_outage: str
    allowed_calls_during_outage: int
    stale_reservations_cleared: int
    agent_recovered_status: str
    borrower_recovered_status: str
    post_recovery_decision: str
    post_recovery_calls_placed: int
    post_recovery_agent_status: str


def print_banner(text: str):
    print(f"\n{'=' * 78}")
    print(f" {text}")
    print(f"{'=' * 78}")


def print_step(step_num: int, title: str):
    print(f"\n[STEP {step_num}] {title}")
    print("-" * 78)


def run_failure_replay_lab() -> FailureReplayLabResult:
    print_banner("CredResolve SmartDialer - Failure Replay Lab")
    print("Demonstrating simulated failures, safety decisions, state transitions, and recovery.")

    # 0. Setup isolated test environment
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Seed 3 agents and 10 borrowers
    campaign = Campaign(name="Failure Replay Campaign", mode="PROGRESSIVE")
    db.add(campaign)
    db.commit()

    agents = [
        Agent(name="Agent-Alice", status=AgentState.AVAILABLE.value),
        Agent(name="Agent-Bob", status=AgentState.AVAILABLE.value),
        Agent(name="Agent-Charlie", status=AgentState.AVAILABLE.value),
    ]
    db.add_all(agents)

    borrowers = [
        Borrower(name=f"Borrower-{i+1}", phone=f"+919800{i:04d}", priority=1)
        for i in range(10)
    ]
    db.add_all(borrowers)
    db.commit()

    provider = ProviderA(failure_rate=0.0, seed=123)
    circuit_breaker = ProviderCircuitBreaker(provider_name="ProviderA", failure_threshold=0.5, window_size=4)
    safety_controller = SafetyController(max_abandon_rate=0.03, max_overdial_ratio=2.0)
    reconciler = StateReconciler(db, stale_reservation_timeout_sec=5, stale_initiated_timeout_sec=10)

    # -------------------------------------------------------------------------
    # STEP 1: Baseline Normal Dial Cycle
    # -------------------------------------------------------------------------
    print_step(1, "Baseline Normal Dial Cycle")
    worker_1 = DialerWorker("worker-primary", db, provider, circuit_breaker, safety_controller)
    cycle_1 = worker_1.run_dial_cycle(campaign.id)

    print(f"Available Agents:   {cycle_1.available_agents}")
    print(f"Pacing Proposal:    {cycle_1.proposed_calls} calls proposed")
    print(f"Safety Decision:    {cycle_1.safety_decision} (Allowed: {cycle_1.allowed_calls})")
    print(f"Calls Dispatched:   {cycle_1.calls_placed}")

    # Invariant Assertions: Baseline normal operation
    assert cycle_1.available_agents == 3, f"Expected 3 available agents, got {cycle_1.available_agents}"
    assert cycle_1.safety_decision == "APPROVE", f"Expected APPROVE, got {cycle_1.safety_decision}"
    assert cycle_1.calls_placed == 3, f"Expected 3 calls placed, got {cycle_1.calls_placed}"

    # Complete the placed calls to restore baseline
    placed_calls = db.query(Call).filter(Call.status == CallState.INITIATED.value).all()
    processor = EventProcessor(db)
    for c in placed_calls:
        evts = provider.generate_events_for_call(c.id, c.provider_call_id or f"mock_{c.id}", answered=True, duration_sec=30)
        for evt in evts:
            processor.process_event(evt)

    # Return agents from WRAP_UP back to AVAILABLE
    for a in db.query(Agent).all():
        a.status = AgentState.AVAILABLE.value
    db.commit()
    print("Baseline result:    Calls completed monotonically, all 3 agents returned to AVAILABLE.")

    # -------------------------------------------------------------------------
    # STEP 2: Simulated Worker Crash (Orphaned Reservation)
    # -------------------------------------------------------------------------
    print_step(2, "Simulated Worker Crash During Reservation")
    # Simulate Worker-Alpha reserving Agent-Alice and Borrower-1, then immediately dying before dispatch
    alice = db.query(Agent).filter(Agent.name == "Agent-Alice").one()
    borrower_1 = db.query(Borrower).filter(Borrower.name == "Borrower-1").one()

    stale_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=20)
    alice.status = AgentState.RESERVED.value
    alice.reserved_by = "worker-crashed-pid-9941"
    alice.reserved_at = stale_time

    borrower_1.status = "RESERVED"
    borrower_1.reserved_by = "worker-crashed-pid-9941"
    borrower_1.reserved_at = stale_time
    db.commit()

    print("Simulated Event:    Worker 'worker-crashed-pid-9941' crashed mid-transaction.")
    print(f"State Transition:   Agent-Alice -> {alice.status} (Reserved by: {alice.reserved_by})")
    print(f"State Transition:   Borrower-1  -> {borrower_1.status} (Reserved by: {borrower_1.reserved_by})")
    print("Failure Impact:     1 agent slot and 1 borrower lead locked without an active call.")

    # Invariant Assertions: Stale reservations exist
    assert alice.status == AgentState.RESERVED.value, f"Expected Alice RESERVED, got {alice.status}"
    assert alice.reserved_by == "worker-crashed-pid-9941", f"Unexpected reserved_by: {alice.reserved_by}"
    assert borrower_1.status == "RESERVED", f"Expected Borrower-1 RESERVED, got {borrower_1.status}"
    assert borrower_1.reserved_by == "worker-crashed-pid-9941", f"Unexpected borrower reserved_by: {borrower_1.reserved_by}"

    # -------------------------------------------------------------------------
    # STEP 3: Safety Decision Under Constrained Capacity
    # -------------------------------------------------------------------------
    print_step(3, "Concurrent Worker & Safety Clamping")
    # Worker-2 executes a dial cycle. Only Bob and Charlie are available.
    worker_2 = DialerWorker("worker-survivor", db, provider, circuit_breaker, safety_controller)
    cycle_2 = worker_2.run_dial_cycle(campaign.id)

    print(f"Free Agents Seen:   {cycle_2.available_agents} (Bob, Charlie available; Alice locked)")
    print(f"Pacing Proposal:    {cycle_2.proposed_calls} dials proposed")
    print(f"Safety Decision:    {cycle_2.safety_decision} -> {cycle_2.allowed_calls} calls allowed")
    print(f"Safety Log Details: {cycle_2.details[1]}")
    print(f"Calls Placed:       {cycle_2.calls_placed}")

    # Invariant Assertions: Worker-2 can only see and dial available agents; Alice was NOT double-booked
    assert cycle_2.available_agents == 2, f"Expected 2 available agents, got {cycle_2.available_agents}"
    assert cycle_2.calls_placed == 2, f"Expected 2 calls placed, got {cycle_2.calls_placed}"
    db.refresh(alice)
    assert alice.status == AgentState.RESERVED.value, "Alice state was mutated during concurrent cycle"
    assert alice.reserved_by == "worker-crashed-pid-9941", "Alice was illegally claimed by worker-survivor"
    print("Safety Guarantee:   Atomic CAS prevented Worker-2 from touching Alice. No double-reservation.")

    # -------------------------------------------------------------------------
    # STEP 4: Telecom Outage & Circuit Breaker Trip
    # -------------------------------------------------------------------------
    print_step(4, "Simulated Telecom Outage & Circuit Breaker Protection")
    # Simulate downstream provider rejecting requests (e.g. 503 Bad Gateway / Network Timeout)
    print("Simulating 4 consecutive telecom dispatch failures...")
    for _ in range(4):
        circuit_breaker.record_result(False)

    cb_health = circuit_breaker.get_health()
    print(f"Circuit Breaker:    State={circuit_breaker.state.value} | Failure Rate={cb_health.recent_failure_rate:.0%}")

    # Invariant Assertions: Circuit Breaker opens after simulated failures
    assert circuit_breaker.state == CircuitState.OPEN, f"Expected CircuitState.OPEN, got {circuit_breaker.state}"
    assert not circuit_breaker.can_execute(), "Circuit breaker must reject execution while OPEN"
    assert cb_health.is_healthy is False, "Provider health must report unhealthy while circuit is OPEN"
    assert cb_health.recent_failure_rate >= 0.5, f"Expected failure rate >= 50%, got {cb_health.recent_failure_rate}"

    # Next dial cycle attempts to run while breaker is OPEN
    cycle_3 = worker_2.run_dial_cycle(campaign.id)
    print(f"Safety Decision:    {cycle_3.safety_decision}")
    print(f"Allowed Calls:      {cycle_3.allowed_calls} (Outbound dialing halted)")
    print(f"Safety Log Details: {cycle_3.details[1]}")

    # Invariant Assertions: Safety Controller rejects dialing while provider is unhealthy
    assert cycle_3.safety_decision == "REJECT", f"Expected safety decision REJECT, got {cycle_3.safety_decision}"
    assert cycle_3.allowed_calls == 0, f"Expected 0 allowed calls, got {cycle_3.allowed_calls}"
    assert cycle_3.calls_placed == 0, f"Expected 0 calls placed, got {cycle_3.calls_placed}"
    assert any("circuit breaker" in d.lower() or "unhealthy" in d.lower() for d in cycle_3.details), (
        f"Expected circuit breaker rejection detail, got: {cycle_3.details}"
    )
    print("Safety Invariant:   System refused to dial into a dead telecom provider; prevented burned leads.")

    # -------------------------------------------------------------------------
    # STEP 5: Automated State Reconciliation & Self-Healing
    # -------------------------------------------------------------------------
    print_step(5, "StateReconciler Recovery Pass")
    report = reconciler.reconcile_all()

    print(f"Reconciler Report:  Cleared {report.stale_reservations_cleared} stale reservations.")
    for detail in report.details:
        print(f"  -> {detail}")

    db.refresh(alice)
    db.refresh(borrower_1)
    print(f"Recovered State:    Agent-Alice status = {alice.status} (reserved_by = {alice.reserved_by})")
    print(f"Recovered State:    Borrower-1 status  = {borrower_1.status} (reserved_by = {borrower_1.reserved_by})")

    # Invariant Assertions: Stale agent and borrower reservations are fully recovered
    assert report.stale_reservations_cleared == 1, (
        f"Expected exactly 1 stale reservation cleared, got {report.stale_reservations_cleared}"
    )
    recovered_agent_status = alice.status
    recovered_borrower_status = borrower_1.status
    assert recovered_agent_status == AgentState.AVAILABLE.value, f"Expected Alice AVAILABLE, got {recovered_agent_status}"
    assert alice.reserved_by is None, f"Expected Alice reserved_by None, got {alice.reserved_by}"
    assert alice.reserved_at is None, f"Expected Alice reserved_at None, got {alice.reserved_at}"
    assert recovered_borrower_status == "PENDING", f"Expected Borrower-1 PENDING, got {recovered_borrower_status}"
    assert borrower_1.reserved_by is None, f"Expected Borrower-1 reserved_by None, got {borrower_1.reserved_by}"
    assert borrower_1.reserved_at is None, f"Expected Borrower-1 reserved_at None, got {borrower_1.reserved_at}"
    print("Recovery Result:    100% of leaked resources cleanly recovered back to pool.")

    # -------------------------------------------------------------------------
    # STEP 6: Circuit Breaker Reset & Verified Post-Recovery Dialing
    # -------------------------------------------------------------------------
    print_step(6, "Post-Recovery Verification")
    # Complete previous in-flight calls so agents return to AVAILABLE
    for c in db.query(Call).filter(Call.status.in_([CallState.INITIATED.value, CallState.RINGING.value])).all():
        evts = provider.generate_events_for_call(c.id, c.provider_call_id or f"mock_{c.id}", answered=True, duration_sec=15)
        for evt in evts:
            processor.process_event(evt)

    for a in db.query(Agent).filter(Agent.status == AgentState.WRAP_UP.value).all():
        a.status = AgentState.AVAILABLE.value
    db.commit()

    # Reset circuit breaker (simulating cooldown expiry and successful canary probe)
    circuit_breaker.state = CircuitState.HALF_OPEN
    circuit_breaker.record_result(True)  # Canary probe succeeds
    print(f"Circuit Breaker:    Transitioned through HALF_OPEN -> {circuit_breaker.state.value} (Downstream healthy).")

    # Invariant Assertions: Circuit Breaker is closed and executable
    assert circuit_breaker.state == CircuitState.CLOSED, f"Expected CircuitState.CLOSED, got {circuit_breaker.state}"
    assert circuit_breaker.can_execute(), "Circuit breaker must allow execution once CLOSED"

    cycle_4 = worker_2.run_dial_cycle(campaign.id)
    print(f"Available Agents:   {cycle_4.available_agents} (Alice and colleagues fully recovered)")
    print(f"Safety Decision:    {cycle_4.safety_decision}")
    print(f"Calls Placed:       {cycle_4.calls_placed}")

    db.refresh(alice)
    print(f"Post-Recovery:      Agent-Alice is now active in Call #{alice.current_call_id} (Status: {alice.status})")

    # Invariant Assertions: Subsequent dial cycle proceeds after recovery
    assert cycle_4.safety_decision == "APPROVE", f"Expected APPROVE after recovery, got {cycle_4.safety_decision}"
    assert cycle_4.allowed_calls > 0, f"Expected allowed_calls > 0, got {cycle_4.allowed_calls}"
    assert cycle_4.calls_placed > 0, f"Expected calls_placed > 0, got {cycle_4.calls_placed}"
    assert alice.current_call_id is not None, "Alice was not allocated to a call after recovery"
    assert alice.status in [AgentState.DIALING.value, AgentState.CONNECTED.value, AgentState.RESERVED.value], (
        f"Expected active status for Alice, got {alice.status}"
    )

    # All assertions passed!
    print_banner("FAILURE REPLAY LAB COMPLETED SUCCESSFULLY: ALL INVARIANTS VERIFIED")

    res = FailureReplayLabResult(
        baseline_calls_placed=cycle_1.calls_placed,
        circuit_breaker_opened=(cb_health.is_healthy is False),
        circuit_breaker_state_during_outage=CircuitState.OPEN.value,
        safety_decision_during_outage=cycle_3.safety_decision,
        allowed_calls_during_outage=cycle_3.allowed_calls,
        stale_reservations_cleared=report.stale_reservations_cleared,
        agent_recovered_status=recovered_agent_status,
        borrower_recovered_status=recovered_borrower_status,
        post_recovery_decision=cycle_4.safety_decision,
        post_recovery_calls_placed=cycle_4.calls_placed,
        post_recovery_agent_status=alice.status,
    )
    db.close()
    return res


if __name__ == "__main__":
    run_failure_replay_lab()
