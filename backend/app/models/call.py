from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func, Index
from app.database import Base
from app.state_machines.call_state import CallState

class Call(Base):
    __tablename__ = "calls"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    borrower_id = Column(Integer, ForeignKey("borrowers.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    
    status = Column(String(50), default=CallState.QUEUED.value, nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    
    provider_name = Column(String(50), nullable=True)
    provider_call_id = Column(String(100), nullable=True, index=True)
    worker_id = Column(String(100), nullable=True)
    
    queued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reserved_at = Column(DateTime(timezone=True), nullable=True)
    initiated_at = Column(DateTime(timezone=True), nullable=True)
    ringing_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    
    duration_seconds = Column(Float, default=0.0, nullable=False)
    failure_reason = Column(String(255), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_calls_status_campaign", "status", "campaign_id"),
    )

    def __repr__(self):
        return f"<Call id={self.id} status='{self.status}' agent_id={self.agent_id} borrower_id={self.borrower_id}>"
