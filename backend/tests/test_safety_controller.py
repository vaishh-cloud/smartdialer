import pytest
from app.pacing.base import PacingProposal, PacingContext
from app.safety.safety_controller import SafetyController, SafetyDecisionType

def test_safety_controller_approve_safe_progressive():
    controller = SafetyController()
    proposal = PacingProposal(
        mode="PROGRESSIVE",
        suggested_calls=5,
        reasoning="Test",
        metrics_snapshot={}
    )
    context = PacingContext(
        available_agents=10,
        busy_agents=5,
        calls_in_flight=0,
        historical_answer_rate=0.5,
        recent_abandon_rate=0.01,
        provider_healthy=True,
    )
    decision = controller.evaluate(proposal, context)
    assert decision.decision == SafetyDecisionType.APPROVE
    assert decision.allowed_calls == 5

def test_safety_controller_reduce_progressive_exceeding_capacity():
    controller = SafetyController()
    proposal = PacingProposal(
        mode="PROGRESSIVE",
        suggested_calls=12,
        reasoning="Aggressive",
        metrics_snapshot={}
    )
    context = PacingContext(
        available_agents=5,
        busy_agents=5,
        calls_in_flight=1,
        historical_answer_rate=0.5,
        provider_healthy=True,
    )
    # 5 available - 1 in flight = 4 safe calls allowed
    decision = controller.evaluate(proposal, context)
    assert decision.decision == SafetyDecisionType.REDUCE
    assert decision.allowed_calls == 4

def test_safety_controller_reject_on_provider_outage():
    controller = SafetyController()
    proposal = PacingProposal(
        mode="PROGRESSIVE",
        suggested_calls=5,
        reasoning="Test",
        metrics_snapshot={}
    )
    context = PacingContext(
        available_agents=10,
        busy_agents=5,
        calls_in_flight=0,
        historical_answer_rate=0.5,
        provider_healthy=False,  # Outage!
    )
    decision = controller.evaluate(proposal, context)
    assert decision.decision == SafetyDecisionType.REJECT
    assert decision.allowed_calls == 0
    assert "unhealthy" in decision.reasons[0].lower()

def test_safety_controller_fallback_progressive_on_high_abandon_rate():
    controller = SafetyController(max_abandon_rate=0.03)
    proposal = PacingProposal(
        mode="PREDICTIVE",
        suggested_calls=20,
        reasoning="Test predictive",
        metrics_snapshot={}
    )
    context = PacingContext(
        available_agents=10,
        busy_agents=10,
        calls_in_flight=2,
        historical_answer_rate=0.5,
        recent_abandon_rate=0.045,  # 4.5% > 3% threshold!
        provider_healthy=True,
    )
    decision = controller.evaluate(proposal, context)
    assert decision.decision == SafetyDecisionType.FALLBACK_PROGRESSIVE
    # Safe progressive limit is 10 available - 2 in flight = 8 calls
    assert decision.allowed_calls == 8
    assert decision.effective_mode == "FALLBACK_PROGRESSIVE"

def test_safety_controller_clamp_excessive_predictive_overdial():
    controller = SafetyController(max_overdial_ratio=2.0)
    proposal = PacingProposal(
        mode="PREDICTIVE",
        suggested_calls=50,
        reasoning="Excessive dials",
        metrics_snapshot={}
    )
    context = PacingContext(
        available_agents=5,
        busy_agents=5,
        calls_in_flight=5,
        historical_answer_rate=0.2,
        provider_healthy=True,
    )
    # Total agents = 10, max overdial ratio = 2.0 -> max in-flight = 20.
    # Current in-flight = 5 -> max new allowed = 15.
    decision = controller.evaluate(proposal, context)
    assert decision.decision == SafetyDecisionType.REDUCE
    assert decision.allowed_calls == 15
