import pytest
from app.state_machines.agent_state import (
    AgentState,
    AgentStateMachine,
    InvalidAgentStateTransitionError,
)
from app.state_machines.call_state import (
    CallState,
    CallStateMachine,
    InvalidCallStateTransitionError,
)

def test_agent_state_machine_valid_transitions():
    # AVAILABLE -> RESERVED -> DIALING -> CONNECTED -> WRAP_UP -> AVAILABLE
    assert AgentStateMachine.can_transition(AgentState.AVAILABLE, AgentState.RESERVED)
    assert AgentStateMachine.can_transition(AgentState.RESERVED, AgentState.DIALING)
    assert AgentStateMachine.can_transition(AgentState.DIALING, AgentState.CONNECTED)
    assert AgentStateMachine.can_transition(AgentState.CONNECTED, AgentState.WRAP_UP)
    assert AgentStateMachine.can_transition(AgentState.WRAP_UP, AgentState.AVAILABLE)

    # Reservation timeout / failure rollback
    assert AgentStateMachine.can_transition(AgentState.RESERVED, AgentState.AVAILABLE)
    assert AgentStateMachine.can_transition(AgentState.DIALING, AgentState.AVAILABLE)

def test_agent_state_machine_invalid_transitions():
    # OFFLINE directly to CONNECTED
    assert not AgentStateMachine.can_transition(AgentState.OFFLINE, AgentState.CONNECTED)
    with pytest.raises(InvalidAgentStateTransitionError):
        AgentStateMachine.validate_transition(AgentState.OFFLINE, AgentState.CONNECTED)

    # RESERVED directly to WRAP_UP
    assert not AgentStateMachine.can_transition(AgentState.RESERVED, AgentState.WRAP_UP)
    with pytest.raises(InvalidAgentStateTransitionError):
        AgentStateMachine.validate_transition(AgentState.RESERVED, AgentState.WRAP_UP)

def test_call_state_machine_lifecycle():
    # QUEUED -> RESERVED -> INITIATED -> RINGING -> ANSWERED -> CONNECTED -> COMPLETED
    assert CallStateMachine.can_transition(CallState.QUEUED, CallState.RESERVED)
    assert CallStateMachine.can_transition(CallState.RESERVED, CallState.INITIATED)
    assert CallStateMachine.can_transition(CallState.INITIATED, CallState.RINGING)
    assert CallStateMachine.can_transition(CallState.RINGING, CallState.ANSWERED)
    assert CallStateMachine.can_transition(CallState.ANSWERED, CallState.CONNECTED)
    assert CallStateMachine.can_transition(CallState.CONNECTED, CallState.COMPLETED)

def test_call_state_terminal_lock():
    # Once terminal, NO transition is allowed
    for terminal in [CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED]:
        assert CallStateMachine.is_terminal(terminal)
        with pytest.raises(InvalidCallStateTransitionError):
            CallStateMachine.validate_transition(terminal, CallState.ANSWERED)
        with pytest.raises(InvalidCallStateTransitionError):
            CallStateMachine.validate_transition(terminal, CallState.RINGING)
        with pytest.raises(InvalidCallStateTransitionError):
            CallStateMachine.validate_transition(terminal, CallState.CONNECTED)

def test_call_state_monotonic_stale_detection():
    # ANSWERED (rank 4) -> incoming RINGING (rank 3) should be flagged stale
    assert CallStateMachine.is_stale_or_out_of_order(CallState.ANSWERED, CallState.RINGING)
    # CONNECTED (rank 5) -> incoming ANSWERED (rank 4) should be flagged stale
    assert CallStateMachine.is_stale_or_out_of_order(CallState.CONNECTED, CallState.ANSWERED)
    # COMPLETED (terminal) -> incoming anything non-terminal should be flagged stale
    assert CallStateMachine.is_stale_or_out_of_order(CallState.COMPLETED, CallState.ANSWERED)

def test_call_state_idempotent_reentry():
    # Repeated ANSWERED event on already ANSWERED call
    assert CallStateMachine.can_transition(CallState.ANSWERED, CallState.ANSWERED)
    # validate_transition should not raise
    CallStateMachine.validate_transition(CallState.ANSWERED, CallState.ANSWERED)
