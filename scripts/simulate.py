"""
CredResolve SmartDialer Multi-Scenario Simulation Script.
Runs Scenarios A, B, C, D as defined in the assignment brief:
- Scenario A: Answer Rate 20%, Avg Talk Time 120s
- Scenario B: Answer Rate 50%, Avg Talk Time 90s
- Scenario C: Answer Rate 70%, Avg Talk Time 180s
- Scenario D: Volatile / Changing conditions with Provider B latency & failures

Measures and outputs:
- Agent utilization %
- Calls initiated
- Calls connected
- Pacing calculations
- Safety controller decisions & interventions
"""

import sys
import os
from pathlib import Path
import random
import time

# Ensure backend directory is in python path
backend_path = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.agent import Agent
from app.models.borrower import Borrower
from app.models.campaign import Campaign
from app.models.call import Call
from app.state_machines.agent_state import AgentState
from app.state_machines.call_state import CallState
from app.providers.provider_a import ProviderA
from app.providers.provider_b import ProviderB
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.safety.safety_controller import SafetyController
from app.engine.dialer_worker import DialerWorker
from app.events.event_processor import EventProcessor
from app.recovery.reconciler import StateReconciler

def setup_simulation_db(num_agents=20, num_borrowers=100, mode="PREDICTIVE"):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    campaign = Campaign(name="Simulation Campaign", mode=mode)
    session.add(campaign)
    session.commit()

    for i in range(num_agents):
        session.add(Agent(name=f"Agent-{i+1}", status=AgentState.AVAILABLE.value))

    for i in range(num_borrowers):
        session.add(Borrower(name=f"Borrower-{i+1}", phone=f"+919800{i:04d}", priority=random.randint(1, 5)))

    session.commit()
    return session, campaign.id

def run_scenario(name, answer_rate, avg_talk_time, provider_type="A", cycles=10, changing=False):
    print(f"\n{'='*75}")
    print(f"RUNNING SCENARIO: {name}")
    print(f"Config: Answer Rate={answer_rate if not changing else 'Dynamic (20%-70%)'}, "
          f"Talk Time={avg_talk_time}s, Provider={provider_type}, Cycles={cycles}")
    print(f"{'='*75}")

    session, campaign_id = setup_simulation_db(num_agents=15, num_borrowers=150, mode="PREDICTIVE")
    
    if provider_type == "A":
        provider = ProviderA(failure_rate=0.01, seed=42)
    else:
        provider = ProviderB(failure_rate=0.08, duplicate_event_rate=0.2, out_of_order_rate=0.2, seed=42)

    cb = ProviderCircuitBreaker(provider_name=provider.name, failure_threshold=0.5)
    safety = SafetyController(max_abandon_rate=0.03, max_overdial_ratio=2.5)
    worker = DialerWorker("sim-worker-1", session, provider, cb, safety)
    processor = EventProcessor(session)
    reconciler = StateReconciler(session, stale_reservation_timeout_sec=5, stale_initiated_timeout_sec=10)

    total_initiated = 0
    total_connected = 0
    total_completed = 0
    total_failed = 0
    safety_approvals = 0
    safety_reductions = 0
    safety_fallbacks = 0
    safety_rejections = 0
    active_talk_ticks = 0
    total_possible_agent_ticks = 0

    rng = random.Random(101)

    # Track in-flight calls: dict of call_id -> {remaining_cycles, answered, duration}
    active_conversations = {}

    for cycle in range(1, cycles + 1):
        current_answer_rate = rng.uniform(0.15, 0.75) if changing else answer_rate
        
        # 1. Step ongoing calls: reduce remaining talk duration
        finished_call_ids = []
        for c_id, conv in list(active_conversations.items()):
            conv["remaining_cycles"] -= 1
            if conv["remaining_cycles"] <= 0:
                finished_call_ids.append(c_id)

        # Process completion for finished calls
        for c_id in finished_call_ids:
            conv = active_conversations.pop(c_id)
            call = session.get(Call, c_id)
            if call:
                comp_evt = provider.generate_events_for_call(
                    call_id=call.id,
                    provider_call_id=call.provider_call_id or f"mock_{call.id}",
                    answered=True,
                    duration_sec=conv["duration"],
                )[-1]  # The COMPLETED event
                processor.process_event(comp_evt)
                total_completed += 1

        # Simulate agents in WRAP_UP completing back to AVAILABLE
        wrap_up_agents = session.query(Agent).filter(Agent.status == AgentState.WRAP_UP.value).all()
        for a in wrap_up_agents:
            a.status = AgentState.AVAILABLE.value
            a.version += 1
        session.commit()

        # 2. Worker runs dialing cycle
        result = worker.run_dial_cycle(campaign_id)
        
        if result.safety_decision == "APPROVE":
            safety_approvals += 1
        elif result.safety_decision == "REDUCE":
            safety_reductions += 1
        elif result.safety_decision == "FALLBACK_PROGRESSIVE":
            safety_fallbacks += 1
        else:
            safety_rejections += 1

        total_initiated += result.calls_placed

        # 3. For newly placed calls, decide answer outcome and schedule talk duration
        newly_initiated = session.query(Call).filter(
            Call.status == CallState.INITIATED.value
        ).all()

        for call in newly_initiated:
            is_answered = (rng.random() < current_answer_rate)
            if is_answered:
                total_connected += 1
                # Transition to ANSWERED
                ans_evt = provider.generate_events_for_call(
                    call_id=call.id,
                    provider_call_id=call.provider_call_id or f"mock_{call.id}",
                    answered=True,
                )[1]  # ANSWERED event
                processor.process_event(ans_evt)

                # Assign call to an available agent if not yet assigned (predictive mode)
                if not call.agent_id:
                    free_agent = session.query(Agent).filter(Agent.status == AgentState.AVAILABLE.value).first()
                    if free_agent:
                        free_agent.status = AgentState.CONNECTED.value
                        free_agent.current_call_id = call.id
                        call.agent_id = free_agent.id
                        session.commit()

                # Schedule 2-3 cycles of talk time
                talk_cycles = max(1, int(avg_talk_time / 45.0))
                active_conversations[call.id] = {
                    "remaining_cycles": talk_cycles,
                    "duration": avg_talk_time,
                }
            else:
                total_failed += 1
                fail_evt = provider.generate_events_for_call(
                    call_id=call.id,
                    provider_call_id=call.provider_call_id or f"mock_{call.id}",
                    answered=False,
                )[-1]  # FAILED event
                processor.process_event(fail_evt)

        # Run reconciler periodically
        reconciler.reconcile_all()

        # Track agent utilization for this tick
        busy = session.query(Agent).filter(Agent.status.in_([AgentState.DIALING.value, AgentState.CONNECTED.value])).count()
        total_agents = session.query(Agent).count()
        active_talk_ticks += busy
        total_possible_agent_ticks += total_agents

        print(f"Cycle {cycle:02d} | Free Agts: {result.available_agents:2d} | "
              f"Pacing Proposed: {result.proposed_calls:2d} | Safety: {result.safety_decision:<20} | "
              f"Calls Placed: {result.calls_placed:2d} | Active In-Call: {busy:2d}/{total_agents}")

    utilization = (active_talk_ticks / total_possible_agent_ticks) * 100 if total_possible_agent_ticks > 0 else 0.0

    print(f"\n--- SCENARIO {name} SUMMARY ---")
    print(f"Total Calls Initiated:     {total_initiated}")
    print(f"Total Calls Connected:     {total_connected}")
    print(f"Total Calls Completed:     {total_completed}")
    print(f"Total Calls Failed/Unans:  {total_failed}")
    print(f"Safety Decisions:          {safety_approvals} Approvals, {safety_reductions} Clamps/Reductions, "
          f"{safety_fallbacks} Fallbacks, {safety_rejections} Rejections")
    print(f"Overall Agent Utilization: {utilization:.1f}%")

    session.close()

if __name__ == "__main__":
    print("===========================================================================")
    print("      CredResolve SmartDialer Technical Recruitment Simulation             ")
    print("===========================================================================")
    
    # Scenario A: 20% answer rate, 120s avg talk time
    run_scenario("A (Low Answer Rate 20%, 120s Talk)", answer_rate=0.20, avg_talk_time=120, provider_type="A", cycles=8)

    # Scenario B: 50% answer rate, 90s avg talk time
    run_scenario("B (Moderate Answer Rate 50%, 90s Talk)", answer_rate=0.50, avg_talk_time=90, provider_type="A", cycles=8)

    # Scenario C: 70% answer rate, 180s avg talk time
    run_scenario("C (High Answer Rate 70%, 180s Talk)", answer_rate=0.70, avg_talk_time=180, provider_type="A", cycles=8)

    # Scenario D: Changing conditions & Provider B anomalies
    run_scenario("D (Volatile Dynamic Conditions + Provider B Latency/Failures)", answer_rate=0.40, avg_talk_time=100, provider_type="B", cycles=8, changing=True)
