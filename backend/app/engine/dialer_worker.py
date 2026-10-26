import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState
from app.pacing.base import PacingContext, PacingProposal
from app.pacing.progressive import ProgressivePacingEngine
from app.pacing.predictive import PredictivePacingEngine
from app.safety.safety_controller import SafetyController, SafetyDecision, SafetyDecisionType
from app.allocation.allocator import CallAllocator
from app.providers.base import TelecomProvider
from app.providers.circuit_breaker import ProviderCircuitBreaker

class DialCycleResult(BaseModel):
    worker_id: str
    campaign_id: int
    mode: str
    available_agents: int
    busy_agents: int
    calls_in_flight: int
    proposed_calls: int
    allowed_calls: int
    safety_decision: str
    calls_placed: int
    failed_dispatches: int
    details: List[str]

class DialerWorker:
    """
    Core Dialer Worker Orchestrator.
    Executes the architectural pipeline:
    Campaign -> Pacing Engine -> Safety Controller -> Call Allocator -> Telecom Provider
    """

    def __init__(
        self,
        worker_id: str,
        db: Session,
        provider: TelecomProvider,
        circuit_breaker: ProviderCircuitBreaker,
        safety_controller: Optional[SafetyController] = None,
    ):
        self.worker_id = worker_id
        self.db = db
        self.provider = provider
        self.circuit_breaker = circuit_breaker
        self.safety_controller = safety_controller or SafetyController()
        self.allocator = CallAllocator(db, worker_id=worker_id)

    def run_dial_cycle(self, campaign_id: int) -> DialCycleResult:
        campaign = self.db.get(Campaign, campaign_id)
        if not campaign or campaign.status != "ACTIVE":
            return DialCycleResult(
                worker_id=self.worker_id,
                campaign_id=campaign_id,
                mode="INACTIVE",
                available_agents=0,
                busy_agents=0,
                calls_in_flight=0,
                proposed_calls=0,
                allowed_calls=0,
                safety_decision="REJECTED_CAMPAIGN_INACTIVE",
                calls_placed=0,
                failed_dispatches=0,
                details=["Campaign does not exist or is not active."],
            )

        # 1. Gather Context & Operational Metrics
        available_agents_count = self.db.execute(
            select(func.count(Agent.id)).where(Agent.status == AgentState.AVAILABLE.value)
        ).scalar() or 0

        busy_agents_count = self.db.execute(
            select(func.count(Agent.id)).where(
                Agent.status.in_([AgentState.DIALING.value, AgentState.CONNECTED.value])
            )
        ).scalar() or 0

        calls_in_flight = self.db.execute(
            select(func.count(Call.id)).where(
                Call.campaign_id == campaign_id,
                Call.status.in_([CallState.INITIATED.value, CallState.RINGING.value]),
            )
        ).scalar() or 0

        # Calculate empirical answer rate & abandon rate from recent completed/failed calls
        recent_calls = self.db.execute(
            select(Call.status)
            .where(
                Call.campaign_id == campaign_id,
                Call.status.in_([CallState.COMPLETED.value, CallState.FAILED.value, CallState.CANCELLED.value]),
            )
            .order_by(Call.ended_at.desc())
            .limit(50)
        ).scalars().all()

        total_recent = len(recent_calls)
        completed_recent = recent_calls.count(CallState.COMPLETED.value)
        historical_answer_rate = (completed_recent / total_recent) if total_recent >= 5 else 0.50
        
        abandoned_recent = recent_calls.count(CallState.CANCELLED.value)
        recent_abandon_rate = (abandoned_recent / total_recent) if total_recent >= 5 else 0.0

        pacing_context = PacingContext(
            available_agents=available_agents_count,
            busy_agents=busy_agents_count,
            calls_in_flight=calls_in_flight,
            historical_answer_rate=historical_answer_rate,
            recent_abandon_rate=recent_abandon_rate,
            provider_healthy=self.circuit_breaker.can_execute(),
        )

        # 2. Pacing Calculation
        if campaign.mode.upper() == "PREDICTIVE":
            pacing_engine = PredictivePacingEngine()
        else:
            pacing_engine = ProgressivePacingEngine()

        proposal = pacing_engine.calculate_proposal(pacing_context)

        # 3. Independent Safety Controller Evaluation
        safety_decision = self.safety_controller.evaluate(proposal, pacing_context)
        allowed_count = safety_decision.allowed_calls

        details: List[str] = [
            f"Proposal: {proposal.suggested_calls} dials ({proposal.reasoning})",
            f"Safety: {safety_decision.decision.value} -> {allowed_count} allowed ({', '.join(safety_decision.reasons)})",
        ]

        if allowed_count <= 0:
            return DialCycleResult(
                worker_id=self.worker_id,
                campaign_id=campaign_id,
                mode=campaign.mode,
                available_agents=available_agents_count,
                busy_agents=busy_agents_count,
                calls_in_flight=calls_in_flight,
                proposed_calls=proposal.suggested_calls,
                allowed_calls=0,
                safety_decision=safety_decision.decision.value,
                calls_placed=0,
                failed_dispatches=0,
                details=details,
            )

        # 4. Atomic Allocation
        calls_placed = 0
        failed_dispatches = 0

        # Reserve borrowers
        reserved_borrowers = self.allocator.reserve_pending_borrowers(allowed_count)
        if not reserved_borrowers:
            details.append("No pending borrowers available to dial.")
            return DialCycleResult(
                worker_id=self.worker_id,
                campaign_id=campaign_id,
                mode=campaign.mode,
                available_agents=available_agents_count,
                busy_agents=busy_agents_count,
                calls_in_flight=calls_in_flight,
                proposed_calls=proposal.suggested_calls,
                allowed_calls=allowed_count,
                safety_decision=safety_decision.decision.value,
                calls_placed=0,
                failed_dispatches=0,
                details=details,
            )

        # In Progressive mode, also atomically reserve an available agent for each call
        reserved_agents: List[Agent] = []
        if campaign.mode.upper() == "PROGRESSIVE" or safety_decision.effective_mode == "FALLBACK_PROGRESSIVE":
            reserved_agents = self.allocator.reserve_available_agents(len(reserved_borrowers))

        # 5. Dispatch calls to Telecom Provider
        now = datetime.datetime.now(datetime.timezone.utc)
        for idx, borrower in enumerate(reserved_borrowers):
            agent = reserved_agents[idx] if idx < len(reserved_agents) else None
            
            # If in progressive mode and we ran out of reserved agents, stop and release borrower
            if (campaign.mode.upper() == "PROGRESSIVE" or safety_decision.effective_mode == "FALLBACK_PROGRESSIVE") and not agent:
                self.allocator.release_borrower(borrower.id, to_status="PENDING")
                continue

            call = self.allocator.create_reserved_call(
                campaign_id=campaign_id,
                borrower_id=borrower.id,
                agent_id=agent.id if agent else None,
            )

            # Check circuit breaker before each provider call
            if not self.circuit_breaker.can_execute():
                self.circuit_breaker.record_result(False)
                call.status = CallState.FAILED.value
                call.failure_reason = "CIRCUIT_BREAKER_OPEN"
                call.ended_at = now
                if agent:
                    self.allocator.release_agent(agent.id, AgentState.AVAILABLE)
                self.allocator.release_borrower(borrower.id, "PENDING")
                self.db.commit()
                failed_dispatches += 1
                continue

            # Dispatch to telecom provider
            resp = self.provider.initiate_call(call_id=call.id, phone_number=borrower.phone)
            self.circuit_breaker.record_result(resp.success)

            if resp.success:
                call.status = CallState.INITIATED.value
                call.provider_name = self.provider.name
                call.provider_call_id = resp.provider_call_id
                call.initiated_at = now
                call.version += 1
                self.db.commit()
                calls_placed += 1
                details.append(f"Call {call.id} placed to {borrower.phone} (Provider ID: {resp.provider_call_id})")
            else:
                call.status = CallState.FAILED.value
                call.failure_reason = resp.error_message or "Provider dispatch failed"
                call.ended_at = now
                call.version += 1
                if agent:
                    self.allocator.release_agent(agent.id, AgentState.AVAILABLE)
                self.allocator.release_borrower(borrower.id, "PENDING")
                self.db.commit()
                failed_dispatches += 1
                details.append(f"Call {call.id} dispatch failed: {resp.error_message}")

        return DialCycleResult(
            worker_id=self.worker_id,
            campaign_id=campaign_id,
            mode=campaign.mode,
            available_agents=available_agents_count,
            busy_agents=busy_agents_count,
            calls_in_flight=calls_in_flight + calls_placed,
            proposed_calls=proposal.suggested_calls,
            allowed_calls=allowed_count,
            safety_decision=safety_decision.decision.value,
            calls_placed=calls_placed,
            failed_dispatches=failed_dispatches,
            details=details,
        )
