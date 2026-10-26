from app.pacing.base import BasePacingEngine, PacingContext, PacingProposal

class ProgressivePacingEngine(BasePacingEngine):
    """
    Progressive Dialing Mode:
    Strict 1:1 ratio between available agents and outbound calls.
    Predictable, deterministic, zero-abandon risk.
    """

    def calculate_proposal(self, context: PacingContext) -> PacingProposal:
        # Number of outbound calls cannot exceed currently free available agents
        # Accounts for calls already dialing or ringing (in flight)
        effective_free_slots = max(0, context.available_agents - context.calls_in_flight)
        
        reasoning = (
            f"Progressive mode: {context.available_agents} available agents, "
            f"{context.calls_in_flight} calls in-flight -> {effective_free_slots} new dials proposed."
        )

        return PacingProposal(
            mode="PROGRESSIVE",
            suggested_calls=effective_free_slots,
            reasoning=reasoning,
            metrics_snapshot={
                "available_agents": context.available_agents,
                "calls_in_flight": context.calls_in_flight,
                "effective_free_slots": effective_free_slots,
            }
        )
