from sqlalchemy import Column, Integer, String, DateTime, func, Index
from app.database import Base

class Borrower(Base):
    __tablename__ = "borrowers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=False, index=True)
    priority = Column(Integer, default=1, nullable=False, index=True)  # Higher is dialed first
    
    # Status: PENDING, RESERVED, CALLED, COMPLETED, DO_NOT_CALL
    status = Column(String(50), default="PENDING", nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    
    reserved_by = Column(String(100), nullable=True)
    reserved_at = Column(DateTime(timezone=True), nullable=True)
    call_attempts = Column(Integer, default=0, nullable=False)
    last_dialed_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_borrowers_status_priority", "status", "priority"),
    )

    def __repr__(self):
        return f"<Borrower id={self.id} name='{self.name}' phone='{self.phone}' status='{self.status}'>"
