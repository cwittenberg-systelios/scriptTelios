import asyncio
from pathlib import Path
from app.services import transcription


async def main():
    audio = sorted(Path("/workspace/eval_data").rglob("*.mp3"))
    print(f"{len(audio)} Audiodateien")
    for ap in audio:
        cache = ap.with_suffix(".transcript.txt")
        if cache.exists():
            print(f"skip (cached): {cache.name}")
            continue
        print(f"transkribiere: {ap} ...")
        tr = await transcription.transcribe_audio(ap)
        cache.write_text(tr["transcript"], encoding="utf-8")
        print(f"  -> {cache.name} ({len(tr['transcript'])} Zeichen)")


asyncio.run(main())
