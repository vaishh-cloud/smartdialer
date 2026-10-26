import uuid
import random
import datetime
from typing import List, Optional

from app.providers.base import TelecomProvider, ProviderCallResponse, ProviderCallEvent
from app.state_machines.call_state import CallState

class ProviderB(TelecomProvider):
    """
    Mock Telecom Provider B:
    - Slower latency (100-300ms)
    - Higher timeout rate (configurable)
    - Emits noisy events: duplicate callbacks and out-of-order deliveries
    - Seedable for deterministic testing
    """

    def __init__(
        self,
        failure_rate: float = 0.05,
        duplicate_event_rate: float = 0.3,
        out_of_order_rate: float = 0.3,
        seed: Optional[int] = None,
    ):
        self.failure_rate = failure_rate
        self.duplicate_event_rate = duplicate_event_rate
        self.out_of_order_rate = out_of_order_rate
        self.rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "ProviderB"

    def initiate_call(self, call_id: int, phone_number: str) -> ProviderCallResponse:
        latency_ms = self.rng.uniform(100.0, 300.0)

        # Timeout / failure simulation
        if self.rng.random() < self.failure_rate:
            return ProviderCallResponse(
                success=False,
                error_message="ProviderB gateway timeout (504)",
                latency_ms=latency_ms,
            )

        provider_call_id = f"provB_{uuid.uuid4().hex[:12]}"
        return ProviderCallResponse(
            success=True,
            provider_call_id=provider_call_id,
            latency_ms=latency_ms,
        )

    def hangup_call(self, provider_call_id: str) -> bool:
        return True

    def generate_events_for_call(
        self,
        call_id: int,
        provider_call_id: str,
        answered: bool,
        duration_sec: float = 30.0,
        force_duplicates: bool = False,
        force_out_of_order: bool = False,
    ) -> List[ProviderCallEvent]:
        """
        Generates call events, intentionally introducing duplicate callbacks
        and out-of-order delivery based on configuration or flags.
        """
        events: List[ProviderCallEvent] = []
        base_time = datetime.datetime.now(datetime.timezone.utc)
        seq = 1

        ringing_evt = ProviderCallEvent(
            idempotency_key=f"{provider_call_id}_RINGING",
            provider_name=self.name,
            provider_call_id=provider_call_id,
            call_id=call_id,
            event_type=CallState.RINGING.value,
            timestamp=base_time + datetime.timedelta(seconds=1),
            sequence_num=seq,
        )
        seq += 1

        if answered:
            answered_evt = ProviderCallEvent(
                idempotency_key=f"{provider_call_id}_ANSWERED",
                provider_name=self.name,
                provider_call_id=provider_call_id,
                call_id=call_id,
                event_type=CallState.ANSWERED.value,
                timestamp=base_time + datetime.timedelta(seconds=4),
                sequence_num=seq,
            )
            seq += 1

            completed_evt = ProviderCallEvent(
                idempotency_key=f"{provider_call_id}_COMPLETED",
                provider_name=self.name,
                provider_call_id=provider_call_id,
                call_id=call_id,
                event_type=CallState.COMPLETED.value,
                timestamp=base_time + datetime.timedelta(seconds=4 + duration_sec),
                sequence_num=seq,
                payload={"duration_seconds": duration_sec},
            )

            # Check if out-of-order should occur: e.g. [COMPLETED, ANSWERED, RINGING]
            should_reorder = force_out_of_order or (self.rng.random() < self.out_of_order_rate)
            if should_reorder:
                # Deliver COMPLETED first, then ANSWERED, then RINGING
                events.extend([completed_evt, answered_evt, ringing_evt])
            else:
                events.extend([ringing_evt, answered_evt, completed_evt])

            # Check if duplicates should be injected
            should_duplicate = force_duplicates or (self.rng.random() < self.duplicate_event_rate)
            if should_duplicate:
                # Duplicate ANSWERED event with the exact same idempotency key
                dup_answered = answered_evt.model_copy()
                events.append(dup_answered)

        else:
            failed_evt = ProviderCallEvent(
                idempotency_key=f"{provider_call_id}_FAILED",
                provider_name=self.name,
                provider_call_id=provider_call_id,
                call_id=call_id,
                event_type=CallState.FAILED.value,
                timestamp=base_time + datetime.timedelta(seconds=15),
                sequence_num=seq,
                payload={"reason": "BUSY_OR_UNREACHABLE"},
            )
            events.extend([ringing_evt, failed_evt])

        return events
