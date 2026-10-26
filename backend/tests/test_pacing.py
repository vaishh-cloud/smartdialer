import pytest
from app.pacing.base import PacingContext
from app.pacing.progressive import ProgressivePacingEngine
from app.pacing.predictive import PredictivePacingEngine

def test_progressive_pacing_engine():
    engine = ProgressivePacingEngine()

    # 10 available, 0 in flight -> 10 dials
    ctx1 = PacingContext(available_agents=10, busy_agents=5, calls_in_flight=0, historical_answer_rate=0.5)
    prop1 = engine.calculate_proposal(ctx1)
    assert prop1.suggested_calls == 10
    assert prop1.mode == "PROGRESSIVE"

    # 10 available, 4 already in flight -> 6 dials
    ctx2 = PacingContext(available_agents=10, busy_agents=5, calls_in_flight=4, historical_answer_rate=0.5)
    prop2 = engine.calculate_proposal(ctx2)
    assert prop2.suggested_calls == 6

    # 10 available, 12 in flight -> 0 dials
    ctx3 = PacingContext(available_agents=10, busy_agents=5, calls_in_flight=12, historical_answer_rate=0.5)
    prop3 = engine.calculate_proposal(ctx3)
    assert prop3.suggested_calls == 0

def test_predictive_pacing_engine():
    engine = PredictivePacingEngine(max_overdial_multiplier=3.0)

    # 10 available, 20 busy (avg talk 100s, ring 15s -> expected freeing = 20 * 0.15 = 3 agents)
    # Total anticipated capacity = 10 + 3 = 13.
    # At 50% answer rate (0.5), with 2 in-flight calls (expected answers = 1.0),
    # Net capacity = 12.
    # Dials = 12 / 0.5 = 24 calls.
    ctx = PacingContext(
        available_agents=10,
        busy_agents=20,
        calls_in_flight=2,
        historical_answer_rate=0.5,
        avg_call_duration_seconds=100.0,
        avg_ring_time_seconds=15.0,
    )
    prop = engine.calculate_proposal(ctx)
    assert prop.mode == "PREDICTIVE"
    assert prop.suggested_calls == 24
    assert "Predictive calculation:" in prop.reasoning
    assert prop.metrics_snapshot["expected_freeing_agents"] == 3.0

def test_predictive_pacing_under_different_answer_rates():
    engine = PredictivePacingEngine(max_overdial_multiplier=5.0)

    # Low answer rate (20%) -> requires more dials
    ctx_low = PacingContext(
        available_agents=10, busy_agents=0, calls_in_flight=0,
        historical_answer_rate=0.2, avg_call_duration_seconds=120.0
    )
    prop_low = engine.calculate_proposal(ctx_low)
    # 10 / 0.2 = 50 dials
    assert prop_low.suggested_calls == 50

    # High answer rate (70%) -> requires fewer dials
    ctx_high = PacingContext(
        available_agents=10, busy_agents=0, calls_in_flight=0,
        historical_answer_rate=0.7, avg_call_duration_seconds=180.0
    )
    prop_high = engine.calculate_proposal(ctx_high)
    # 10 / 0.7 = 14.28 -> 14 dials
    assert prop_high.suggested_calls == 14
