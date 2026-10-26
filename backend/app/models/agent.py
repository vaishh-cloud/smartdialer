from sqlalchemy import Column, Integer, String, DateTime, func, Index
from app.database import Base
from app.state_machines.agent_state import AgentState

class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, default=AgentState.AVAILABLE.value, index=True)
    version = Column(Integer, nullable=False, default=1)  # Optimistic concurrency / CAS version
    
    reserved_by = Column(String(100), nullable=True)  # Worker ID holding the reservation
    reserved_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    current_call_id = Column(Integer, nullable=True)
    total_calls_handled = Column(Integer, default=0, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_agents_status_version", "status", "version"),
    )

    def __repr__(self):
        return f"<Agent id={self.id} name='{self.name}' status='{self.status}' v={self.version}>"
