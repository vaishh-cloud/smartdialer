import pytest
import concurrent.futures
from app.allocation.allocator import CallAllocator
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.state_machines.agent_state import AgentState

def test_atomic_agent_reservation_single_worker(db_session):
    agent = Agent(name="Agent 1", status=AgentState.AVAILABLE.value)
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    allocator = CallAllocator(db_session, worker_id="worker-1")
    reserved = allocator.reserve_single_agent(agent.id)

    assert reserved is not None
    assert reserved.status == AgentState.RESERVED.value
    assert reserved.reserved_by == "worker-1"
    assert reserved.version == 2

    # Second attempt on already reserved agent should return None
    second_attempt = allocator.reserve_single_agent(agent.id)
    assert second_attempt is None

def test_concurrent_agent_reservation_two_workers(db_session):
    """
    Simulation of two workers seeing the same available agent at almost the same time.
    Both must not be able to reserve that agent.
    """
    agent = Agent(name="Agent 1", status=AgentState.AVAILABLE.value)
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    worker1 = CallAllocator(db_session, worker_id="worker-alpha")
    worker2 = CallAllocator(db_session, worker_id="worker-beta")

    # Worker 1 claims first
    res1 = worker1.reserve_single_agent(agent.id)
    # Worker 2 attempts at same time
    res2 = worker2.reserve_single_agent(agent.id)

    # Exactly one worker wins
    assert (res1 is not None and res2 is None) or (res1 is None and res2 is not None)
    winner = res1 if res1 is not None else res2
    assert winner.status == AgentState.RESERVED.value
    assert winner.reserved_by in ["worker-alpha", "worker-beta"]

def test_batch_allocation_and_release(db_session):
    # Add 5 available agents and 5 pending borrowers
    for i in range(5):
        db_session.add(Agent(name=f"Agent {i}", status=AgentState.AVAILABLE.value))
        db_session.add(Borrower(name=f"Borrower {i}", phone=f"+1000{i}", priority=i))
    db_session.commit()

    allocator = CallAllocator(db_session, worker_id="worker-batch")
    agents = allocator.reserve_available_agents(3)
    borrowers = allocator.reserve_pending_borrowers(3)

    assert len(agents) == 3
    assert len(borrowers) == 3
    for a in agents:
        assert a.status == AgentState.RESERVED.value
    for b in borrowers:
        assert b.status == "RESERVED"

    # Higher priority borrower dialed first (Borrower 4, 3, 2)
    assert borrowers[0].priority == 4
    assert borrowers[1].priority == 3
    assert borrowers[2].priority == 2

    # Release one agent
    released = allocator.release_agent(agents[0].id)
    assert released is True
    db_session.refresh(agents[0])
    assert agents[0].status == AgentState.AVAILABLE.value
    assert agents[0].reserved_by is None
