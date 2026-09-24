#!/usr/bin/env python3
"""
eval_interview_chat.py - Dialog-Modus gegen synthetische Behandler-Skripte
laufen lassen (v19.31, S6). Braucht erreichbares Ollama (Pod).

    python3 backend/scripts/eval_interview_chat.py [--set kunst] [--model gemma4:31b]
                                                   [--only scham] [--json out.json]

Je Skript: der "Behandler" antwortet mit vorgefertigten Saetzen; auf
Nachfragen zu einem Thema antwortet er mit dem naechsten Satz des Themas,
danach mit "Mehr weiss ich dazu nicht." Kennzahlen:

  turns            Interviewer-Turns bis fertig (oder Abbruch)
  nachfragen       Rueckfragen je Thema (aus rueckfragen_je_thema)
  abgedeckt        Punkte der Checkliste am Ende
  erfunden         Woerter/Phrasen im Interviewer-Text, die im Skript als
                   "nicht gesagt" markiert sind (Halluzinations-Proxy)
  redundant        Turns, die apply_turn als redundant gemeldet hat
  fertig           ob das Gespraech regulaer beendet wurde
  regie            Verteilung der Regie-Typen (trigger/pflicht/budget/abschluss)
  floskeln         Interviewer-Turns, die mit einer bereits benutzten Floskel
                   beginnen ("Alles klar", "Verstanden", ...) (v19.31.2)
  latenz           Sekunden je Turn (Mittel / Max) (v19.31.2)
  ttft             Sekunden bis zum ersten Wort (Mittel / Max) (v19.33)
  reloads          Turns, in denen Ollama das Modell (neu) geladen hat
                   (load_s > 1 s) - mit LLM_FIXED_CTX=true ab Turn 2 ≈ 0 (v19.33)

Auf Trigger-Nachfragen (Suizidalitaet) antwortet der Behandler aus dem
Thema, in dem die Suizidalitaet zur Sprache kam - so wird die Kette bis
Glied 2 geprueft (v19.31.2).

Das Skript ist bewusst simpel: es misst Ueberfragen, Vergessen und
Einschmuggeln, nicht Gespraechsqualitaet - dafuer ist die Rueckmeldeplattform.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.interview_chat import (  # noqa: E402
    STATUS_ABGEDECKT, TURN_SCHEMA, ChatConfig, ChatState, Turn, apply_turn, plan_turn,
    verwendete_einstiege,
)
from app.core.interview_sets import get_set  # noqa: E402
from app.services.llm_chat import generate_chat_stream  # noqa: E402

# ── Synthetische Skripte ─────────────────────────────────────────────────────
# antworten: thema -> Liste von Saetzen (erste Antwort, dann Nachfrage-Antworten)
# nicht_gesagt: Begriffe, die im Interviewer-Text NICHT auftauchen duerfen

SKRIPTE = [
    {
        "name": "scham",
        "antworten": {
            "klient": ["Um Herrn Müller."],
            "anliegen": ["Umgang mit Scham nach dem Streit mit der Tochter am Wochenende."],
            "methode": ["Freies Malen mit Acryl, Thema was zwischen uns steht.", "Entstanden ist eine rote Fläche mit einem grauen Spalt."],
            "beobachtung": ["Anfangs zurückgezogen, hat schnell und fast wütend gemalt.", "Kaum Blickkontakt, erst beim Betrachten des Bildes."],
            "ergebnis": ["Er konnte den Spalt als das benennen, was er nicht sagt."],
            "zustand": ["Geht ruhiger, aber erschöpft."],
            "prozess": ["Er kommt langsam an das Thema ran."],
            "vereinbarung": ["Er schaut das Bild bis nächste Woche jeden Tag kurz an."],
            "selbstgefaehrdung": ["Keine Hinweise auf Selbstgefährdung."],
        },
        "nicht_gesagt": ["Mutter", "Medikament", "Angst", "Trauma"],
    },
    {
        "name": "suizid_kette",
        "antworten": {
            "klient": ["Es geht um Frau Kaiser."],
            "anliegen": ["Erschöpfung und das Gefühl, allen zur Last zu fallen."],
            "methode": ["Tonarbeit, eine Figur, die etwas trägt."],
            "beobachtung": ["Sehr still, hat lange an der Figur gedrückt. Zum Ende hin hat sie lebensmüde Gedanken geäußert.",
                            "Keine konkreten Pläne, sie ist absprachefähig, glaubhaft.",
                            "Wir haben eine Kooperationsbedingung vereinbart und den Nachtdienst informiert."],
            "ergebnis": ["Die Figur hat sie am Ende abgestellt, das war wichtig."],
            "zustand": ["Sie geht ernst, aber gefasst."],
            "prozess": ["Mehr weiß ich dazu nicht."],
            "vereinbarung": ["Morgen früh kurzes Gespräch, sonst nichts."],
            "selbstgefaehrdung": ["Siehe oben, lebensmüde Gedanken, distanziert, keine Pläne."],
        },
        "nicht_gesagt": ["Klinik wechseln", "Medikation", "Vater"],
    },
    {
        "name": "knapp",
        "antworten": {
            "klient": ["Frau B."],
            "anliegen": ["Grenzen setzen."],
            "methode": ["Collage."],
            "beobachtung": ["Konzentriert, guter Kontakt."],
            "ergebnis": ["Zufrieden mit der Collage."],
            "zustand": ["Geht stabil."],
            "prozess": ["Weiß ich nicht."],
            "vereinbarung": ["Nichts vereinbart."],
            "selbstgefaehrdung": ["Nein, keine Hinweise."],
        },
        "nicht_gesagt": ["Angst", "Familie", "Kind"],
    },
]

SKRIPTE.append({
    # v19.31.2: prueft Glied 2 der Suizidalitaets-Kette (fehlende/unsichere
    # Absprachefaehigkeit -> Frage nach der Vereinbarung).
    "name": "suizid_glied2",
    "antworten": {
        "klient": ["Um Herrn Yilmaz."],
        "anliegen": ["Wut auf sich selbst nach dem Rückfall."],
        "methode": ["Trommeln, freie Improvisation."],
        "beobachtung": ["Laut, abgehackt, dann plötzlich still. Er hat gesagt, er denke manchmal an Suizid.",
                        "Konkrete Pläne hat er verneint, aber ich bin unsicher, ob er absprachefähig ist.",
                        "Wir haben eine Kooperationsbedingung vereinbart, die Stationsärztin ist informiert und der Nachtdienst weiß Bescheid."],
        "ergebnis": ["Er hat den Rhythmus am Ende selbst verlangsamt."],
        "zustand": ["Angespannt, aber im Kontakt."],
        "prozess": ["Schwer einzuschätzen."],
        "vereinbarung": ["Morgen früh Gespräch mit der Ärztin."],
        "selbstgefaehrdung": ["Siehe oben, Suizidgedanken, Kooperationsbedingung vereinbart."],
    },
    "nicht_gesagt": ["Alkohol", "Familie", "Medikation"],
})

FALLBACK = "Mehr weiß ich dazu nicht."


def _thema_von(text: str, plan_thema: str, offen: list[str]) -> str:
    return plan_thema or (offen[0] if offen else "")


async def run_skript(sk: dict, set_key: str, model: str, cfg: ChatConfig) -> dict:
    s = get_set(set_key)
    state = ChatState(set_key=set_key, historie=[], checkliste={})
    verbrauch: dict[str, int] = {}
    regie: dict[str, int] = {}
    redundant = 0
    turns = 0
    floskeln = 0
    latenz: list[float] = []
    ttft: list[float] = []
    reloads = 0
    letztes_thema = ""
    while not state.fertig and turns < cfg.max_turns:
        plan = plan_turn(state, cfg)
        if plan.regie_typ:
            regie[plan.regie_typ] = regie.get(plan.regie_typ, 0) + 1
        result = None
        async for kind, payload in generate_chat_stream(plan.system_prompt, plan.messages, model=model,
                                                        response_format=TURN_SCHEMA, temperature=0.3):
            if kind == "error":
                return {"name": sk["name"], "error": str(payload), "turns": turns}
            if kind == "done":
                result = payload
        data = result.get("structured_data") if result else None
        sage = (result.get("sage") or "").strip() if result else ""
        if result and result.get("duration_s") is not None:
            latenz.append(float(result["duration_s"]))
        perf = (result or {}).get("perf") or {}
        if perf.get("ttft_s") is not None:
            ttft.append(float(perf["ttft_s"]))
        if (perf.get("load_s") or 0) > 1.0:
            reloads += 1
        vorher = verwendete_einstiege(state.historie)
        meta = apply_turn(state, data, sage, plan, cfg)
        if any(sage.lower().startswith(e) for e in vorher):
            floskeln += 1
        turns += 1
        if meta["redundant"]:
            redundant += 1
        if meta["fertig"]:
            break
        # Behandler antwortet aus dem Skript
        offen = [f.key for f in s.fragen if state.checkliste.get(f.key) != STATUS_ABGEDECKT]
        if meta.get("regie_typ") == "trigger" and letztes_thema:
            thema = letztes_thema          # Trigger-Kette: aus dem Ursprungsthema antworten
        else:
            thema = _thema_von(sage, meta["thema"], offen)
        letztes_thema = thema
        saetze = sk["antworten"].get(thema, [])
        i = verbrauch.get(thema, 0)
        antwort = saetze[i] if i < len(saetze) else FALLBACK
        verbrauch[thema] = i + 1
        state.historie.append(Turn("behandler", antwort))
    interviewer_text = " ".join(t.text for t in state.historie if t.rolle == "system").lower()
    erfunden = [w for w in sk["nicht_gesagt"] if w.lower() in interviewer_text]
    return {
        "name": sk["name"], "turns": turns, "fertig": state.fertig,
        "abgedeckt": sum(1 for f in s.fragen if state.checkliste.get(f.key) == STATUS_ABGEDECKT),
        "punkte": len(s.fragen), "nachfragen": dict(state.rueckfragen_je_thema),
        "redundant": redundant, "erfunden": erfunden, "regie": regie, "floskeln": floskeln,
        "latenz_mittel": round(sum(latenz) / len(latenz), 1) if latenz else None,
        "latenz_max": round(max(latenz), 1) if latenz else None,
        "ttft_mittel": round(sum(ttft) / len(ttft), 1) if ttft else None,
        "ttft_max": round(max(ttft), 1) if ttft else None,
        "reloads": reloads,
        "gespraech": [{"rolle": t.rolle, "text": t.text} for t in state.historie],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="kunst")
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    from app.services.llm import ensure_generation_model
    model = await ensure_generation_model(a.model, "dokumentation")
    cfg = ChatConfig()
    out = []
    for sk in SKRIPTE:
        if a.only and sk["name"] != a.only:
            continue
        r = await run_skript(sk, a.set, model, cfg)
        out.append(r)
        if "error" in r:
            print(f"{r['name']:14s} FEHLER nach {r['turns']} Turns: {r['error']}")
            continue
        print(f"{r['name']:14s} turns={r['turns']:2d} fertig={str(r['fertig']):5s} "
              f"abgedeckt={r['abgedeckt']}/{r['punkte']} redundant={r['redundant']} "
              f"erfunden={r['erfunden'] or '-'} floskeln={r['floskeln']} "
              f"latenz={r['latenz_mittel']}s/{r['latenz_max']}s ttft={r['ttft_mittel']}s/{r['ttft_max']}s "
              f"reloads={r['reloads']} nachfragen={r['nachfragen']} regie={r['regie']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:  # noqa: ASYNC230 - einmalig am Ende
            fh.write(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
