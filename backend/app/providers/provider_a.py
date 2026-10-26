import uuid
import time
import random
import datetime
from typing import List, Optional

from app.providers.base import TelecomProvider, ProviderCallResponse, ProviderCallEvent
from app.state_machines.call_state import CallState

class ProviderA(TelecomProvider):
    """
    Mock Telecom Provider A:
    - High reliability (<1% failure rate)
    - Low latency (10-30ms)
    - Clean, deterministic in-order event sequence
    """

    def __init__(self, failure_rate: float = 0.0, seed: Optional[int] = None):
        self.failure_rate = failure_rate
        self.rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "ProviderA"

    def initiate_call(self, call_id: int, phone_number: str) -> ProviderCallResponse:
        # Simulate slight latency
        latency_ms = self.rng.uniform(10.0, 30.0)
        
        # Check simulated failure
        if self.rng.random() < self.failure_rate:
            return ProviderCallResponse(
                success=False,
                error_message="ProviderA network error / congestion",
                latency_ms=latency_ms,
            )

        provider_call_id = f"provA_{uuid.uuid4().hex[:12]}"
        return ProviderCallResponse(
            success=True,
            provider_call_id=provider_call_id,
            latency_ms=latency_ms,
        )

    def hangup_call(self, provider_call_id: str) -> bool:
        return True

    def generate_events_for_call(
        self, call_id: int, provider_call_id: str, answered: bool, duration_sec: float = 30.0
    ) -> List[ProviderCallEvent]:
        """Generates clean, chronological events for call lifecycle."""
        events: List[ProviderCallEvent] = []
        base_time = datetime.datetime.now(datetime.timezone.utc)
        seq = 1

        # Event 1: RINGING
        events.append(
            ProviderCallEvent(
                idempotency_key=f"{provider_call_id}_RINGING",
                provider_name=self.name,
                provider_call_id=provider_call_id,
                call_id=call_id,
                event_type=CallState.RINGING.value,
                timestamp=base_time + datetime.timedelta(seconds=1),
                sequence_num=seq,
            )
        )
        seq += 1

        if answered:
            # Event 2: ANSWERED
            events.append(
                ProviderCallEvent(
                    idempotency_key=f"{provider_call_id}_ANSWERED",
                    provider_name=self.name,
                    provider_call_id=provider_call_id,
                    call_id=call_id,
                    event_type=CallState.ANSWERED.value,
                    timestamp=base_time + datetime.timedelta(seconds=5),
                    sequence_num=seq,
                )
            )
            seq += 1

            # Event 3: COMPLETED
            events.append(
                ProviderCallEvent(
                    idempotency_key=f"{provider_call_id}_COMPLETED",
                    provider_name=self.name,
                    provider_call_id=provider_call_id,
                    call_id=call_id,
                    event_type=CallState.COMPLETED.value,
                    timestamp=base_time + datetime.timedelta(seconds=5 + duration_sec),
                    sequence_num=seq,
                    payload={"duration_seconds": duration_sec},
                )
            )
        else:
            # Event 2: FAILED (No answer / line busy)
            events.append(
                ProviderCallEvent(
                    idempotency_key=f"{provider_call_id}_FAILED",
                    provider_name=self.name,
                    provider_call_id=provider_call_id,
                    call_id=call_id,
                    event_type=CallState.FAILED.value,
                    timestamp=base_time + datetime.timedelta(seconds=20),
                    sequence_num=seq,
                    payload={"reason": "NO_ANSWER"},
                )
            )

        return events
