from enum import Enum
from typing import Set, Dict, Optional

class CallState(str, Enum):
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    CONNECTED = "CONNECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class InvalidCallStateTransitionError(Exception):
    def __init__(self, from_state: CallState, to_state: CallState, message: Optional[str] = None):
        self.from_state = from_state
        self.to_state = to_state
        msg = message or f"Illegal call state transition: {from_state.value} -> {to_state.value}"
        super().__init__(msg)

class StaleEventIgnoredError(Exception):
    """Raised when an out-of-order or late event is safely ignored due to monotonic ordering."""
    pass

class CallStateMachine:
    """
    Call state machine with strict monotonic progress and terminal lock.
    Guarantees idempotency and protects against out-of-order provider callbacks.
    """
    
    # State hierarchy / progression rank
    _STATE_RANKS: Dict[CallState, int] = {
        CallState.QUEUED: 0,
        CallState.RESERVED: 1,
        CallState.INITIATED: 2,
        CallState.RINGING: 3,
        CallState.ANSWERED: 4,
        CallState.CONNECTED: 5,
        CallState.COMPLETED: 6,
        CallState.FAILED: 6,
        CallState.CANCELLED: 6
    }
    
    # Legal state transition map
    _VALID_TRANSITIONS: Dict[CallState, Set[CallState]] = {
        CallState.QUEUED: {
            CallState.RESERVED,
            CallState.CANCELLED
        },
        CallState.RESERVED: {
            CallState.INITIATED,
            CallState.CANCELLED,
            CallState.FAILED
        },
        CallState.INITIATED: {
            CallState.RINGING,
            CallState.ANSWERED,   # Some networks skip RINGING
            CallState.COMPLETED,  # Short call or immediate completion
            CallState.FAILED,     # Immediate rejection / network busy
            CallState.CANCELLED
        },
        CallState.RINGING: {
            CallState.ANSWERED,
            CallState.COMPLETED,  # Fast completion / auto-attendant
            CallState.FAILED,     # No answer, line busy, timeout
            CallState.CANCELLED
        },
        CallState.ANSWERED: {
            CallState.CONNECTED,  # Successfully bridged to agent
            CallState.COMPLETED,  # Short call or IVR
            CallState.FAILED,     # Early hangup before bridge
            CallState.CANCELLED   # Abandoned call (e.g., no agent available in predictive mode)
        },
        CallState.CONNECTED: {
            CallState.COMPLETED,  # Normal call end
            CallState.FAILED      # Network drop during call
        },
        # Terminal states have no forward transitions
        CallState.COMPLETED: set(),
        CallState.FAILED: set(),
        CallState.CANCELLED: set()
    }

    TERMINAL_STATES: Set[CallState] = {
        CallState.COMPLETED,
        CallState.FAILED,
        CallState.CANCELLED
    }

    @classmethod
    def is_terminal(cls, state: CallState) -> bool:
        return state in cls.TERMINAL_STATES

    @classmethod
    def get_rank(cls, state: CallState) -> int:
        return cls._STATE_RANKS.get(state, -1)

    @classmethod
    def can_transition(cls, from_state: CallState, to_state: CallState) -> bool:
        if from_state == to_state:
            return True  # Idempotent re-entry
        allowed = cls._VALID_TRANSITIONS.get(from_state, set())
        return to_state in allowed

    @classmethod
    def validate_transition(cls, from_state: CallState, to_state: CallState, context: Optional[str] = None) -> None:
        if from_state == to_state:
            return  # Idempotent
            
        # Check if already in terminal state
        if cls.is_terminal(from_state):
            raise InvalidCallStateTransitionError(
                from_state, 
                to_state, 
                f"Terminal state locked: Call is already in terminal state {from_state.value}. Cannot transition to {to_state.value}."
            )
            
        if not cls.can_transition(from_state, to_state):
            ctx_info = f" [Context: {context}]" if context else ""
            raise InvalidCallStateTransitionError(
                from_state, 
                to_state, 
                f"Cannot transition call from {from_state.value} to {to_state.value}{ctx_info}"
            )

    @classmethod
    def is_stale_or_out_of_order(cls, current_state: CallState, event_state: CallState) -> bool:
        """
        Determines if an incoming event represents an out-of-order or stale event
        that should be ignored without raising a fatal error.
        """
        # If call is already terminal, any non-terminal event is stale
        if cls.is_terminal(current_state):
            return True
            
        current_rank = cls.get_rank(current_state)
        event_rank = cls.get_rank(event_state)
        
        # If the incoming event state has a lower rank than the current state, it is stale
        if event_rank < current_rank:
            return True
            
        return False
