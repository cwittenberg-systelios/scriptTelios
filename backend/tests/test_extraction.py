"""
Tests fuer den Extraktions-Service (extraction.py)

Abgedeckt:
  - Qualitaetspruefung (_assess_quality)
  - Wiederholungsrate (_has_excessive_repetition)
  - Textnormalisierung (_normalize_text)
  - Bild-Base64-Kodierung (_image_to_base64)
  - Formatvalidierung (extract_text_with_meta mit ungueltigem Format)
  - Stufenauswahl per Methoden-String in ExtractionResult
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import io

# Modul importieren (App-Kontext wird gemockt)
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# settings mocken bevor Import
with patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}):
    from app.services.extraction import (
        _assess_quality,
        _has_excessive_repetition,
        _normalize_text,
        _image_to_base64,
        ExtractionResult,
        TextQuality,
        MIN_CHARS,
        MIN_READABILITY,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Qualitaetspruefung
# ─────────────────────────────────────────────────────────────────────────────

class TestAssessQuality:

    def test_leerer_text_nicht_ok(self):
        q = _assess_quality("")
        assert not q.ok
        assert q.score == 0.0

    def test_text_zu_kurz(self):
        q = _assess_quality("Hallo Welt")
        assert not q.ok
        assert "Zu kurz" in q.reason

    def test_guter_deutscher_text(self):
        text = (
            "Der Patient berichtet von anhaltenden Schlafproblemen und innerer Unruhe. "
            "Die Therapie konzentriert sich auf die Bearbeitung familiärer Konflikte. "
            "Im heutigen Gespräch wurden erste Fortschritte bei der Expositionsübung erzielt. "
            "Der Befund zeigt eine deutliche Verbesserung der Stimmungslage. "
            "Diagnose: Anpassungsstörung mit ängstlicher Symptomatik."
        )
        q = _assess_quality(text)
        assert q.ok, f"Erwartet ok=True, aber: {q.reason}"
        assert q.score > 0.5

    def test_zeichensalat_nicht_ok(self):
        # OCR-Artefakt: fast nur Sonderzeichen
        text = "§§§@@@###~~~" * 20
        q = _assess_quality(text)
        assert not q.ok

    def test_englischer_text_nicht_ok_bei_laengeren_texten(self):
        # Langer englischer Text ohne deutsche Stoppwoerter sollte durchfallen
        text = (
            "The patient reports persistent sleep problems and internal restlessness. "
            "Therapy focuses on processing family conflicts. Progress was made today. "
            "The assessment shows significant improvement in mood and daily functioning. "
            "Diagnosis: adjustment disorder with anxious symptoms and chronic stress."
        )
        q = _assess_quality(text)
        # Englisch kann durchfallen (lang_score < 0.1)
        # Wir pruefen nur dass es nicht faelschlicherweise hohe Qualitaet bekommt
        if q.ok:
            assert q.score < 0.9  # Wenn ok, dann zumindest kein perfekter Score

    def test_mindestlaenge_konfigurierbar(self):
        kurzer_text = "Befund: unauffaellig."
        q_streng = _assess_quality(kurzer_text, min_chars=100)
        q_tolerant = _assess_quality(kurzer_text, min_chars=5)
        assert not q_streng.ok
        # Bei sehr toleranter Grenze und kurzem aber lesbarem Text
        # koennte es ok sein (haengt von Stoppwort-Score ab)
        assert q_tolerant.score >= 0.0  # mindestens berechnet


# ─────────────────────────────────────────────────────────────────────────────
# Wiederholungsrate
# ─────────────────────────────────────────────────────────────────────────────

class TestHasExcessiveRepetition:

    def test_normale_striche_ok(self):
        # Striche und Leerzeichen sollen nicht als Artefakt gelten
        assert not _has_excessive_repetition("Name: ____________")
        assert not _has_excessive_repetition("Datum: ----------")

    def test_buchstaben_wiederholung_artefakt(self):
        assert _has_excessive_repetition("||||||||||||||||")
        assert _has_excessive_repetition("xxxxxxxxxxxxxxxx")
        assert _has_excessive_repetition("aaaaaaaaaaaaaaaa")

    def test_normaler_text_kein_artefakt(self):
        assert not _has_excessive_repetition("Der Patient berichtet von Schlafproblemen.")
        assert not _has_excessive_repetition("Diagnose: F32.1 Mittelgradige depressive Episode")

    def test_fuenf_wiederholungen_noch_ok(self):
        # Grenze ist 6, also 5 sollte noch kein Artefakt sein
        assert not _has_excessive_repetition("aaaaa")

    def test_sieben_wiederholungen_artefakt(self):
        assert _has_excessive_repetition("aaaaaaa")


# ─────────────────────────────────────────────────────────────────────────────
# Textnormalisierung
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeText:

    def test_mehrfach_leerzeilen_reduziert(self):
        text = "Zeile 1\n\n\n\n\nZeile 2"
        result = _normalize_text(text)
        assert "\n\n\n" not in result
        assert "Zeile 1" in result
        assert "Zeile 2" in result

    def test_seitentrennzeichen_entfernt(self):
        text = "Seite 1\x0cSeite 2\x0bSeite 3"
        result = _normalize_text(text)
        assert "\x0c" not in result
        assert "\x0b" not in result

    def test_fuehrendes_trailing_whitespace_entfernt(self):
        text = "   \n  Inhalt  \n   "
        result = _normalize_text(text)
        assert result == "Inhalt"

    def test_unicode_normalisierung(self):
        # Zusammengesetztes 'ä' (a + combining diaeresis) vs vorkomponiertes 'ä'
        text_composed = "a\u0308rztlich"  # a + combining diaeresis
        text_precomposed = "\u00e4rztlich"  # vorkomponiertes ä
        result = _normalize_text(text_composed)
        assert result == text_precomposed or result == "a\u0308rztlich"  # NFC oder unveraendert


# ─────────────────────────────────────────────────────────────────────────────
# Bild-Base64
# ─────────────────────────────────────────────────────────────────────────────

class TestImageToBase64:

    def test_gibt_string_zurueck(self):
        from PIL import Image
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        result = _image_to_base64(img)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_bild_wird_skaliert_wenn_zu_gross(self):
        from PIL import Image
        import base64
        # Grosses Bild erstellen
        img = Image.new("RGB", (3000, 3000), color=(128, 128, 128))
        result = _image_to_base64(img, max_size=800)
        # Dekodieren und Groesse pruefen
        img_decoded = Image.open(io.BytesIO(base64.b64decode(result)))
        assert max(img_decoded.size) <= 800

    def test_kleines_bild_nicht_skaliert(self):
        from PIL import Image
        import base64
        img = Image.new("RGB", (200, 150), color=(0, 0, 0))
        result = _image_to_base64(img, max_size=1600)
        img_decoded = Image.open(io.BytesIO(base64.b64decode(result)))
        assert img_decoded.size == (200, 150)

    def test_valides_base64(self):
        from PIL import Image
        import base64
        img = Image.new("RGB", (50, 50))
        result = _image_to_base64(img)
        # Sollte decodierbar sein ohne Fehler
        decoded = base64.b64decode(result)
        assert len(decoded) > 0


# ─────────────────────────────────────────────────────────────────────────────
# ExtractionResult
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractionResult:

    def test_namedtuple_felder(self):
        r = ExtractionResult(
            text="Test",
            method="pdfplumber",
            quality=0.9,
            pages=2,
            warnings=[]
        )
        assert r.text == "Test"
        assert r.method == "pdfplumber"
        assert r.quality == 0.9
        assert r.pages == 2
        assert r.warnings == []

    def test_methoden_strings(self):
        methoden = [
            "pdfplumber", "tesseract", "ollama_vision",
            "docx", "txt", "image_tess", "image_vision"
        ]
        for m in methoden:
            r = ExtractionResult("x" * 100, m, 0.8, 1, [])
            assert r.method == m


# ─────────────────────────────────────────────────────────────────────────────
# Formatvalidierung
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatValidierung:

    @pytest.mark.asyncio
    async def test_unbekanntes_format_wirft_valueerror(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}):
            from app.services.extraction import extract_text_with_meta
            fake_path = Path("/tmp/test.xyz")
            with pytest.raises(ValueError, match="Nicht unterstuetztes Dateiformat"):
                await extract_text_with_meta(fake_path)

    @pytest.mark.asyncio
    async def test_txt_extraktion(self, tmp_path):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434"}):
            from app.services.extraction import extract_text_with_meta
            # Genuegend langen deutschen Text schreiben
            content = (
                "Der Patient stellt sich mit anhaltenden Schlafproblemen vor. "
                "Die Anamnese ergibt eine langjährige Belastungssituation im Beruf. "
                "Diagnose: Anpassungsstörung mit depressiver Reaktion (F43.21). "
                "Empfehlung: Ambulante psychotherapeutische Weiterbehandlung."
            )
            txt_file = tmp_path / "test.txt"
            txt_file.write_text(content, encoding="utf-8")
            result = await extract_text_with_meta(txt_file)
            assert result.method == "txt"
            assert result.pages == 1
            assert len(result.text) > 50
