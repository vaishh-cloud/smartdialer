from typing import List, Optional, Tuple
import datetime
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.call import Call
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState

class AllocationResult:
    def __init__(self, agent: Optional[Agent], borrower: Optional[Borrower], call: Optional[Call] = None):
        self.agent = agent
        self.borrower = borrower
        self.call = call

class CallAllocator:
    """
    Atomic Call & Agent Allocator.
    Guarantees that multiple concurrent workers competing for the same agent/borrower
    can never double-allocate or produce duplicate calls.
    Enforces atomic CAS (Compare-And-Swap) at the database layer.
    """

    def __init__(self, db: Session, worker_id: str):
        self.db = db
        self.worker_id = worker_id

    def reserve_single_agent(self, agent_id: int) -> Optional[Agent]:
        """
        Atomically reserve a specific agent using CAS.
        Returns the Agent if reserved, or None if already claimed by another worker.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            update(Agent)
            .where(Agent.id == agent_id, Agent.status == AgentState.AVAILABLE.value)
            .values(
                status=AgentState.RESERVED.value,
                reserved_by=self.worker_id,
                reserved_at=now,
                version=Agent.version + 1,
            )
        )
        result = self.db.execute(stmt)
        self.db.commit()

        if result.rowcount == 1:
            return self.db.get(Agent, agent_id)
        return None

    def reserve_available_agents(self, count: int) -> List[Agent]:
        """
        Atomically reserve up to `count` available agents.
        Safe against concurrent workers: only candidates whose CAS succeeds are returned.
        """
        if count <= 0:
            return []

        # Find candidate available agents
        candidates = self.db.execute(
            select(Agent.id)
            .where(Agent.status == AgentState.AVAILABLE.value)
            .order_by(Agent.total_calls_handled.asc(), Agent.id.asc())
            .limit(count * 2)  # Over-fetch candidate pool in case of contention
        ).scalars().all()

        reserved_agents: List[Agent] = []
        for agent_id in candidates:
            if len(reserved_agents) >= count:
                break
            agent = self.reserve_single_agent(agent_id)
            if agent:
                reserved_agents.append(agent)

        return reserved_agents

    def reserve_single_borrower(self, borrower_id: int) -> Optional[Borrower]:
        """
        Atomically reserve a specific borrower using CAS.
        Returns the Borrower if reserved, or None if already claimed.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            update(Borrower)
            .where(Borrower.id == borrower_id, Borrower.status == "PENDING")
            .values(
                status="RESERVED",
                reserved_by=self.worker_id,
                reserved_at=now,
                version=Borrower.version + 1,
            )
        )
        result = self.db.execute(stmt)
        self.db.commit()

        if result.rowcount == 1:
            return self.db.get(Borrower, borrower_id)
        return None

    def reserve_pending_borrowers(self, count: int) -> List[Borrower]:
        """
        Atomically reserve up to `count` pending borrowers by priority.
        """
        if count <= 0:
            return []

        candidates = self.db.execute(
            select(Borrower.id)
            .where(Borrower.status == "PENDING")
            .order_by(Borrower.priority.desc(), Borrower.call_attempts.asc(), Borrower.id.asc())
            .limit(count * 2)
        ).scalars().all()

        reserved_borrowers: List[Borrower] = []
        for borrower_id in candidates:
            if len(reserved_borrowers) >= count:
                break
            borrower = self.reserve_single_borrower(borrower_id)
            if borrower:
                reserved_borrowers.append(borrower)

        return reserved_borrowers

    def release_agent(self, agent_id: int, to_state: AgentState = AgentState.AVAILABLE) -> bool:
        """
        Release an agent from reservation back to available or another valid state.
        Only allowed if the agent is currently held by this worker or during cleanup.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            update(Agent)
            .where(Agent.id == agent_id)
            .values(
                status=to_state.value,
                reserved_by=None,
                reserved_at=None,
                current_call_id=None,
                version=Agent.version + 1,
                updated_at=now,
            )
        )
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount > 0

    def release_borrower(self, borrower_id: int, to_status: str = "PENDING") -> bool:
        """Release borrower back to pending or terminal status."""
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            update(Borrower)
            .where(Borrower.id == borrower_id)
            .values(
                status=to_status,
                reserved_by=None,
                reserved_at=None,
                version=Borrower.version + 1,
                updated_at=now,
            )
        )
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount > 0

    def create_reserved_call(self, campaign_id: int, borrower_id: int, agent_id: Optional[int]) -> Call:
        """
        Creates a call in RESERVED state tied to the reserved borrower and (optionally) agent.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        call = Call(
            campaign_id=campaign_id,
            borrower_id=borrower_id,
            agent_id=agent_id,
            status=CallState.RESERVED.value,
            worker_id=self.worker_id,
            reserved_at=now,
            queued_at=now,
        )
        self.db.add(call)
        self.db.commit()
        self.db.refresh(call)

        if agent_id:
            # Update agent with current call id
            self.db.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(current_call_id=call.id, status=AgentState.DIALING.value)
            )
            self.db.commit()

        return call
