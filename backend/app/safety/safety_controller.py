from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel
from app.pacing.base import PacingProposal, PacingContext

class SafetyDecisionType(str, Enum):
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    REJECT = "REJECT"
    FALLBACK_PROGRESSIVE = "FALLBACK_PROGRESSIVE"

class SafetyDecision(BaseModel):
    decision: SafetyDecisionType
    allowed_calls: int
    original_requested: int
    effective_mode: str
    reasons: list[str]
    safety_metrics: Dict[str, Any]

class SafetyController:
    """
    Independent Safety Controller.
    Positioned strictly between Pacing Engine and Call Allocator.
    Guarantees compliance, prevents call abandonment spikes, and protects agents.
    The pacing engine has NO authority or mechanism to bypass or disable this controller.
    """

    def __init__(
        self,
        max_abandon_rate: float = 0.03,  # Max 3% compliance limit
        max_overdial_ratio: float = 2.0,  # Max 2x calls per available agent
        min_available_agents_for_predictive: int = 3, # Need min pool for statistical predictive dialing
    ):
        self.max_abandon_rate = max_abandon_rate
        self.max_overdial_ratio = max_overdial_ratio
        self.min_available_agents_for_predictive = min_available_agents_for_predictive

    def evaluate(self, proposal: PacingProposal, context: PacingContext) -> SafetyDecision:
        reasons = []
        original_requested = max(0, proposal.suggested_calls)

        # 1. Provider Health Gate: If telecom provider is failing, REJECT all new outbound calls
        if not context.provider_healthy:
            reasons.append("Telecom provider reported unhealthy / circuit breaker tripped.")
            return SafetyDecision(
                decision=SafetyDecisionType.REJECT,
                allowed_calls=0,
                original_requested=original_requested,
                effective_mode="REJECTED_PROVIDER_OUTAGE",
                reasons=reasons,
                safety_metrics={"provider_healthy": False}
            )

        # 2. Agent Pool Floor: If zero available agents AND zero busy agents, REJECT
        total_agents = context.available_agents + context.busy_agents
        if total_agents == 0:
            reasons.append("Zero agents in campaign pool.")
            return SafetyDecision(
                decision=SafetyDecisionType.REJECT,
                allowed_calls=0,
                original_requested=original_requested,
                effective_mode="REJECTED_NO_AGENTS",
                reasons=reasons,
                safety_metrics={"total_agents": 0}
            )

        # 3. Compliance Abandon Rate Gate:
        # If current abandon rate exceeds legal compliance threshold, force immediate progressive fallback
        if context.recent_abandon_rate >= self.max_abandon_rate:
            reasons.append(
                f"Abandon rate ({context.recent_abandon_rate:.1%}) exceeds safety threshold ({self.max_abandon_rate:.1%}). "
                "Triggering fallback to 1:1 progressive dialing."
            )
            progressive_safe = max(0, context.available_agents - context.calls_in_flight)
            allowed = min(original_requested, progressive_safe)
            return SafetyDecision(
                decision=SafetyDecisionType.FALLBACK_PROGRESSIVE,
                allowed_calls=allowed,
                original_requested=original_requested,
                effective_mode="FALLBACK_PROGRESSIVE",
                reasons=reasons,
                safety_metrics={
                    "recent_abandon_rate": context.recent_abandon_rate,
                    "max_abandon_rate": self.max_abandon_rate,
                    "progressive_safe": progressive_safe,
                }
            )

        # 4. Mode-specific safety verification
        if proposal.mode == "PROGRESSIVE":
            # In Progressive mode, strict 1:1 constraint:
            # Outbound calls must not exceed currently available agents minus in-flight calls
            safe_progressive_limit = max(0, context.available_agents - context.calls_in_flight)
            
            if original_requested > safe_progressive_limit:
                reasons.append(
                    f"Progressive safety clamp: Requested {original_requested} exceeds free slots {safe_progressive_limit}."
                )
                return SafetyDecision(
                    decision=SafetyDecisionType.REDUCE,
                    allowed_calls=safe_progressive_limit,
                    original_requested=original_requested,
                    effective_mode="PROGRESSIVE",
                    reasons=reasons,
                    safety_metrics={"progressive_limit": safe_progressive_limit}
                )
            
            if original_requested == 0:
                reasons.append("No free agent slots for progressive call.")
                return SafetyDecision(
                    decision=SafetyDecisionType.REJECT,
                    allowed_calls=0,
                    original_requested=0,
                    effective_mode="PROGRESSIVE",
                    reasons=reasons,
                    safety_metrics={"progressive_limit": safe_progressive_limit}
                )

            reasons.append("Progressive dialing proposal within safe limits.")
            return SafetyDecision(
                decision=SafetyDecisionType.APPROVE,
                allowed_calls=original_requested,
                original_requested=original_requested,
                effective_mode="PROGRESSIVE",
                reasons=reasons,
                safety_metrics={"progressive_limit": safe_progressive_limit}
            )

        # 5. Predictive Mode Safety Boundaries:
        # Check if agent pool is too small for statistical law of large numbers
        if context.available_agents < self.min_available_agents_for_predictive and context.available_agents > 0:
            reasons.append(
                f"Small agent pool ({context.available_agents} < {self.min_available_agents_for_predictive}). "
                "Predictive statistics unreliable; falling back to progressive mode."
            )
            safe_progressive = max(0, context.available_agents - context.calls_in_flight)
            return SafetyDecision(
                decision=SafetyDecisionType.FALLBACK_PROGRESSIVE,
                allowed_calls=safe_progressive,
                original_requested=original_requested,
                effective_mode="FALLBACK_PROGRESSIVE",
                reasons=reasons,
                safety_metrics={"available_agents": context.available_agents}
            )

        # Check total active calls ceiling (in-flight + proposed) vs total agent pool
        max_allowed_in_flight = int(total_agents * self.max_overdial_ratio)
        current_in_flight = context.calls_in_flight
        allowed_new_calls = max(0, max_allowed_in_flight - current_in_flight)

        if original_requested > allowed_new_calls:
            reasons.append(
                f"Overdial limit reached: Requested {original_requested}, allowed ceiling is {allowed_new_calls} "
                f"(max in-flight {max_allowed_in_flight}, current in-flight {current_in_flight})."
            )
            return SafetyDecision(
                decision=SafetyDecisionType.REDUCE if allowed_new_calls > 0 else SafetyDecisionType.REJECT,
                allowed_calls=allowed_new_calls,
                original_requested=original_requested,
                effective_mode="PREDICTIVE_CLAMPED",
                reasons=reasons,
                safety_metrics={
                    "max_allowed_in_flight": max_allowed_in_flight,
                    "current_in_flight": current_in_flight,
                    "clamped_calls": allowed_new_calls,
                }
            )

        if original_requested == 0:
            reasons.append("Zero calls proposed by predictive engine.")
            return SafetyDecision(
                decision=SafetyDecisionType.REJECT,
                allowed_calls=0,
                original_requested=0,
                effective_mode="PREDICTIVE",
                reasons=reasons,
                safety_metrics={}
            )

        reasons.append("Predictive proposal verified and approved within safe operational envelope.")
        return SafetyDecision(
            decision=SafetyDecisionType.APPROVE,
            allowed_calls=original_requested,
            original_requested=original_requested,
            effective_mode="PREDICTIVE",
            reasons=reasons,
            safety_metrics={"allowed_calls": original_requested}
        )
