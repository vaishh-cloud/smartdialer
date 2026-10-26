from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, UniqueConstraint, Index
from app.database import Base

class CallEventLog(Base):
    __tablename__ = "call_event_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Idempotency key supplied by provider or deterministically hashed from event attributes
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    
    event_type = Column(String(50), nullable=False)
    sequence_num = Column(Integer, nullable=True)  # Provider event sequence if provided
    payload_json = Column(Text, nullable=True)
    
    processed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_call_event_call_id_type", "call_id", "event_type"),
    )

    def __repr__(self):
        return f"<CallEventLog call_id={self.call_id} type='{self.event_type}' key='{self.idempotency_key}'>"
