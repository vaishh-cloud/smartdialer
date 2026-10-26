import json
from typing import Optional, Dict, Any
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.models.call import Call
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.call_event import CallEventLog
from app.state_machines.call_state import CallState, CallStateMachine, InvalidCallStateTransitionError
from app.state_machines.agent_state import AgentState, AgentStateMachine
from app.providers.base import ProviderCallEvent

class EventProcessResult(BaseModel):
    status: str  # PROCESSED, DUPLICATE_IGNORED, STALE_EVENT_IGNORED, NOOP_ALREADY_IN_STATE, ERROR
    call_id: int
    prior_state: Optional[str] = None
    new_state: Optional[str] = None
    notes: Optional[str] = None

class EventProcessor:
    """
    Idempotent Event Processor.
    Processes provider events safely, preventing duplicates, ignoring out-of-order/stale
    callbacks, and ensuring call/agent states remain strictly consistent and monotonic.
    """

    def __init__(self, db: Session):
        self.db = db

    def process_event(self, event: ProviderCallEvent) -> EventProcessResult:
        # Step 1: Idempotency Key deduplication
        existing_log = self.db.execute(
            select(CallEventLog).where(CallEventLog.idempotency_key == event.idempotency_key)
        ).scalar_one_or_none()

        if existing_log:
            call = self.db.get(Call, event.call_id)
            return EventProcessResult(
                status="DUPLICATE_IGNORED",
                call_id=event.call_id,
                prior_state=call.status if call else None,
                new_state=call.status if call else None,
                notes=f"Duplicate event with key '{event.idempotency_key}' dropped without mutation.",
            )

        # Step 2: Fetch Call
        call = self.db.get(Call, event.call_id)
        if not call:
            return EventProcessResult(
                status="ERROR",
                call_id=event.call_id,
                notes=f"Call {event.call_id} not found in database.",
            )

        prior_state = call.status
        event_state = CallState(event.event_type)
        current_state = CallState(call.status)

        # Step 3: Handle Idempotent Same-State Re-entry
        if current_state == event_state:
            self._record_audit_log(event)
            self.db.commit()
            return EventProcessResult(
                status="NOOP_ALREADY_IN_STATE",
                call_id=call.id,
                prior_state=prior_state,
                new_state=call.status,
                notes=f"Call already in state {call.status}.",
            )

        # Step 4: Handle Out-of-Order / Stale Events (Monotonicity Guard)
        if CallStateMachine.is_stale_or_out_of_order(current_state, event_state):
            # Record audit log that the late event was received, but DO NOT mutate call or agent state
            self._record_audit_log(event)
            self.db.commit()
            return EventProcessResult(
                status="STALE_EVENT_IGNORED",
                call_id=call.id,
                prior_state=prior_state,
                new_state=call.status,
                notes=(
                    f"Out-of-order event {event.event_type} arrived while call is in {call.status}. "
                    "Ignored to protect state monotonicity."
                ),
            )

        # Step 5: Validate forward transition
        try:
            CallStateMachine.validate_transition(current_state, event_state)
        except InvalidCallStateTransitionError as ex:
            return EventProcessResult(
                status="ERROR",
                call_id=call.id,
                prior_state=prior_state,
                new_state=call.status,
                notes=f"Illegal transition attempted: {str(ex)}",
            )

        # Step 6: Apply Valid State Progression
        call.status = event_state.value
        call.version += 1

        if event_state == CallState.RINGING:
            call.ringing_at = event.timestamp

        elif event_state == CallState.ANSWERED:
            call.answered_at = event.timestamp
            # If agent is assigned and dialing, advance agent to CONNECTED
            if call.agent_id:
                agent = self.db.get(Agent, call.agent_id)
                if agent and AgentStateMachine.can_transition(AgentState(agent.status), AgentState.CONNECTED):
                    agent.status = AgentState.CONNECTED.value
                    agent.version += 1

        elif event_state == CallState.CONNECTED:
            call.connected_at = event.timestamp
            if call.agent_id:
                agent = self.db.get(Agent, call.agent_id)
                if agent and AgentStateMachine.can_transition(AgentState(agent.status), AgentState.CONNECTED):
                    agent.status = AgentState.CONNECTED.value
                    agent.version += 1

        elif event_state == CallState.COMPLETED:
            call.ended_at = event.timestamp
            duration = event.payload.get("duration_seconds")
            if duration is not None:
                call.duration_seconds = float(duration)
            elif call.answered_at:
                call.duration_seconds = (event.timestamp - call.answered_at).total_seconds()
            
            # Transition agent to WRAP_UP or AVAILABLE
            if call.agent_id:
                agent = self.db.get(Agent, call.agent_id)
                if agent:
                    agent.status = AgentState.WRAP_UP.value
                    agent.total_calls_handled += 1
                    agent.current_call_id = None
                    agent.version += 1

            # Update borrower to COMPLETED
            borrower = self.db.get(Borrower, call.borrower_id)
            if borrower:
                borrower.status = "COMPLETED"
                borrower.version += 1

        elif event_state == CallState.FAILED:
            call.ended_at = event.timestamp
            call.failure_reason = event.payload.get("reason", "Call failed")

            # Release agent back to AVAILABLE
            if call.agent_id:
                agent = self.db.get(Agent, call.agent_id)
                if agent and agent.status in [AgentState.RESERVED.value, AgentState.DIALING.value]:
                    agent.status = AgentState.AVAILABLE.value
                    agent.reserved_by = None
                    agent.reserved_at = None
                    agent.current_call_id = None
                    agent.version += 1

            # Borrower can be retried or marked unreachable
            borrower = self.db.get(Borrower, call.borrower_id)
            if borrower:
                borrower.call_attempts += 1
                if borrower.call_attempts >= 3:
                    borrower.status = "UNREACHABLE"
                else:
                    borrower.status = "PENDING"
                borrower.version += 1

        # Record audit event log
        self._record_audit_log(event)
        self.db.commit()

        return EventProcessResult(
            status="PROCESSED",
            call_id=call.id,
            prior_state=prior_state,
            new_state=call.status,
            notes=f"Successfully transitioned call {call.id} from {prior_state} to {call.status}.",
        )

    def _record_audit_log(self, event: ProviderCallEvent) -> None:
        log = CallEventLog(
            call_id=event.call_id,
            idempotency_key=event.idempotency_key,
            event_type=event.event_type,
            sequence_num=event.sequence_num,
            payload_json=json.dumps(event.payload),
        )
        self.db.add(log)
