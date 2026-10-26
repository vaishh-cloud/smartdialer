import pytest
import datetime
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.recovery.reconciler import StateReconciler
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState

def test_recover_stale_reserved_agent(db_session):
    """
    Scenario: Worker crashes after reserving agent.
    Reservation times out and reconciler restores agent to AVAILABLE.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_time = now - datetime.timedelta(seconds=30)  # 30s ago (> 15s timeout)

    agent = Agent(
        name="Agent Stale",
        status=AgentState.RESERVED.value,
        reserved_by="crashed-worker-99",
        reserved_at=stale_time,
    )
    borrower = Borrower(
        name="Borrower Stale",
        phone="+12345",
        status="RESERVED",
        reserved_by="crashed-worker-99",
        reserved_at=stale_time,
    )
    db_session.add_all([agent, borrower])
    db_session.commit()

    reconciler = StateReconciler(db_session, stale_reservation_timeout_sec=15)
    report = reconciler.reconcile_all()

    assert report.stale_reservations_cleared == 1
    db_session.refresh(agent)
    db_session.refresh(borrower)
    assert agent.status == AgentState.AVAILABLE.value
    assert agent.reserved_by is None
    assert borrower.status == "PENDING"
    assert borrower.reserved_by is None

def test_recover_hanging_initiated_call(db_session):
    """
    Scenario: Call initiated with provider, but worker crashes or network partitions.
    No provider callback ever arrives.
    Reconciler marks call FAILED and frees the agent back to AVAILABLE.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_time = now - datetime.timedelta(seconds=60)  # 60s ago (> 30s timeout)

    camp = Campaign(name="Crash Test", mode="PROGRESSIVE")
    db_session.add(camp)
    db_session.commit()

    agent = Agent(name="Hanging Agent", status=AgentState.DIALING.value)
    borrower = Borrower(name="Hanging Borrower", phone="+19999", status="CALLED")
    db_session.add_all([agent, borrower])
    db_session.commit()

    call = Call(
        campaign_id=camp.id,
        borrower_id=borrower.id,
        agent_id=agent.id,
        status=CallState.INITIATED.value,
        initiated_at=stale_time,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    # Attach call to agent
    agent.current_call_id = call.id
    db_session.commit()

    reconciler = StateReconciler(db_session, stale_initiated_timeout_sec=30)
    report = reconciler.reconcile_all()

    assert report.stale_initiated_calls_failed == 1
    db_session.refresh(call)
    db_session.refresh(agent)
    db_session.refresh(borrower)

    assert call.status == CallState.FAILED.value
    assert call.failure_reason == "RECONCILER_TIMEOUT_OR_WORKER_CRASH"
    assert agent.status == AgentState.AVAILABLE.value
    assert agent.current_call_id is None
    assert borrower.status == "PENDING"
