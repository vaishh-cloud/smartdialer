from enum import Enum
from typing import List, Optional
import datetime
from app.providers.base import ProviderHealth

class CircuitState(str, Enum):
    CLOSED = "CLOSED"      # Normal healthy operation
    OPEN = "OPEN"          # Provider tripping; reject calls
    HALF_OPEN = "HALF_OPEN"  # Testing recovery with canary probe

class ProviderCircuitBreaker:
    """
    Circuit Breaker protecting the dialer against telecom provider outages,
    consecutive network timeouts, and degraded telecom infrastructure.
    """

    def __init__(
        self,
        provider_name: str,
        failure_threshold: float = 0.5,  # 50% failure rate trips breaker
        window_size: int = 10,
        cooldown_seconds: int = 15,
    ):
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.window_size = window_size
        self.cooldown_seconds = cooldown_seconds

        self.state = CircuitState.CLOSED
        self.call_history: List[bool] = []  # True = success, False = failure
        self.last_state_change = datetime.datetime.now(datetime.timezone.utc)
        self.consecutive_failures = 0

    def can_execute(self) -> bool:
        """Determines whether a call to the provider is currently permitted."""
        now = datetime.datetime.now(datetime.timezone.utc)
        
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = (now - self.last_state_change).total_seconds()
            if elapsed >= self.cooldown_seconds:
                # Transition to HALF_OPEN to test provider health with a probe
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow canary trial
            return True

        return False

    def record_result(self, success: bool) -> None:
        """Records the outcome of a provider request and evaluates state transitions."""
        now = datetime.datetime.now(datetime.timezone.utc)
        
        self.call_history.append(success)
        if len(self.call_history) > self.window_size:
            self.call_history.pop(0)

        if success:
            self.consecutive_failures = 0
            if self.state == CircuitState.HALF_OPEN:
                # Canary probe succeeded; provider recovered
                self.state = CircuitState.CLOSED
                self.last_state_change = now
                self.call_history.clear()
        else:
            self.consecutive_failures += 1
            if self.state == CircuitState.HALF_OPEN:
                # Canary failed; return immediately to OPEN
                self.state = CircuitState.OPEN
                self.last_state_change = now
            elif self.state == CircuitState.CLOSED:
                if len(self.call_history) >= 4:
                    failures = self.call_history.count(False)
                    failure_rate = failures / len(self.call_history)
                    if failure_rate >= self.failure_threshold:
                        self.state = CircuitState.OPEN
                        self.last_state_change = now

    def get_health(self) -> ProviderHealth:
        failures = self.call_history.count(False)
        total = len(self.call_history)
        rate = (failures / total) if total > 0 else 0.0

        return ProviderHealth(
            provider_name=self.provider_name,
            is_healthy=(self.state != CircuitState.OPEN),
            consecutive_failures=self.consecutive_failures,
            recent_failure_rate=round(rate, 2),
            last_check=datetime.datetime.now(datetime.timezone.utc),
        )
