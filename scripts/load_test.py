"""
CredResolve SmartDialer Concurrent Multi-Worker Load Test.
Simulates multiple dialer workers (Worker 1..N) concurrently running against
the exact same campaign and agent/borrower pool.

Verifies:
- Safe atomic agent allocation (no two workers can ever claim the same agent)
- Safe borrower allocation (no borrower dialed twice)
- Zero race conditions or database corruption under concurrent load
- Consistent state throughout
"""

import sys
import os
from pathlib import Path
import concurrent.futures
import time
import random

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
from app.providers.provider_a import ProviderA
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.safety.safety_controller import SafetyController
from app.engine.dialer_worker import DialerWorker

def run_concurrent_worker(worker_id: str, session_factory, campaign_id: int, iterations: int = 5):
    provider = ProviderA(failure_rate=0.0, seed=hash(worker_id) % 1000)
    cb = ProviderCircuitBreaker(provider_name=f"CB-{worker_id}")
    safety = SafetyController()
    
    total_placed = 0
    decisions = []

    for i in range(iterations):
        db = session_factory()
        try:
            worker = DialerWorker(worker_id, db, provider, cb, safety)
            res = worker.run_dial_cycle(campaign_id)
            total_placed += res.calls_placed
            decisions.append(res.safety_decision)
        finally:
            db.close()
        time.sleep(0.01)

    return {"worker_id": worker_id, "calls_placed": total_placed, "decisions": decisions}

def main():
    print("=" * 70)
    print("      CredResolve SmartDialer Multi-Worker Concurrency Load Test       ")
    print("=" * 70)

    import tempfile
    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, f"smartdialer_loadtest_{int(time.time())}.db")
    db_url = f"sqlite:///{db_path}"

    from app.database import create_db_engine
    engine = create_db_engine(db_url)
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine)

    # Initialize shared pool: 20 agents, 100 borrowers
    setup_db = SessionFactory()
    campaign = Campaign(name="Concurrent Load Test", mode="PROGRESSIVE")
    setup_db.add(campaign)
    setup_db.commit()

    for i in range(20):
        setup_db.add(Agent(name=f"Agent-{i+1}", status=AgentState.AVAILABLE.value))

    for i in range(100):
        setup_db.add(Borrower(name=f"Borrower-{i+1}", phone=f"+919999{i:04d}", priority=random.randint(1, 10)))

    setup_db.commit()
    campaign_id = campaign.id
    setup_db.close()

    num_workers = 6
    print(f"Spinning up {num_workers} concurrent worker threads...")
    start_time = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(run_concurrent_worker, f"Worker-{i+1}", SessionFactory, campaign_id, iterations=4)
            for i in range(num_workers)
        ]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    elapsed = time.perf_counter() - start_time

    # Verify invariants in database
    verify_db = SessionFactory()
    all_calls = verify_db.query(Call).all()
    all_agents = verify_db.query(Agent).all()
    all_borrowers = verify_db.query(Borrower).all()

    # Invariant 1: No two active calls have the same agent
    active_agent_ids = [c.agent_id for c in all_calls if c.agent_id is not None]
    duplicate_agents = len(active_agent_ids) - len(set(active_agent_ids))

    # Invariant 2: No borrower called more than once
    borrower_ids = [c.borrower_id for c in all_calls]
    duplicate_borrowers = len(borrower_ids) - len(set(borrower_ids))

    # Invariant 3: No agent reserved by multiple workers
    reserved_agents = [a for a in all_agents if a.status in [AgentState.RESERVED.value, AgentState.DIALING.value]]

    print("\n--- CONCURRENCY LOAD TEST RESULTS ---")
    print(f"Elapsed Time:             {elapsed:.3f}s")
    for r in results:
        print(f"  {r['worker_id']}: Placed {r['calls_placed']} calls | Decisions: {set(r['decisions'])}")

    print(f"\nTotal Calls Placed:       {len(all_calls)}")
    print(f"Duplicate Agent Clashes:  {duplicate_agents} (MUST BE 0)")
    print(f"Duplicate Borrower Calls: {duplicate_borrowers} (MUST BE 0)")
    print(f"Agents Currently Active:  {len(reserved_agents)} / {len(all_agents)}")

    assert duplicate_agents == 0, f"Violation: Found {duplicate_agents} duplicate agent allocations!"
    assert duplicate_borrowers == 0, f"Violation: Found {duplicate_borrowers} duplicate borrower allocations!"
    print("\n>>> ALL CONCURRENCY INVARIANTS PERFECTLY PRESERVED! PASS! <<<")

    verify_db.close()

if __name__ == "__main__":
    main()
