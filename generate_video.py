import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts
import requests

PEXELS_SEARCH = "https://api.pexels.com/v1/videos/search"
OUT = Path("output")
OUT.mkdir(exist_ok=True)

SCRIPT = os.environ.get("SCRIPT", "").strip()
HOOK = os.environ.get("HOOK", "").strip()
OUTRO = os.environ.get("OUTRO", "OROLOGI SPIEGATI SEMPLICE ⌚").strip()
VOICE = os.environ.get("VOICE", "it-IT-GiuseppeMultilingualNeural").strip()
RATE = os.environ.get("RATE", "+6%").strip()
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
VISUAL_QUERIES = os.environ.get("VISUAL_QUERIES", "").strip()
MODE = os.environ.get("MODE", "generate_video").strip()
REQUEST_ID = os.environ.get("REQUEST_ID", "manual").strip()

SAFE_REQUEST_ID = "".join(ch for ch in REQUEST_ID if ch.isalnum())[:40] or "manual"
GIUSEPPE = "it-IT-GiuseppeMultilingualNeural"


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-5000:] or f"Comando fallito: {' '.join(cmd)}")
    return proc.stdout


def duration(path: Path) -> float:
    out = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ])
    return max(0.1, float(out.strip()))


async def _synth_once(text: str, out_mp3: Path, voice: str):
    comm = edge_tts.Communicate(text, voice, rate=RATE, boundary="WordBoundary")
    boundaries = []

    with out_mp3.open("wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                })

    return boundaries


def synthesize(text: str, out_mp3: Path, voice: str | None = None):
    requested = voice or VOICE
    selected_voice = requested if requested == GIUSEPPE else GIUSEPPE

    if requested != selected_voice:
        print(f"Voce {requested} non abilitata: uso {GIUSEPPE}.")

    last_error = None

    for attempt in range(1, 4):
        try:
            if out_mp3.exists():
                out_mp3.unlink()

            boundaries = asyncio.run(
                asyncio.wait_for(
                    _synth_once(text, out_mp3, selected_voice),
                    timeout=60,
                )
            )

            if not out_mp3.exists() or out_mp3.stat().st_size < 1000:
                raise RuntimeError("TTS ha restituito un file vuoto.")

            return boundaries

        except Exception as exc:
            last_error = exc
            print(f"TTS tentativo {attempt}/3 fallito: {exc}")

    raise RuntimeError(f"TTS non riuscito dopo 3 tentativi: {last_error}")


def stable_number(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def auto_queries(script: str):
    s = script.lower()
    found = []

    def add(query):
        if query not in found:
            found.append(query)

    # Scene molto specifiche prima dei visual generici.
    if any(k in s for k in ["comodino", "notte", "dormire", "lasciato", "fermo", "si ferma"]):
        add("watch on bedside table")

    if any(k in s for k in ["rotore", "oscillante"]):
        add("automatic watch rotor movement macro")

    if any(k in s for k in ["corona", "carica", "ricarica", "ricaricare"]):
        add("winding automatic watch crown close up")

    if any(k in s for k in ["polso", "indossarlo", "indossare"]):
        add("automatic watch on wrist close up")

    if any(k in s for k in ["automatico", "automatic"]):
        add("automatic watch movement macro")

    if any(k in s for k in ["quarzo", "batteria", "cristallo"]):
        add("quartz watch close up")

    if any(k in s for k in ["ingranaggi", "ruote", "meccanica", "molla", "movimento"]):
        add("mechanical watch gears macro")

    if any(k in s for k in ["quadrante", "lancetta", "secondi", "precisione", "preciso"]):
        add("watch dial second hand macro")

    if any(k in s for k in ["lusso", "migliaia", "costoso", "rolex", "omega", "patek", "marchio"]):
        add("luxury watch wrist macro")

    if any(k in s for k in ["orologiaio", "lavorazione", "assemblaggio"]):
        add("watchmaker working on watch movement")

    # Riempimento solo se servono altre clip.
    for q in [
        "mechanical watch macro cinematic",
        "watch movement close up",
        "watch wrist close up",
        "watchmaker macro",
        "watch dial macro",
    ]:
        if len(found) >= 5:
            break
        add(q)

    return found[:5]


def search_videos(query: str):
    seed = stable_number(f"{SAFE_REQUEST_ID}:{query}")
    preferred_page = 1 + (seed % 3)

    pages = []
    for page in [preferred_page, 1, 2, 3]:
        if page not in pages:
            pages.append(page)

    for page in pages:
        base = {
            "query": query,
            "size": "medium",
            "per_page": 30,
            "page": page,
            "locale": "en-US",
        }

        for params in ({**base, "orientation": "portrait"}, base):
            r = requests.get(
                PEXELS_SEARCH,
                headers={"Authorization": PEXELS_API_KEY},
                params=params,
                timeout=30,
            )

            if not r.ok:
                raise RuntimeError(f"Pexels {r.status_code}: {r.text[:300]}")

            videos = r.json().get("videos", [])
            if videos:
                return videos

    return []


def choose_mp4(video):
    candidates = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link")
    ]

    if not candidates:
        return None

    def score(f):
        w = f.get("width") or 1
        h = f.get("height") or 1
        portrait = 1 if h >= w else 0
        ratio_penalty = abs((w / h) - (9 / 16))
        pixels = min(w * h, 1080 * 1920) / (1080 * 1920)
        return portrait * 20 + pixels * 2 - ratio_penalty

    return max(candidates, key=score)


def download(url: str, dest: Path):
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)


def normalize_clip(src: Path, dst: Path, seconds: float):
    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{seconds:.3f}",
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=30,eq=contrast=1.04:saturation=0.95",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", str(dst),
    ])


def ass_time(sec: float) -> str:
    sec = max(0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", "(").replace("}", ")").replace("\n", r"\N")


def caption_groups(boundaries, n=4):
    out = []
    group = []

    for item in boundaries:
        group.append(item)
        if len(group) == n:
            out.append((group[0]["start"], group[-1]["end"], " ".join(x["text"] for x in group)))
            group = []

    if group:
        out.append((group[0]["start"], group[-1]["end"], " ".join(x["text"] for x in group)))

    return out


def write_ass(path: Path, boundaries, total: float):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,DejaVu Sans,64,&H00FFFFFF,&H000000FF,&H00101010,&H66000000,-1,0,0,0,100,100,0,0,1,5,0,2,70,70,220,1
Style: Hook,DejaVu Sans,82,&H00FFFFFF,&H000000FF,&H00101010,&H44000000,-1,0,0,0,100,100,0,0,1,6,0,8,70,70,130,1
Style: Outro,DejaVu Sans,66,&H00FFFFFF,&H000000FF,&H00101010,&H44000000,-1,0,0,0,100,100,0,0,1,6,0,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]

    for start, end, text in caption_groups(boundaries, 4):
        end = max(end, start + 0.45)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Captions,,0,0,0,,{esc(text)}\n")

    if HOOK:
        lines.append(
            f"Dialogue: 1,0:00:00.00,{ass_time(min(2.8, total))},Hook,,0,0,0,,{esc(HOOK.upper())}\n"
        )

    if OUTRO and total > 2.3:
        lines.append(
            f"Dialogue: 1,{ass_time(total - 2.3)},{ass_time(total)},Outro,,0,0,0,,{esc(OUTRO)}\n"
        )

    path.write_text("".join(lines), encoding="utf-8")


def write_meta(kind: str, status: str, extra: dict | None = None):
    data = {
        "request_id": SAFE_REQUEST_ID,
        "kind": kind,
        "status": status,
    }

    if extra:
        data.update(extra)

    (OUT / "result.json").write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8"
    )


def main():
    if MODE == "deploy_only":
        write_meta("deploy_only", "ok")
        return

    if not SCRIPT:
        raise SystemExit("SCRIPT mancante.")

    if MODE == "test_voice":
        sample = SCRIPT[:320]
        filename = f"voice_test_{SAFE_REQUEST_ID}.mp3"
        dest = OUT / filename

        print(f"Creo anteprima voce {GIUSEPPE}...")
        synthesize(sample, dest, voice=GIUSEPPE)
        write_meta("test_voice", "ok", {"file": filename})
        print(f"Creato {dest}")
        return

    if not PEXELS_API_KEY:
        raise SystemExit("PEXELS_API_KEY mancante.")

    work = Path(tempfile.mkdtemp(prefix="watchvideo_"))

    try:
        print("1/5 Creo la voce...")
        voice_mp3 = work / "voice.mp3"
        boundaries = synthesize(SCRIPT, voice_mp3, voice=GIUSEPPE)
        total = duration(voice_mp3)

        queries = (
            [q.strip() for q in VISUAL_QUERIES.split(",") if q.strip()]
            if VISUAL_QUERIES else auto_queries(SCRIPT)
        )[:5]

        (OUT / "queries.txt").write_text("\n".join(queries), encoding="utf-8")

        print("Query visual:")
        for q in queries:
            print(f"- {q}")

        print("2/5 Cerco le clip su Pexels...")
        raw_clips = []
        credits = []
        used_ids = set()

        fallback = [
            "watch close up cinematic",
            "watch movement macro",
            "watch wrist close up",
            "watchmaker close up",
        ]

        for q in queries + fallback:
            if len(raw_clips) >= 5:
                break

            videos = search_videos(q)
            viable = [
                v for v in videos
                if v.get("id") not in used_ids and choose_mp4(v)
            ]

            if not viable:
                continue

            pick = stable_number(
                f"{SAFE_REQUEST_ID}:{q}:{len(raw_clips)}"
            ) % len(viable)

            chosen = viable[pick]
            media = choose_mp4(chosen)
            dest = work / f"raw_{len(raw_clips)}.mp4"

            download(media["link"], dest)
            raw_clips.append(dest)
            used_ids.add(chosen.get("id"))

            creator = (chosen.get("user") or {}).get("name", "Pexels creator")
            chosen_url = chosen.get("url", "https://www.pexels.com")
            credits.append(f"{q} | id={chosen.get('id')} | {creator} | {chosen_url}")

            print(f"Clip scelta: query='{q}' id={chosen.get('id')}")

        if len(raw_clips) < 3:
            raise RuntimeError("Pexels ha restituito meno di 3 clip utilizzabili.")

        print("3/5 Adatto le clip al formato TikTok...")
        each = total / len(raw_clips)
        norm = []

        for i, src in enumerate(raw_clips):
            sec = each if i < len(raw_clips) - 1 else max(
                0.5,
                total - each * (len(raw_clips) - 1)
            )
            dst = work / f"norm_{i}.mp4"
            normalize_clip(src, dst, sec)
            norm.append(dst)

        concat_file = work / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in norm),
            encoding="utf-8"
        )

        visuals = work / "visuals.mp4"
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c", "copy", str(visuals)
        ])

        print("4/5 Creo sottotitoli e hook...")
        ass = work / "captions.ass"
        write_ass(ass, boundaries, total)

        print("5/5 Render finale...")
        video_filename = f"orologi_video_{SAFE_REQUEST_ID}.mp4"
        final = OUT / video_filename

        run([
            "ffmpeg", "-y", "-i", str(visuals), "-i", str(voice_mp3),
            "-vf", f"ass={ass.as_posix()}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", str(final),
        ])

        caption = (
            f"{HOOK or 'Orologi spiegati semplice.'} ⌚\n\n"
            "Segui la pagina per capire il mondo degli orologi senza tecnicismi inutili.\n\n"
            "#orologi #orologeria #watches #automaticwatch #luxurywatches"
        )

        (OUT / "caption.txt").write_text(caption, encoding="utf-8")
        (OUT / "credits.txt").write_text(
            "Visual forniti tramite Pexels.\n\n" + "\n".join(credits),
            encoding="utf-8"
        )

        write_meta(
            "generate_video",
            "ok",
            {
                "file": video_filename,
                "duration": round(total, 2),
                "queries": queries,
                "video_ids": list(used_ids),
            }
        )

        print(f"VIDEO PRONTO: {final} ({total:.1f}s)")

    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
