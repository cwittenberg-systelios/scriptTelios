# ─────────────────────────────────────────────────────────────────────────────
# v19.12: Kandidaten-Konsens-Extraktion von Patientenname + Geschlecht
#
# Testet:
#   - _segmentiere_namen(): Partikel (von/van/v./v.d.), Titel, Mehrfach-Vornamen
#   - Initialen-Hauskonvention ("von Musterberg" -> "v.M.", belegt in der
#     Folgeverlaengerungs-Vorlage der Klinik)
#   - Konsens/Dissens-Logik von extract_patient_name()
#   - Briefkopf-Fixtures der drei echten (anonymisierten) Klinik-Vorlagen
#   - extract_verlaufskopf_name(): "Nachname, Vorname (Aufnahmenr)"
# ─────────────────────────────────────────────────────────────────────────────
from pathlib import Path

import pytest

from app.services.extraction import (
    _segmentiere_namen,
    extract_patient_name,
    extract_verlaufskopf_name,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "briefkoepfe"


# ── Segmentierung: Partikel, Titel, Initialen-Konvention ─────────────────────


class TestSegmentiereNamen:
    @pytest.mark.parametrize(
        "tokens, nachname, stamm, initial",
        [
            (["Maria", "Schmidt"], "Schmidt", "Schmidt", "S."),
            (["Maria", "von", "Schmidt"], "von Schmidt", "Schmidt", "v.S."),
            (["Maria", "von", "der", "Heyden"], "von der Heyden", "Heyden", "v.d.H."),
            (["Klaus", "v.", "Schmidt"], "v. Schmidt", "Schmidt", "v.S."),
            (["Maria", "v.", "d.", "Heyden"], "v. d. Heyden", "Heyden", "v.d.H."),
            (["Peter", "van", "den", "Berg"], "van den Berg", "Berg", "v.d.B."),
            (["Christina", "von", "Musterberg"], "von Musterberg", "Musterberg", "v.M."),
            (["Maria", "Schmidt-Meier"], "Schmidt-Meier", "Schmidt-Meier", "S."),
        ],
    )
    def test_partikel_und_initiale(self, tokens, nachname, stamm, initial):
        r = _segmentiere_namen(list(tokens))
        assert r is not None
        assert r["nachname"] == nachname
        assert r["nachname_stamm"] == stamm
        assert r["initial"] == initial

    def test_mehrere_vornamen_letztes_token_ist_nachname(self):
        # v19.12-Vorbefund: die Alt-Implementierung machte aus
        # "Maria Anna Schmidt" den Nachnamen "Anna Schmidt" (Initiale "A.").
        r = _segmentiere_namen(["Maria", "Anna", "Schmidt"])
        assert r["vorname"] == "Maria Anna"
        assert r["nachname"] == "Schmidt"
        assert r["initial"] == "S."

    def test_abgekuerzter_vorname_ist_kein_partikel(self):
        # "D." grossgeschrieben = abgekuerzter Vorname, kein Partikel.
        r = _segmentiere_namen(["Maria", "D.", "Schmidt"])
        assert r["nachname"] == "Schmidt"
        assert r["initial"] == "S."

    def test_titel_wird_erkannt_und_abgeschnitten(self):
        r = _segmentiere_namen(["Dr.", "med.", "Ulrich", "Musterberg"])
        assert r["titel"] is True
        assert r["nachname"] == "Musterberg"

    def test_partikel_ohne_stamm_ist_kein_name(self):
        assert _segmentiere_namen(["Maria", "von"]) is None


# ── Konsens/Dissens ──────────────────────────────────────────────────────────


BRIEFKOPF = (
    "Wald-Michelbach, 29. Januar 2026\n"
    "AKUTAUFNAHME\n"
    "Antrag auf Kostenübernahme\n"
    "{anrede_block}\n"
    "{name_block}\n"
    "Musterweg 89\n"
    "12346 Musterstadt\n"
    "geb. 08.09.1982\n"
    "Sehr geehrte Damen und Herren,\n"
    "wir berichten über {berichten}, die sich seit dem 29.01.2026 "
    "in unserer stationären Krankenhausbehandlung befindet.\n"
)


class TestKonsens:
    def test_zwei_anker_einig_liefert_konsens(self):
        text = BRIEFKOPF.format(
            anrede_block="Frau", name_block="Sabine Schuster",
            berichten="Frau Sabine Schuster",
        )
        r = extract_patient_name(text)
        assert r is not None
        assert (r["anrede"], r["nachname_stamm"], r["initial"]) == ("Frau", "Schuster", "S.")
        assert r["quelle"] == "konsens"
        assert set(r["anker"]) == {"a1_berichten_ueber", "a2_adressblock"}

    def test_partikelname_ueber_beide_anker(self):
        text = BRIEFKOPF.format(
            anrede_block="Frau", name_block="Christina von Musterberg",
            berichten="Frau Christina von Musterberg",
        )
        r = extract_patient_name(text)
        assert r is not None
        assert r["nachname"] == "von Musterberg"
        assert r["initial"] == "v.M."
        assert r["quelle"] == "konsens"

    def test_dissens_geschlecht_liefert_none(self):
        # Adressblock weiblich, berichten-Satz maennlich mit anderem Namen:
        # frueher gewann still der erste Treffer - jetzt keine Erkennung.
        text = BRIEFKOPF.format(
            anrede_block="Frau", name_block="Sabine Schuster",
            berichten="Herrn Peter Müller",
        )
        assert extract_patient_name(text) is None

    def test_sachbearbeiterin_im_kopf_kein_verwechsler(self):
        # Der im v19.12-Audit reproduzierte stille Personenverwechsler:
        # Kassen-Kontaktblock ("Frau Sabine Meier") vor der Grussformel,
        # Patient nur im berichten-Satz ("Herrn Klaus v. Schmidt").
        # Dissens der Anker -> None statt falscher "Frau Meier".
        text = (
            "Krankenkasse Musterland\n"
            "Frau\n"
            "Sabine Meier\n"
            "Postfach 12 34\n"
            "68159 Mannheim\n"
            "Antrag auf Verlängerung\n"
            "Sehr geehrte Damen und Herren,\n"
            "wir berichten über Herrn Klaus v. Schmidt aus der Behandlung.\n"
        )
        assert extract_patient_name(text) is None

    def test_einzelkandidat_a1_wird_akzeptiert(self):
        text = "wir berichten über Frau Sabine Schuster, die sich seit..."
        r = extract_patient_name(text)
        assert r is not None
        assert r["quelle"] == "a1_einzeln"

    def test_einzelkandidat_a2_wird_abgelehnt(self):
        # F3a: Adressblock allein reicht nicht (traf frueher auch die
        # Sachbearbeiterin) - ohne bestaetigenden A1-Anker keine Erkennung.
        text = (
            "AKUTAUFNAHME\n"
            "Frau\n"
            "Sabine Schuster\n"
            "Musterweg 89\n"
            "12346 Musterstadt\n"
        )
        assert extract_patient_name(text) is None

    def test_behandler_mit_titel_ist_kein_patient(self):
        # Titel-Kandidaten werden nicht als Patient gewertet, selbst wenn
        # sie das Blockmuster erfuellen wuerden.
        text = (
            "wir berichten über Frau Sabine Schuster, die sich seit...\n"
            "Dr. med. Ulrich Musterberg\n"
        )
        r = extract_patient_name(text)
        assert r is not None
        assert r["nachname_stamm"] == "Schuster"

    def test_name_nach_grussformel_wird_ignoriert(self):
        # Zonen-Schnitt: Blockmuster greifen nur VOR der Grussformel -
        # Signaturbloecke am Dokumentende fallen weg, auch wenn der
        # Behandler denselben Nachnamen traegt wie der Patient (belegt in
        # der Folgeverlaengerungs-Vorlage: Dr. med. Ulrich Musterberg vs.
        # Patientin Christina von Musterberg).
        text = (
            "wir berichten über Frau Christina von Musterberg, die...\n"
            + "x\n" * 5
            + "Herr\nUlrich Musterberg\ngeb. 01.01.1960\n"
        )
        r = extract_patient_name(text)
        assert r is not None
        assert r["anrede"] == "Frau"
        assert r["quelle"] == "a1_einzeln"


# ── Fixtures der echten Klinik-Vorlagen ──────────────────────────────────────


class TestEchteVorlagen:
    @pytest.mark.parametrize(
        "fixture, anrede, stamm, initial",
        [
            ("akutantrag_kopf.txt", "Frau", "Schuster", "S."),
            ("entlassbericht_kopf.txt", "Frau", "Musterfrau", "M."),
            ("folgeverlaengerung_kopf.txt", "Frau", "Musterberg", "v.M."),
        ],
    )
    def test_briefkopf_fixture(self, fixture, anrede, stamm, initial):
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        r = extract_patient_name(text)
        assert r is not None, f"{fixture}: keine Erkennung"
        assert r["anrede"] == anrede
        assert r["nachname_stamm"] == stamm
        assert r["initial"] == initial
        assert r["quelle"] == "konsens", (
            f"{fixture}: erwartet Konsens aus beiden Ankern, kam {r['anker']}"
        )


# ── Verlaufsdokumentations-Kopf ──────────────────────────────────────────────


class TestVerlaufskopf:
    def test_standard_format(self):
        r = extract_verlaufskopf_name("Musterfrau, Marianne (00025272)  9/52\n...")
        assert r is not None
        assert r["nachname"] == "Musterfrau"
        assert r["vorname"] == "Marianne"
        assert r["aufnahmenummer"] == "00025272"
        assert r["anrede"] == ""  # kein Geschlecht im Verlaufskopf!

    def test_partikelname_im_verlaufskopf(self):
        r = extract_verlaufskopf_name("von Musterberg, Christina (00025388)\n...")
        assert r is not None
        assert r["nachname_stamm"] == "Musterberg"
        assert r["initial"] == "v.M."

    def test_ohne_aufnahmenummer_kein_treffer(self):
        # Ohne die Nummern-Klammer ist "Wort, Wort" zu unspezifisch
        # (OCR-Text ist unzuverlaessig) - kein Treffer.
        assert extract_verlaufskopf_name("Musterfrau, Marianne\n...") is None

    def test_liefert_nie_geschlecht(self):
        # Der Verlaufskopf enthaelt keine Anrede - er darf downstream nie
        # als Geschlechtsquelle wirken, nur als Namens-Kreuzcheck.
        r = extract_verlaufskopf_name("Schuster, Sabine (00025199)")
        assert r["anrede"] == ""
