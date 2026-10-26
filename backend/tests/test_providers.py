import pytest
import time
from app.providers.provider_a import ProviderA
from app.providers.provider_b import ProviderB
from app.providers.circuit_breaker import ProviderCircuitBreaker, CircuitState

def test_provider_a_basic_flow():
    prov = ProviderA(failure_rate=0.0, seed=42)
    resp = prov.initiate_call(call_id=1, phone_number="+1234567890")
    assert resp.success is True
    assert resp.provider_call_id.startswith("provA_")

    events = prov.generate_events_for_call(call_id=1, provider_call_id=resp.provider_call_id, answered=True)
    assert len(events) == 3
    assert events[0].event_type == "RINGING"
    assert events[1].event_type == "ANSWERED"
    assert events[2].event_type == "COMPLETED"

def test_provider_b_duplicates_and_out_of_order():
    prov = ProviderB(failure_rate=0.0, seed=123)
    resp = prov.initiate_call(call_id=2, phone_number="+1987654321")
    assert resp.success is True
    assert resp.provider_call_id.startswith("provB_")

    # Generate events with forced out-of-order and forced duplicate
    events = prov.generate_events_for_call(
        call_id=2,
        provider_call_id=resp.provider_call_id,
        answered=True,
        force_duplicates=True,
        force_out_of_order=True,
    )
    # Reordered: COMPLETED, ANSWERED, RINGING, + duplicate ANSWERED
    types = [e.event_type for e in events]
    assert "COMPLETED" in types
    assert "ANSWERED" in types
    assert "RINGING" in types
    assert types.count("ANSWERED") == 2  # Injected duplicate

def test_circuit_breaker_trip_and_recover():
    cb = ProviderCircuitBreaker(provider_name="TestProvider", failure_threshold=0.5, window_size=4, cooldown_seconds=1)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # Record 2 successes
    cb.record_result(True)
    cb.record_result(True)
    assert cb.state == CircuitState.CLOSED

    # Record consecutive failures (2 failures out of 4 = 50% failure threshold)
    cb.record_result(False)
    cb.record_result(False)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Wait for cooldown
    time.sleep(1.1)
    # Canary trial allowed in HALF_OPEN
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Probe succeeds -> Circuit resets to CLOSED
    cb.record_result(True)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True
