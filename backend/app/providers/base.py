from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
import datetime

class ProviderCallResponse(BaseModel):
    success: bool
    provider_call_id: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0

class ProviderCallEvent(BaseModel):
    idempotency_key: str
    provider_name: str
    provider_call_id: str
    call_id: int
    event_type: str  # e.g., RINGING, ANSWERED, COMPLETED, FAILED
    timestamp: datetime.datetime
    sequence_num: Optional[int] = None
    payload: Dict[str, Any] = {}

class ProviderHealth(BaseModel):
    provider_name: str
    is_healthy: bool
    consecutive_failures: int
    recent_failure_rate: float
    last_check: datetime.datetime

class TelecomProvider(ABC):
    """Common interface for telecom provider integrations."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def initiate_call(self, call_id: int, phone_number: str) -> ProviderCallResponse:
        """Initiate an outbound call to the given phone number."""
        pass

    @abstractmethod
    def hangup_call(self, provider_call_id: str) -> bool:
        """Terminate an active call."""
        pass

    @abstractmethod
    def generate_events_for_call(
        self, call_id: int, provider_call_id: str, answered: bool, duration_sec: float
    ) -> List[ProviderCallEvent]:
        """
        Generate lifecycle events for testing and simulation.
        Allows mock providers to emit clean or noisy/out-of-order events.
        """
        pass
