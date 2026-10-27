from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.database import Base, get_db

def test_api_endpoints():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    had_previous = get_db in app.dependency_overrides
    prev_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            # Health & index
            res = client.get("/")
            assert res.status_code == 200
            assert "CredResolve SmartDialer" in res.text

            # Seed demo data
            res_seed = client.post("/api/seed-demo")
            assert res_seed.status_code == 200
            camp_id = res_seed.json()["campaign_id"]

            # Stats
            res_stats = client.get("/api/stats")
            assert res_stats.status_code == 200
            data = res_stats.json()
            assert data["agents"]["total"] >= 10
            assert data["circuit_breaker"]["state"] == "CLOSED"

            # Dial cycle
            res_dial = client.post(f"/api/campaigns/{camp_id}/dial-cycle")
            assert res_dial.status_code == 200
            dial_data = res_dial.json()
            assert dial_data["safety_decision"] in ["APPROVE", "REDUCE", "FALLBACK_PROGRESSIVE", "REJECT"]

            # Toggle mode
            res_mode = client.post(f"/api/campaigns/{camp_id}/mode?mode=PREDICTIVE")
            assert res_mode.status_code == 200
            assert res_mode.json()["mode"] == "PREDICTIVE"

            # Reconciler
            res_rec = client.post("/api/reconcile")
            assert res_rec.status_code == 200
    finally:
        if had_previous:
            app.dependency_overrides[get_db] = prev_override
        else:
            app.dependency_overrides.pop(get_db, None)
