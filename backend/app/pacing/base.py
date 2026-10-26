from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pydantic import BaseModel

class PacingContext(BaseModel):
    available_agents: int
    busy_agents: int
    calls_in_flight: int  # Calls currently ringing or awaiting answer
    historical_answer_rate: float  # e.g., 0.50 (50%)
    avg_call_duration_seconds: float = 90.0
    avg_ring_time_seconds: float = 15.0
    recent_abandon_rate: float = 0.0
    max_abandon_rate: float = 0.03
    provider_healthy: bool = True

class PacingProposal(BaseModel):
    mode: str
    suggested_calls: int
    reasoning: str
    metrics_snapshot: Dict[str, Any]

class BasePacingEngine(ABC):
    @abstractmethod
    def calculate_proposal(self, context: PacingContext) -> PacingProposal:
        """Calculates the proposed number of calls to initiate."""
        pass
