"""
Tests fuer app/middleware/ratelimit.py.

Schwerpunkte:
  - _UserLimiter Sliding-Window-Logik (allow returns korrektes remaining)
  - /api/health, OPTIONS-Preflight bypassen Rate-Limit
  - Headers X-RateLimit-* werden gesetzt
  - 429 mit Retry-After bei Ueberschreitung
"""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.ratelimit import _UserLimiter, RateLimitMiddleware


# ─────────────────────────────────────────────────────────────────────────────
# _UserLimiter (direkte Unit-Tests, kein TestClient noetig)
# ─────────────────────────────────────────────────────────────────────────────


class TestUserLimiter:

    def test_erstes_request_allowed(self):
        lim = _UserLimiter()
        allowed, remaining = lim.allow("user1", max_per_hour=10)
        assert allowed is True
        assert remaining == 9

    def test_remaining_zaehlt_runter(self):
        lim = _UserLimiter()
        for i in range(5):
            allowed, remaining = lim.allow("user1", max_per_hour=10)
            assert allowed is True
            assert remaining == 10 - (i + 1)

    def test_genau_an_limit_letzter_request_durch(self):
        lim = _UserLimiter()
        for _ in range(10):
            allowed, _ = lim.allow("user1", max_per_hour=10)
            assert allowed is True
        # 11. Request: nicht mehr erlaubt
        allowed, remaining = lim.allow("user1", max_per_hour=10)
        assert allowed is False
        assert remaining == 0

    def test_unterschiedliche_user_unabhaengig(self):
        lim = _UserLimiter()
        for _ in range(10):
            lim.allow("user1", max_per_hour=10)
        # user1 erschoepft, user2 hat noch volles Budget
        allowed, remaining = lim.allow("user2", max_per_hour=10)
        assert allowed is True
        assert remaining == 9

    def test_alte_eintraege_werden_entfernt(self, monkeypatch):
        """Eintraege aelter als 3600s muessen aus dem Sliding-Window fliegen."""
        lim = _UserLimiter()

        # 5 Requests in der Vergangenheit (>1h alt)
        old_time = time.time() - 7200  # 2h alt
        for _ in range(5):
            lim._windows["user1"].append(old_time)

        # Neuer Request: alte 5 fliegen raus, dieser ist erster im Window
        allowed, remaining = lim.allow("user1", max_per_hour=10)
        assert allowed is True
        assert remaining == 9  # 10 - 1 (nur der neue zaehlt)


# ─────────────────────────────────────────────────────────────────────────────
# RateLimitMiddleware in FastAPI-App
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def test_app():
    """Frische FastAPI-App + Middleware fuer jeden Test (limiter ist global)."""
    # WICHTIG: Modul re-importieren, sonst teilen sich alle Tests denselben
    # _limiter-Singleton und Counter wandern zwischen Tests.
    import importlib
    import app.middleware.ratelimit as rl_mod
    importlib.reload(rl_mod)

    app = FastAPI()
    app.add_middleware(rl_mod.RateLimitMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/jobs/test")
    async def jobs():
        return {"ok": True}

    return app


class TestSkipPaths:

    def test_health_kein_ratelimit(self, test_app, monkeypatch):
        """Auch nach 1000 Requests bleibt /api/health erreichbar."""
        # Limit kuenstlich tief setzen
        monkeypatch.setattr(
            "app.middleware.ratelimit.settings.RATE_LIMIT_REQUESTS_PER_HOUR", 3,
            raising=False,
        )
        client = TestClient(test_app)
        for _ in range(20):
            r = client.get("/api/health")
            assert r.status_code == 200

    def test_options_request_kein_ratelimit(self, test_app, monkeypatch):
        monkeypatch.setattr(
            "app.middleware.ratelimit.settings.RATE_LIMIT_REQUESTS_PER_HOUR", 3,
            raising=False,
        )
        client = TestClient(test_app)
        for _ in range(10):
            client.options("/api/jobs/test")
        # Normale Route muss danach noch funktionieren (OPTIONS hat nicht
        # gezaehlt) - wir haetten sonst nach 3 Requests 429 bekommen
        r = client.get("/api/jobs/test", headers={"X-Systelios-User": "u1"})
        assert r.status_code == 200


class TestRateLimitHeaders:

    def test_headers_in_response(self, test_app):
        client = TestClient(test_app)
        r = client.get("/api/jobs/test", headers={"X-Systelios-User": "header_user"})
        assert r.status_code == 200
        assert "X-RateLimit-Limit" in r.headers
        assert "X-RateLimit-Remaining" in r.headers


class TestRateLimitTriggered:

    def test_429_nach_ueberschreitung(self, test_app, monkeypatch):
        monkeypatch.setattr(
            "app.middleware.ratelimit.settings.RATE_LIMIT_REQUESTS_PER_HOUR", 3,
            raising=False,
        )
        client = TestClient(test_app)
        headers = {"X-Systelios-User": "limited_user"}

        # 3 Requests OK
        for _ in range(3):
            r = client.get("/api/jobs/test", headers=headers)
            assert r.status_code == 200

        # 4. = 429
        r = client.get("/api/jobs/test", headers=headers)
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert r.headers["Retry-After"] == "3600"
        assert r.headers["X-RateLimit-Remaining"] == "0"

        body = r.json()
        assert "Rate limit" in body["detail"] or "rate" in body["detail"].lower()
        assert body["retry_after_seconds"] == 3600

    def test_anonymer_user_default_anonymous(self, test_app, monkeypatch):
        """Ohne X-Systelios-User-Header zaehlt der Counter unter 'anonymous'."""
        monkeypatch.setattr(
            "app.middleware.ratelimit.settings.RATE_LIMIT_REQUESTS_PER_HOUR", 2,
            raising=False,
        )
        client = TestClient(test_app)
        client.get("/api/jobs/test")
        client.get("/api/jobs/test")
        r = client.get("/api/jobs/test")
        assert r.status_code == 429
