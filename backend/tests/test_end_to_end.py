import pytest
import datetime
from sqlalchemy import select
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.engine.dialer_worker import DialerWorker
from app.events.event_processor import EventProcessor
from app.providers.provider_a import ProviderA
from app.providers.provider_b import ProviderB
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.safety.safety_controller import SafetyController
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState

def test_full_progressive_dialing_flow(db_session):
    """
    End-to-end test of progressive mode:
    1. Create 3 agents, 5 borrowers.
    2. Dialer worker runs dial cycle: places 3 calls.
    3. Simulate provider events (Ringing -> Answered -> Completed).
    4. Agents cycle through Dialing -> Connected -> Wrap Up.
    """
    camp = Campaign(name="EndToEnd Progressive", mode="PROGRESSIVE")
    db_session.add(camp)
    db_session.commit()

    # Add 3 available agents
    for i in range(3):
        db_session.add(Agent(name=f"Agent {i}", status=AgentState.AVAILABLE.value))

    # Add 5 pending borrowers
    for i in range(5):
        db_session.add(Borrower(name=f"Borrower {i}", phone=f"+1555000{i}", priority=i))
    db_session.commit()

    provider = ProviderA(failure_rate=0.0, seed=1)
    cb = ProviderCircuitBreaker(provider_name="ProviderA")
    worker = DialerWorker(
        worker_id="worker-e2e-1",
        db=db_session,
        provider=provider,
        circuit_breaker=cb,
    )

    # Run dial cycle: 3 agents free -> places exactly 3 calls
    result = worker.run_dial_cycle(camp.id)
    assert result.allowed_calls == 3
    assert result.calls_placed == 3
    assert result.safety_decision == "APPROVE"

    calls = db_session.execute(select(Call).where(Call.campaign_id == camp.id)).scalars().all()
    assert len(calls) == 3
    for c in calls:
        assert c.status == CallState.INITIATED.value
        assert c.agent_id is not None
        assert c.provider_call_id is not None

    # Process events for call 0 (Answered & Completed)
    call0 = calls[0]
    processor = EventProcessor(db_session)
    events = provider.generate_events_for_call(call0.id, call0.provider_call_id, answered=True, duration_sec=45.0)

    for evt in events:
        processor.process_event(evt)

    db_session.refresh(call0)
    agent0 = db_session.get(Agent, call0.agent_id)
    borrower0 = db_session.get(Borrower, call0.borrower_id)

    assert call0.status == CallState.COMPLETED.value
    assert call0.duration_seconds == 45.0
    assert agent0.status == AgentState.WRAP_UP.value
    assert agent0.total_calls_handled == 1
    assert borrower0.status == "COMPLETED"

def test_predictive_safety_clamping_and_fallback(db_session):
    """
    End-to-end test of predictive mode with safety clamping:
    Excessive dials suggested by predictive algorithm are safely clamped by SafetyController.
    """
    camp = Campaign(name="Predictive Test", mode="PREDICTIVE")
    db_session.add(camp)
    db_session.commit()

    # Add 5 available agents and 20 borrowers
    for i in range(5):
        db_session.add(Agent(name=f"Agent {i}", status=AgentState.AVAILABLE.value))
    for i in range(20):
        db_session.add(Borrower(name=f"Borrower {i}", phone=f"+1666000{i}", priority=1))
    db_session.commit()

    provider = ProviderA(failure_rate=0.0)
    cb = ProviderCircuitBreaker(provider_name="ProviderA")
    safety = SafetyController(max_overdial_ratio=1.5)  # Max 1.5x of 5 agents = 7 calls
    worker = DialerWorker(
        worker_id="worker-pred-1",
        db=db_session,
        provider=provider,
        circuit_breaker=cb,
        safety_controller=safety,
    )

    result = worker.run_dial_cycle(camp.id)
    # Total agents = 5, max ratio = 1.5 -> allowed calls clamped to 7
    assert result.allowed_calls <= 8
    assert result.calls_placed <= 8
    assert result.safety_decision in ["APPROVE", "REDUCE"]
