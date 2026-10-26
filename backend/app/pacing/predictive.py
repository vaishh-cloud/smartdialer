import math
from app.pacing.base import BasePacingEngine, PacingContext, PacingProposal

class PredictivePacingEngine(BasePacingEngine):
    """
    Predictive Pacing Engine:
    Uses historical answer rates, average call talk times, call setup times,
    and expected agent liberation to pace outbound calls and maximize agent utilization
    while minimizing abandoned calls.
    """

    def __init__(self, max_overdial_multiplier: float = 2.5):
        self.max_overdial_multiplier = max_overdial_multiplier

    def calculate_proposal(self, context: PacingContext) -> PacingProposal:
        # Clamp answer rate to valid operational range [0.05, 1.0]
        answer_rate = max(0.05, min(1.0, context.historical_answer_rate))
        avg_talk_time = max(10.0, context.avg_call_duration_seconds)
        ring_window = max(5.0, context.avg_ring_time_seconds)

        # 1. Expected agents freeing up during the call setup window
        # Rate of agent liberation lambda = busy_agents / avg_talk_time
        expected_freeing = context.busy_agents * (ring_window / avg_talk_time)
        
        # 2. Total anticipated agent capacity
        total_anticipated_capacity = context.available_agents + expected_freeing

        # 3. Expected answers from calls already in-flight
        expected_in_flight_answers = context.calls_in_flight * answer_rate

        # 4. Net capacity available for new dials
        net_capacity = max(0.0, total_anticipated_capacity - expected_in_flight_answers)

        # 5. Raw dials required to yield net_capacity answered calls
        raw_dials = net_capacity / answer_rate

        # 6. Apply max overdial ceiling to avoid catastrophic bursts
        total_agent_pool = max(1, context.available_agents + context.busy_agents)
        max_allowed_by_ceiling = total_agent_pool * self.max_overdial_multiplier
        clamped_dials = min(raw_dials, max_allowed_by_ceiling)
        suggested_calls = int(math.floor(clamped_dials))

        reasoning = (
            f"Predictive calculation: {context.available_agents} avail + "
            f"{expected_freeing:.2f} expected freeing (from {context.busy_agents} busy over {ring_window}s/{avg_talk_time}s) = "
            f"{total_anticipated_capacity:.2f} capacity. Minus {expected_in_flight_answers:.2f} in-flight answers "
            f"(from {context.calls_in_flight} calls @ {answer_rate:.0%}) -> Net capacity {net_capacity:.2f}. "
            f"At {answer_rate:.0%} answer rate -> {suggested_calls} dials."
        )

        return PacingProposal(
            mode="PREDICTIVE",
            suggested_calls=max(0, suggested_calls),
            reasoning=reasoning,
            metrics_snapshot={
                "available_agents": context.available_agents,
                "busy_agents": context.busy_agents,
                "calls_in_flight": context.calls_in_flight,
                "answer_rate": answer_rate,
                "expected_freeing_agents": round(expected_freeing, 2),
                "total_anticipated_capacity": round(total_anticipated_capacity, 2),
                "expected_in_flight_answers": round(expected_in_flight_answers, 2),
                "raw_calculated_dials": round(raw_dials, 2),
                "final_suggested_calls": max(0, suggested_calls),
            }
        )
