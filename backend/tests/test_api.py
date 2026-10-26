from fastapi.testclient import TestClient
from app.api.main import app
from app.database import Base, engine

def test_api_endpoints():
    Base.metadata.create_all(bind=engine)
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
