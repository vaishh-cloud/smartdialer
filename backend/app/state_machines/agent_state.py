from enum import Enum
from typing import Set, Dict, Optional
import datetime

class AgentState(str, Enum):
    OFFLINE = "OFFLINE"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    DIALING = "DIALING"
    CONNECTED = "CONNECTED"
    WRAP_UP = "WRAP_UP"
    PAUSED = "PAUSED"

class InvalidAgentStateTransitionError(Exception):
    def __init__(self, from_state: AgentState, to_state: AgentState, message: Optional[str] = None):
        self.from_state = from_state
        self.to_state = to_state
        msg = message or f"Illegal agent state transition: {from_state.value} -> {to_state.value}"
        super().__init__(msg)

class AgentStateMachine:
    """
    Strict state machine for Agent lifecycle management.
    Ensures safe, validated transitions and prevents invalid or corrupting state changes.
    """
    
    # Adjacency map of legal transitions
    _VALID_TRANSITIONS: Dict[AgentState, Set[AgentState]] = {
        AgentState.OFFLINE: {
            AgentState.AVAILABLE,
            AgentState.PAUSED
        },
        AgentState.AVAILABLE: {
            AgentState.RESERVED,
            AgentState.PAUSED,
            AgentState.OFFLINE
        },
        AgentState.RESERVED: {
            AgentState.DIALING,
            AgentState.AVAILABLE,  # Cancellation or reservation timeout recovery
            AgentState.OFFLINE     # Agent went offline unexpectedly
        },
        AgentState.DIALING: {
            AgentState.CONNECTED,  # Borrower answered and bridged
            AgentState.AVAILABLE,  # Call failed, unanswered, busy -> agent back to available
            AgentState.WRAP_UP,    # Short disposition
            AgentState.OFFLINE
        },
        AgentState.CONNECTED: {
            AgentState.WRAP_UP,    # Call ended, agent enters wrap up / disposition
            AgentState.AVAILABLE,  # Direct to available
            AgentState.OFFLINE
        },
        AgentState.WRAP_UP: {
            AgentState.AVAILABLE,  # Wrap up completed
            AgentState.PAUSED,     # Agent takes break after call
            AgentState.OFFLINE
        },
        AgentState.PAUSED: {
            AgentState.AVAILABLE,  # Agent unpauses
            AgentState.OFFLINE
        }
    }
    
    @classmethod
    def can_transition(cls, from_state: AgentState, to_state: AgentState) -> bool:
        if from_state == to_state:
            return True  # Idempotent same-state transition
        allowed = cls._VALID_TRANSITIONS.get(from_state, set())
        return to_state in allowed

    @classmethod
    def validate_transition(cls, from_state: AgentState, to_state: AgentState, context: Optional[str] = None) -> None:
        if not cls.can_transition(from_state, to_state):
            ctx_info = f" [Context: {context}]" if context else ""
            raise InvalidAgentStateTransitionError(
                from_state, 
                to_state, 
                f"Cannot transition agent from {from_state.value} to {to_state.value}{ctx_info}"
            )
