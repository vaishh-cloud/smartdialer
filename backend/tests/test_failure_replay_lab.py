import datetime
from scripts.failure_replay_lab import run_failure_replay_lab
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.providers.provider_a import ProviderA
from app.providers.circuit_breaker import ProviderCircuitBreaker, CircuitState
from app.safety.safety_controller import SafetyController
from app.engine.dialer_worker import DialerWorker
from app.recovery.reconciler import StateReconciler


def test_failure_replay_lab_end_to_end_outcomes():
    """
    Verifies that the end-to-end failure replay lab completes and all key
    invariants are explicitly asserted on the returned result object:
    1. Circuit breaker opens under simulated provider errors.
    2. Safety controller enforces REJECT on dialing while downstream is unhealthy.
    3. Stale agent and borrower reservations are fully recovered.
    4. Subsequent dial cycles proceed normally post-recovery.
    """
    result = run_failure_replay_lab()

    # 1. Circuit breaker opens after simulated failures
    assert result.circuit_breaker_opened is True
    assert result.circuit_breaker_state_during_outage == CircuitState.OPEN.value

    # 2. Safety controller rejects dialing while provider is unhealthy
    assert result.safety_decision_during_outage == "REJECT"
    assert result.allowed_calls_during_outage == 0

    # 3. Stale agent and borrower reservations are recovered
    assert result.stale_reservations_cleared == 1
    assert result.agent_recovered_status == AgentState.AVAILABLE.value
    assert result.borrower_recovered_status == "PENDING"

    # 4. Subsequent dial cycle proceeds after recovery
    assert result.post_recovery_decision == "APPROVE"
    assert result.post_recovery_calls_placed > 0
    assert result.post_recovery_agent_status in [
        AgentState.DIALING.value,
        AgentState.CONNECTED.value,
        AgentState.RESERVED.value,
    ]


def test_circuit_breaker_tripping_forces_safety_rejection(db_session):
    """
    Directly asserts that a tripped circuit breaker forces the safety controller
    to reject dialing and blocks any outbound dispatches.
    """
    camp = Campaign(name="Trip Test", mode="PROGRESSIVE")
    db_session.add(camp)
    agent = Agent(name="Agent-1", status=AgentState.AVAILABLE.value)
    db_session.add(agent)
    borrower = Borrower(name="Borrower-1", phone="+919000000001", priority=1)
    db_session.add(borrower)
    db_session.commit()

    provider = ProviderA(failure_rate=0.0)
    cb = ProviderCircuitBreaker(provider_name="ProviderA", failure_threshold=0.5, window_size=4)
    safety = SafetyController()
    worker = DialerWorker("worker-trip-test", db_session, provider, cb, safety)

    # Trip breaker by recording 4 consecutive failures
    for _ in range(4):
        cb.record_result(False)

    assert cb.state == CircuitState.OPEN
    assert not cb.can_execute()

    res = worker.run_dial_cycle(camp.id)
    assert res.safety_decision == "REJECT"
    assert res.allowed_calls == 0
    assert res.calls_placed == 0

    # Agent and borrower remain unmutated
    db_session.refresh(agent)
    db_session.refresh(borrower)
    assert agent.status == AgentState.AVAILABLE.value
    assert borrower.status == "PENDING"


def test_stale_reservation_reconciliation_and_resumed_dialing(db_session):
    """
    Directly asserts that stale agent and borrower reservations from a dead worker
    are recovered by StateReconciler, and a healthy dial cycle can immediately proceed.
    """
    camp = Campaign(name="Reconcile Test", mode="PROGRESSIVE")
    db_session.add(camp)

    stale_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
    agent = Agent(
        name="Agent-Stale",
        status=AgentState.RESERVED.value,
        reserved_by="crashed-worker",
        reserved_at=stale_time,
    )
    db_session.add(agent)

    borrower = Borrower(
        name="Borrower-Stale",
        phone="+919000000002",
        status="RESERVED",
        reserved_by="crashed-worker",
        reserved_at=stale_time,
    )
    db_session.add(borrower)
    db_session.commit()

    reconciler = StateReconciler(db_session, stale_reservation_timeout_sec=5)
    report = reconciler.reconcile_all()

    assert report.stale_reservations_cleared == 1
    db_session.refresh(agent)
    db_session.refresh(borrower)

    assert agent.status == AgentState.AVAILABLE.value
    assert agent.reserved_by is None
    assert borrower.status == "PENDING"
    assert borrower.reserved_by is None

    # Resumed dialing cycle succeeds
    provider = ProviderA(failure_rate=0.0)
    cb = ProviderCircuitBreaker(provider_name="ProviderA")
    safety = SafetyController()
    worker = DialerWorker("worker-healthy", db_session, provider, cb, safety)

    res = worker.run_dial_cycle(camp.id)
    assert res.safety_decision == "APPROVE"
    assert res.calls_placed == 1
    db_session.refresh(agent)
    assert agent.status in [AgentState.DIALING.value, AgentState.CONNECTED.value]
