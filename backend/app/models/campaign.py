from sqlalchemy import Column, Integer, String, Float, Text, DateTime, func
from app.database import Base

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    
    # PROGRESSIVE or PREDICTIVE
    mode = Column(String(50), default="PROGRESSIVE", nullable=False)
    # ACTIVE, PAUSED, COMPLETED
    status = Column(String(50), default="ACTIVE", nullable=False)
    
    target_abandon_rate = Column(Float, default=0.03, nullable=False)  # Max allowed abandon rate (e.g. 3%)
    config_json = Column(Text, nullable=True)  # Additional configuration
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Campaign id={self.id} name='{self.name}' mode='{self.mode}' status='{self.status}'>"
