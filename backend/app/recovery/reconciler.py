import datetime
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.call import Call
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState, CallStateMachine
from app.config import settings

class ReconcilerReport:
    def __init__(self):
        self.stale_reservations_cleared: int = 0
        self.stale_initiated_calls_failed: int = 0
        self.orphaned_agents_freed: int = 0
        self.details: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stale_reservations_cleared": self.stale_reservations_cleared,
            "stale_initiated_calls_failed": self.stale_initiated_calls_failed,
            "orphaned_agents_freed": self.orphaned_agents_freed,
            "details": self.details,
        }

class StateReconciler:
    """
    Background recovery and reconciliation worker.
    Recovers from worker crashes, network partitioning, stale reservations,
    and abandoned/hanging provider calls.
    """

    def __init__(
        self,
        db: Session,
        stale_reservation_timeout_sec: int = settings.STALE_RESERVATION_TIMEOUT_SECONDS,
        stale_initiated_timeout_sec: int = settings.STALE_INITIATED_TIMEOUT_SECONDS,
    ):
        self.db = db
        self.stale_reservation_timeout_sec = stale_reservation_timeout_sec
        self.stale_initiated_timeout_sec = stale_initiated_timeout_sec

    def reconcile_all(self) -> ReconcilerReport:
        """Runs all reconciliation tasks in a single pass."""
        report = ReconcilerReport()
        self.recover_stale_agent_reservations(report)
        self.recover_stale_borrower_reservations(report)
        self.recover_hanging_initiated_calls(report)
        self.cleanup_orphaned_dialing_agents(report)
        return report

    def recover_stale_agent_reservations(self, report: ReconcilerReport) -> None:
        """
        Recovers agents that were RESERVED by a worker that subsequently crashed
        before placing the call.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        threshold = now - datetime.timedelta(seconds=self.stale_reservation_timeout_sec)

        stale_agents = self.db.execute(
            select(Agent).where(
                Agent.status == AgentState.RESERVED.value,
                Agent.reserved_at < threshold,
                Agent.current_call_id.is_(None),
            )
        ).scalars().all()

        for agent in stale_agents:
            agent.status = AgentState.AVAILABLE.value
            agent.reserved_by = None
            agent.reserved_at = None
            agent.version += 1
            report.stale_reservations_cleared += 1
            report.details.append(f"Freed stale reserved agent {agent.id} (worker crashed before call creation)")

        if stale_agents:
            self.db.commit()

    def recover_stale_borrower_reservations(self, report: ReconcilerReport) -> None:
        """Recovers borrowers that were marked RESERVED but never dialed."""
        now = datetime.datetime.now(datetime.timezone.utc)
        threshold = now - datetime.timedelta(seconds=self.stale_reservation_timeout_sec)

        stale_borrowers = self.db.execute(
            select(Borrower).where(
                Borrower.status == "RESERVED",
                Borrower.reserved_at < threshold,
            )
        ).scalars().all()

        for b in stale_borrowers:
            b.status = "PENDING"
            b.reserved_by = None
            b.reserved_at = None
            b.version += 1
            report.details.append(f"Reset stale reserved borrower {b.id} to PENDING")

        if stale_borrowers:
            self.db.commit()

    def recover_hanging_initiated_calls(self, report: ReconcilerReport) -> None:
        """
        Recovers calls stuck in INITIATED or RINGING where the provider or worker died
        without sending terminal callback events.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        threshold = now - datetime.timedelta(seconds=self.stale_initiated_timeout_sec)

        hanging_calls = self.db.execute(
            select(Call).where(
                Call.status.in_([CallState.INITIATED.value, CallState.RINGING.value]),
                Call.initiated_at < threshold,
            )
        ).scalars().all()

        for call in hanging_calls:
            call.status = CallState.FAILED.value
            call.failure_reason = "RECONCILER_TIMEOUT_OR_WORKER_CRASH"
            call.ended_at = now
            call.version += 1
            report.stale_initiated_calls_failed += 1

            # Release attached agent if still in DIALING
            if call.agent_id:
                agent = self.db.get(Agent, call.agent_id)
                if agent and agent.status in [AgentState.RESERVED.value, AgentState.DIALING.value]:
                    agent.status = AgentState.AVAILABLE.value
                    agent.current_call_id = None
                    agent.reserved_by = None
                    agent.reserved_at = None
                    agent.version += 1
                    report.orphaned_agents_freed += 1

            # Release borrower
            borrower = self.db.get(Borrower, call.borrower_id)
            if borrower:
                borrower.call_attempts += 1
                borrower.status = "PENDING" if borrower.call_attempts < 3 else "UNREACHABLE"
                borrower.version += 1

            report.details.append(f"Timed out hanging call {call.id} (no event received within SLA)")

        if hanging_calls:
            self.db.commit()

    def cleanup_orphaned_dialing_agents(self, report: ReconcilerReport) -> None:
        """
        Checks for agents marked DIALING whose call has already reached a terminal state.
        Ensures agents never get stuck permanently in DIALING.
        """
        dialing_agents = self.db.execute(
            select(Agent).where(Agent.status == AgentState.DIALING.value)
        ).scalars().all()

        for agent in dialing_agents:
            if not agent.current_call_id:
                agent.status = AgentState.AVAILABLE.value
                agent.version += 1
                report.orphaned_agents_freed += 1
            else:
                call = self.db.get(Call, agent.current_call_id)
                if not call or CallStateMachine.is_terminal(CallState(call.status)):
                    agent.status = AgentState.AVAILABLE.value
                    agent.current_call_id = None
                    agent.reserved_by = None
                    agent.reserved_at = None
                    agent.version += 1
                    report.orphaned_agents_freed += 1
                    report.details.append(f"Freed agent {agent.id} whose call {agent.current_call_id} was already terminal")

        if dialing_agents:
            self.db.commit()
