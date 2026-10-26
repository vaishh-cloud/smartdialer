from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from typing import List, Optional
from pydantic import BaseModel
import datetime

from app.database import get_db
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.models.call_event import CallEventLog
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState
from app.engine.dialer_worker import DialerWorker, DialCycleResult
from app.providers.provider_a import ProviderA
from app.providers.provider_b import ProviderB
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.safety.safety_controller import SafetyController
from app.events.event_processor import EventProcessor, EventProcessResult
from app.providers.base import ProviderCallEvent
from app.recovery.reconciler import StateReconciler

router = APIRouter(prefix="/api")

# In-memory singletons for demo / runtime
provider_a = ProviderA()
provider_b = ProviderB()
circuit_breaker = ProviderCircuitBreaker(provider_name="ProviderA")
safety_controller = SafetyController()

# Pydantic Schemas
class CreateAgentSchema(BaseModel):
    name: str
    status: Optional[str] = "AVAILABLE"

class CreateBorrowerSchema(BaseModel):
    name: str
    phone: str
    priority: int = 1

class CreateCampaignSchema(BaseModel):
    name: str
    mode: str = "PROGRESSIVE"

class WebhookEventSchema(BaseModel):
    idempotency_key: str
    provider_name: str
    provider_call_id: str
    call_id: int
    event_type: str
    duration_seconds: Optional[float] = None
    reason: Optional[str] = None

@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db)):
    return db.query(Campaign).all()

@router.post("/campaigns")
def create_campaign(schema: CreateCampaignSchema, db: Session = Depends(get_db)):
    camp = Campaign(name=schema.name, mode=schema.mode.upper())
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return camp

@router.post("/campaigns/{campaign_id}/mode")
def update_campaign_mode(campaign_id: int, mode: str, db: Session = Depends(get_db)):
    camp = db.get(Campaign, campaign_id)
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    camp.mode = mode.upper()
    db.commit()
    return {"id": camp.id, "mode": camp.mode}

@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    return db.query(Agent).all()

@router.post("/agents")
def create_agent(schema: CreateAgentSchema, db: Session = Depends(get_db)):
    agent = Agent(name=schema.name, status=schema.status)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent

@router.get("/borrowers")
def list_borrowers(db: Session = Depends(get_db)):
    return db.query(Borrower).limit(50).all()

@router.post("/borrowers")
def create_borrower(schema: CreateBorrowerSchema, db: Session = Depends(get_db)):
    b = Borrower(name=schema.name, phone=schema.phone, priority=schema.priority)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b

@router.get("/calls")
def list_calls(limit: int = 50, db: Session = Depends(get_db)):
    return db.query(Call).order_by(Call.id.desc()).limit(limit).all()

@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    available_agents = db.query(Agent).filter(Agent.status == AgentState.AVAILABLE.value).count()
    busy_agents = db.query(Agent).filter(Agent.status.in_([AgentState.DIALING.value, AgentState.CONNECTED.value])).count()
    total_agents = db.query(Agent).count()

    total_calls = db.query(Call).count()
    completed_calls = db.query(Call).filter(Call.status == CallState.COMPLETED.value).count()
    in_flight_calls = db.query(Call).filter(Call.status.in_([CallState.INITIATED.value, CallState.RINGING.value])).count()
    failed_calls = db.query(Call).filter(Call.status.in_([CallState.FAILED.value, CallState.CANCELLED.value])).count()

    health = circuit_breaker.get_health()

    return {
        "agents": {
            "available": available_agents,
            "busy": busy_agents,
            "total": total_agents,
        },
        "calls": {
            "in_flight": in_flight_calls,
            "completed": completed_calls,
            "failed": failed_calls,
            "total": total_calls,
        },
        "circuit_breaker": {
            "state": circuit_breaker.state.value,
            "healthy": health.is_healthy,
            "failure_rate": health.recent_failure_rate,
        }
    }

@router.post("/campaigns/{campaign_id}/dial-cycle")
def run_dial_cycle(campaign_id: int, provider: str = "A", db: Session = Depends(get_db)):
    prov = provider_a if provider.upper() == "A" else provider_b
    worker = DialerWorker(
        worker_id="api-worker",
        db=db,
        provider=prov,
        circuit_breaker=circuit_breaker,
        safety_controller=safety_controller,
    )
    result = worker.run_dial_cycle(campaign_id)
    return result

@router.post("/webhooks/telecom")
def process_webhook(event_data: WebhookEventSchema, db: Session = Depends(get_db)):
    processor = EventProcessor(db)
    evt = ProviderCallEvent(
        idempotency_key=event_data.idempotency_key,
        provider_name=event_data.provider_name,
        provider_call_id=event_data.provider_call_id,
        call_id=event_data.call_id,
        event_type=event_data.event_type,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        payload={
            "duration_seconds": event_data.duration_seconds,
            "reason": event_data.reason,
        }
    )
    res = processor.process_event(evt)
    return res

@router.post("/reconcile")
def run_reconciliation(db: Session = Depends(get_db)):
    reconciler = StateReconciler(db)
    report = reconciler.reconcile_all()
    return report.to_dict()

@router.post("/seed-demo")
def seed_demo_data(db: Session = Depends(get_db)):
    """Convenience endpoint to seed demo agents, borrowers, and campaign."""
    camp = db.query(Campaign).first()
    if not camp:
        camp = Campaign(name="Collections Q4 Campaign", mode="PROGRESSIVE")
        db.add(camp)
        db.commit()
        db.refresh(camp)

    if db.query(Agent).count() == 0:
        for i in range(10):
            db.add(Agent(name=f"Agent {i+1}", status=AgentState.AVAILABLE.value))

    if db.query(Borrower).count() == 0:
        for i in range(50):
            db.add(Borrower(name=f"Borrower {i+1}", phone=f"+9198765{i:04d}", priority=(i % 5) + 1))

    db.commit()
    return {"status": "seeded", "campaign_id": camp.id}
