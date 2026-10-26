import pytest
import datetime
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.events.event_processor import EventProcessor
from app.providers.base import ProviderCallEvent
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState

def setup_call(db_session):
    camp = Campaign(name="Test Camp", mode="PROGRESSIVE")
    db_session.add(camp)
    db_session.commit()

    agent = Agent(name="Agent 1", status=AgentState.DIALING.value)
    borrower = Borrower(name="Borrower 1", phone="+10001", status="RESERVED")
    db_session.add_all([agent, borrower])
    db_session.commit()

    call = Call(
        campaign_id=camp.id,
        borrower_id=borrower.id,
        agent_id=agent.id,
        status=CallState.INITIATED.value,
        provider_name="ProviderB",
        provider_call_id="provB_call_123",
        initiated_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)
    return call, agent, borrower

def test_repeated_duplicate_events(db_session):
    """
    Scenario: Provider sends ANSWERED, ANSWERED, ANSWERED, COMPLETED.
    System must not create multiple state transitions or corrupt state.
    """
    call, agent, borrower = setup_call(db_session)
    processor = EventProcessor(db_session)
    now = datetime.datetime.now(datetime.timezone.utc)

    # First event: ANSWERED
    evt1 = ProviderCallEvent(
        idempotency_key="key_ans_1",
        provider_name="ProviderB",
        provider_call_id=call.provider_call_id,
        call_id=call.id,
        event_type="ANSWERED",
        timestamp=now,
    )
    res1 = processor.process_event(evt1)
    assert res1.status == "PROCESSED"
    assert res1.new_state == "ANSWERED"
    db_session.refresh(call)
    db_session.refresh(agent)
    assert call.status == "ANSWERED"
    assert agent.status == AgentState.CONNECTED.value

    # Duplicate 1: Exact same key
    res2 = processor.process_event(evt1)
    assert res2.status == "DUPLICATE_IGNORED"
    assert res2.new_state == "ANSWERED"

    # Duplicate 2: New key, but same event_type (already in state)
    evt3 = ProviderCallEvent(
        idempotency_key="key_ans_2",
        provider_name="ProviderB",
        provider_call_id=call.provider_call_id,
        call_id=call.id,
        event_type="ANSWERED",
        timestamp=now,
    )
    res3 = processor.process_event(evt3)
    assert res3.status == "NOOP_ALREADY_IN_STATE"
    assert res3.new_state == "ANSWERED"

    # Next event: COMPLETED
    evt4 = ProviderCallEvent(
        idempotency_key="key_comp_1",
        provider_name="ProviderB",
        provider_call_id=call.provider_call_id,
        call_id=call.id,
        event_type="COMPLETED",
        timestamp=now + datetime.timedelta(seconds=60),
        payload={"duration_seconds": 60.0},
    )
    res4 = processor.process_event(evt4)
    assert res4.status == "PROCESSED"
    assert res4.new_state == "COMPLETED"

    db_session.refresh(call)
    db_session.refresh(agent)
    db_session.refresh(borrower)
    assert call.status == "COMPLETED"
    assert agent.status == AgentState.WRAP_UP.value
    assert borrower.status == "COMPLETED"

def test_out_of_order_events(db_session):
    """
    Scenario: Provider sends COMPLETED, then late ANSWERED, then late RINGING.
    The system should not regress terminal state or crash.
    """
    call, agent, borrower = setup_call(db_session)
    processor = EventProcessor(db_session)
    now = datetime.datetime.now(datetime.timezone.utc)

    # Direct to COMPLETED
    evt_completed = ProviderCallEvent(
        idempotency_key="comp_key",
        provider_name="ProviderB",
        provider_call_id=call.provider_call_id,
        call_id=call.id,
        event_type="COMPLETED",
        timestamp=now,
        payload={"duration_seconds": 15.0},
    )
    res_comp = processor.process_event(evt_completed)
    assert res_comp.status == "PROCESSED"
    assert res_comp.new_state == "COMPLETED"

    # Late arriving ANSWERED
    evt_late_ans = ProviderCallEvent(
        idempotency_key="late_ans_key",
        provider_name="ProviderB",
        provider_call_id=call.provider_call_id,
        call_id=call.id,
        event_type="ANSWERED",
        timestamp=now - datetime.timedelta(seconds=10),
    )
    res_late_ans = processor.process_event(evt_late_ans)
    assert res_late_ans.status == "STALE_EVENT_IGNORED"

    # Late arriving RINGING
    evt_late_ring = ProviderCallEvent(
        idempotency_key="late_ring_key",
        provider_name="ProviderB",
        provider_call_id=call.provider_call_id,
        call_id=call.id,
        event_type="RINGING",
        timestamp=now - datetime.timedelta(seconds=14),
    )
    res_late_ring = processor.process_event(evt_late_ring)
    assert res_late_ring.status == "STALE_EVENT_IGNORED"

    # Call MUST still be in terminal COMPLETED state
    db_session.refresh(call)
    assert call.status == "COMPLETED"
