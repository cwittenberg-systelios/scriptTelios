"""
v19.14b: HMAC-Auth mit differenzierter Fehlerursache.

Vor v19.14b lieferte verify_signature nur True/False - Uhr-Drift und echte
Signaturfehler waren fuer Nutzer wie Audit-Log ununterscheidbar (bei jedem
401 steht user="-" im Log, weil request.state.user_id nie gesetzt wird).
check_auth() liefert jetzt den Grund; get_current_user macht daraus eine
selbst behebbare Meldung.
"""
from __future__ import annotations

import time

import pytest
from starlette.datastructures import Headers

from app.core.auth import AuthError, check_auth, verify_signature, _compute_signature
from app.core.config import settings


def _sig(user: str, ts: str) -> str:
    return _compute_signature(user, ts)


def _now() -> str:
    return str(int(time.time()))


# ── check_auth ────────────────────────────────────────────────────────────────

class TestCheckAuth:

    def test_gueltige_frische_signatur(self):
        ts = _now()
        assert check_auth("alice", ts, _sig("alice", ts)) == (True, "ok", None)

    def test_fehlende_header(self):
        assert check_auth("", "", "") == (False, "missing", None)
        assert check_auth("alice", "", "x") == (False, "missing", None)
        assert check_auth("alice", _now(), "") == (False, "missing", None)

    def test_timestamp_kein_integer(self):
        ok, reason, skew = check_auth("alice", "vorgestern", "deadbeef")
        assert (ok, reason, skew) == (False, "bad_timestamp", None)

    def test_uhr_geht_nach_positives_skew(self):
        # Geraet meldet einen zu ALTEN Timestamp -> Uhr geht nach.
        old = str(int(time.time()) - settings.AUTH_TIMESTAMP_WINDOW_SEC - 600)
        ok, reason, skew = check_auth("alice", old, _sig("alice", old))
        assert (ok, reason) == (False, "clock_skew")
        assert skew > settings.AUTH_TIMESTAMP_WINDOW_SEC

    def test_uhr_geht_vor_negatives_skew(self):
        future = str(int(time.time()) + settings.AUTH_TIMESTAMP_WINDOW_SEC + 600)
        ok, reason, skew = check_auth("alice", future, _sig("alice", future))
        assert (ok, reason) == (False, "clock_skew")
        assert skew < -settings.AUTH_TIMESTAMP_WINDOW_SEC

    def test_skew_vor_signaturpruefung(self):
        # Auch mit voellig kaputter Signatur muss clock_skew gemeldet werden -
        # das ist die bewusste Reihenfolge (D4).
        old = str(int(time.time()) - settings.AUTH_TIMESTAMP_WINDOW_SEC - 60)
        ok, reason, _ = check_auth("alice", old, "00" * 32)
        assert (ok, reason) == (False, "clock_skew")

    def test_innerhalb_fenster_kein_skew(self):
        inside = str(int(time.time()) - settings.AUTH_TIMESTAMP_WINDOW_SEC + 30)
        ok, reason, _ = check_auth("alice", inside, _sig("alice", inside))
        assert (ok, reason) == (True, "ok")

    def test_falsche_signatur_bei_frischem_timestamp(self):
        ts = _now()
        ok, reason, skew = check_auth("alice", ts, "00" * 32)
        assert (ok, reason, skew) == (False, "bad_signature", None)

    def test_signatur_fuer_anderen_user_zaehlt_nicht(self):
        ts = _now()
        ok, reason, _ = check_auth("mallory", ts, _sig("alice", ts))
        assert (ok, reason) == (False, "bad_signature")


# ── verify_signature (Wrapper, Rueckwaertskompatibilitaet) ───────────────────

class TestVerifySignatureWrapper:

    def test_true_bei_gueltig(self):
        ts = _now()
        assert verify_signature("alice", ts, _sig("alice", ts)) is True

    @pytest.mark.parametrize("case", ["missing", "bad_timestamp", "skew", "bad_sig"])
    def test_false_bei_jedem_fehlerfall(self, case):
        ts = _now()
        args = {
            "missing":       ("", "", ""),
            "bad_timestamp": ("alice", "xx", "yy"),
            "skew":          ("alice",
                              str(int(time.time()) - settings.AUTH_TIMESTAMP_WINDOW_SEC - 60),
                              "00" * 32),
            "bad_sig":       ("alice", ts, "00" * 32),
        }[case]
        assert verify_signature(*args) is False


# ── get_current_user: Meldung + Header ───────────────────────────────────────

class _Req:
    """Minimal-Stub statt echtem Starlette-Request: get_current_user liest nur
    request.headers und setzt request.state.user_id."""

    def __init__(self, **headers):
        self.headers = Headers({k: v for k, v in headers.items() if v is not None})
        self.state = type("S", (), {})()


async def _call(**headers):
    from app.core.auth import get_current_user
    return await get_current_user(_Req(**headers))


class TestGetCurrentUser:

    @pytest.mark.asyncio
    async def test_gueltig_liefert_user(self):
        ts = _now()
        user = await _call(**{
            "X-Systelios-User": "alice",
            "X-Systelios-Timestamp": ts,
            "X-Systelios-Signature": _sig("alice", ts),
        })
        assert user == "alice"

    @pytest.mark.asyncio
    async def test_clock_skew_meldung_nennt_betrag_und_richtung(self):
        old = str(int(time.time()) - settings.AUTH_TIMESTAMP_WINDOW_SEC - 12 * 60)
        with pytest.raises(AuthError) as ei:
            await _call(**{
                "X-Systelios-User": "alice",
                "X-Systelios-Timestamp": old,
                "X-Systelios-Signature": _sig("alice", old),
            })
        exc = ei.value
        assert exc.status_code == 401
        assert "Minuten nach" in exc.detail
        assert "Uhr dieses Geräts" in exc.detail
        assert exc.headers["X-Systelios-Auth-Error"] == "clock_skew"
        assert int(exc.headers["X-Systelios-Clock-Skew"]) > 0

    @pytest.mark.asyncio
    async def test_uhr_geht_vor_richtung_vor(self):
        future = str(int(time.time()) + settings.AUTH_TIMESTAMP_WINDOW_SEC + 12 * 60)
        with pytest.raises(AuthError) as ei:
            await _call(**{
                "X-Systelios-User": "alice",
                "X-Systelios-Timestamp": future,
                "X-Systelios-Signature": _sig("alice", future),
            })
        assert "Minuten vor" in ei.value.detail
        assert int(ei.value.headers["X-Systelios-Clock-Skew"]) < 0

    @pytest.mark.asyncio
    async def test_bad_signature_bleibt_generisch(self):
        # Kein Informationsleck ueber die Signatur selbst - nur der Header
        # traegt den Grund (fuer Log/Devtools).
        ts = _now()
        with pytest.raises(AuthError) as ei:
            await _call(**{
                "X-Systelios-User": "alice",
                "X-Systelios-Timestamp": ts,
                "X-Systelios-Signature": "00" * 32,
            })
        assert ei.value.detail == "Ungültige oder fehlende Authentifizierung"
        assert ei.value.headers["X-Systelios-Auth-Error"] == "bad_signature"
        assert "X-Systelios-Clock-Skew" not in ei.value.headers

    @pytest.mark.asyncio
    async def test_fehlende_header_generisch(self):
        # AUTH_ENABLED=True (Default) + kein User -> 401, nicht dev-user.
        with pytest.raises(AuthError) as ei:
            await _call(**{
                "X-Systelios-User": "alice",
                "X-Systelios-Timestamp": "",
                "X-Systelios-Signature": "",
            })
        assert ei.value.headers["X-Systelios-Auth-Error"] == "missing"

    @pytest.mark.asyncio
    async def test_dev_modus_ohne_user(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_ENABLED", False)
        assert await _call() == "dev-user"
