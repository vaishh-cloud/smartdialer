from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class Settings(BaseSettings):
    PROJECT_NAME: str = "CredResolve SmartDialer"
    DEBUG: bool = False
    
    # Database
    DATABASE_URL: str = "sqlite:///./smartdialer.db"
    DB_ECHO: bool = False
    
    # Dialing parameters
    DEFAULT_DIALING_MODE: str = "PROGRESSIVE"  # PROGRESSIVE or PREDICTIVE
    PROGRESSIVE_CALL_RATIO: float = 1.0  # 1 outbound call per available agent
    MAX_ABANDON_RATE_PERCENT: float = 3.0  # Compliance limit (e.g. 3% max abandon)
    MAX_OVERDIAL_RATIO: float = 2.5        # Max calls per available agent in predictive
    
    # Worker & Recovery timeouts
    STALE_RESERVATION_TIMEOUT_SECONDS: int = 15  # Agent reserved but call not placed
    STALE_INITIATED_TIMEOUT_SECONDS: int = 30    # Call initiated with provider but no event
    MAX_RING_TIMEOUT_SECONDS: int = 45          # Maximum ring duration before timing out
    WRAP_UP_DURATION_SECONDS: int = 10          # Agent wrap up duration
    
    # Circuit breaker settings
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: float = 0.5  # 50% errors trips breaker
    CIRCUIT_BREAKER_WINDOW_SIZE: int = 10
    CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 15
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
