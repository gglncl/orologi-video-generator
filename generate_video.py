import asyncio
import hashlib
import json
import os
import re
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
TARGET_SCENE_SECONDS = 3.3
MIN_SCENES = 7
MAX_SCENES = 10


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


SCENE_CONCEPTS = [
    (["21 jewels", "jewels", "rubini", "rubino", "pietre", "gioielli"], [
        "mechanical wristwatch ruby jewels movement macro",
        "watch movement ruby jewel close up",
        "mechanical wristwatch movement macro",
    ]),
    (["water resistant", "30 metri", "50 metri", "100 metri", "impermeabile", "impermeabilità"], [
        "wristwatch water resistance case back close up",
        "wristwatch crown gasket close up",
        "wristwatch case back macro",
    ]),
    (["pioggia", "schizzi", "lavarsi le mani", "lavare le mani", "rubinetto"], [
        "person washing hands wearing wristwatch",
        "wristwatch water splash close up",
        "wristwatch on wrist everyday close up",
    ]),
    (["nuotare", "nuoto", "piscina", "immersione", "subacqueo", "diver"], [
        "diver wristwatch on wrist close up",
        "diver watch bezel close up",
        "diver wristwatch underwater close up",
    ]),
    (["comodino", "tavolo", "fermo", "si ferma", "ritrovato fermo", "lasciato"], [
        "mechanical wristwatch on table close up",
        "automatic wristwatch resting on table",
        "wristwatch bedside table close up",
    ]),
    (["rotore", "massa oscillante", "oscillante"], [
        "automatic wristwatch rotor movement macro",
        "automatic watch rotor close up",
        "mechanical wristwatch movement macro",
    ]),
    (["molla", "molla principale", "riserva di carica", "energia accumulata", "autonomia"], [
        "mechanical wristwatch mainspring movement macro",
        "mechanical watch gears spring macro",
        "mechanical wristwatch movement close up",
    ]),
    (["corona", "carica manuale", "ricaricare", "ricarica", "caricare"], [
        "hand winding mechanical wristwatch crown close up",
        "wristwatch crown being wound close up",
        "mechanical wristwatch crown macro",
    ]),
    (["polso", "indossarlo", "indossare", "indossi", "indossato"], [
        "mechanical wristwatch on wrist close up",
        "person wearing mechanical wristwatch",
        "wristwatch wrist lifestyle close up",
    ]),
    (["quarzo", "batteria", "cristallo"], [
        "quartz wristwatch movement battery close up",
        "quartz watch movement macro",
        "wristwatch battery movement close up",
    ]),
    (["precisione", "preciso", "precisa", "secondi", "lancetta"], [
        "wristwatch dial second hand macro",
        "wristwatch second hand close up",
        "watch dial macro",
    ]),
    (["ingranaggi", "ruote", "meccanica", "movimento meccanico", "movimento"], [
        "mechanical wristwatch movement gears macro",
        "watch gears movement close up",
        "mechanical watch movement macro",
    ]),
    (["orologiaio", "riparazione", "assemblaggio", "lavorazione", "manutenzione"], [
        "watchmaker repairing wristwatch macro",
        "watchmaker hands watch movement close up",
        "watchmaker workshop wristwatch",
    ]),
    (["investimento", "valore", "rivendita", "mercato dell'usato", "mercato usato", "prezzo", "listino"], [
        "luxury wristwatch store display close up",
        "wristwatch shopping display close up",
        "luxury mechanical wristwatch showcase",
    ]),
    (["lusso", "migliaia", "costoso", "rolex", "omega", "patek", "marchio"], [
        "luxury mechanical wristwatch close up",
        "premium wristwatch macro",
        "luxury wristwatch on wrist close up",
    ]),
    (["quadrante", "indici", "numeri romani", "lancette"], [
        "wristwatch dial macro",
        "watch face close up",
        "wristwatch hands dial close up",
    ]),
    (["cinturino", "bracciale", "fibbie"], [
        "wristwatch strap bracelet close up",
        "watch bracelet clasp macro",
        "wristwatch leather strap close up",
    ]),
    (["cronografo", "cronometro", "pulsante", "pulsanti"], [
        "chronograph wristwatch pushers close up",
        "chronograph watch dial macro",
        "chronograph wristwatch close up",
    ]),
    (["automatico", "automatic"], [
        "automatic mechanical wristwatch close up",
        "automatic wristwatch movement macro",
        "mechanical wristwatch on wrist",
    ]),
]

GENERIC_VISUALS = [
    "mechanical wristwatch movement gears macro",
    "wristwatch dial macro",
    "mechanical wristwatch on wrist close up",
    "watchmaker hands wristwatch close up",
    "mechanical wristwatch crown macro",
    "luxury mechanical wristwatch close up",
    "wristwatch case back close up",
    "wristwatch bracelet close up",
]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_semantic_units(script: str):
    script = clean_text(script)
    if not script:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", script)
    units = []
    for sentence in sentences:
        sentence = sentence.strip(" -")
        if not sentence:
            continue

        if len(sentence) > 115:
            chunks = re.split(
                r"\s*(?:;|:|,\s+(?:ma|perché|quindi|quando|mentre|e invece|invece|oppure|però)\s+)\s*",
                sentence,
                flags=re.I,
            )
            chunks = [clean_text(x) for x in chunks if len(clean_text(x)) >= 18]
            if len(chunks) > 1:
                units.extend(chunks)
                continue

        units.append(sentence)

    return units


def fit_units_to_scene_count(units, target_count):
    units = [clean_text(u) for u in units if clean_text(u)]
    if not units:
        return []

    while len(units) < target_count:
        splittable = [(len(u), i, u) for i, u in enumerate(units) if len(u) > 70]
        if not splittable:
            break

        _, idx, unit = max(splittable)
        parts = re.split(
            r"\s*,\s*|\s+(?:e|ma|perché|quindi|mentre|quando|invece)\s+",
            unit,
            maxsplit=1,
            flags=re.I,
        )
        parts = [clean_text(p) for p in parts if len(clean_text(p)) >= 16]
        if len(parts) != 2:
            break

        units[idx:idx + 1] = parts

    while len(units) > target_count:
        best_idx = None
        best_len = None
        for i in range(len(units) - 1):
            combined_len = len(units[i]) + len(units[i + 1])
            if best_len is None or combined_len < best_len:
                best_len = combined_len
                best_idx = i

        units[best_idx:best_idx + 2] = [
            clean_text(units[best_idx] + " " + units[best_idx + 1])
        ]

    return units


def score_concept(text: str, keywords):
    t = text.lower()
    score = 0
    for keyword in keywords:
        if keyword in t:
            score += 3 if " " in keyword else 1
    return score


def query_candidates_for_unit(unit: str, scene_index: int):
    ranked = []
    for idx, (keywords, queries) in enumerate(SCENE_CONCEPTS):
        score = score_concept(unit, keywords)
        if score:
            ranked.append((score, -idx, queries))

    ranked.sort(reverse=True)
    candidates = []

    for _, _, queries in ranked[:2]:
        for q in queries:
            if q not in candidates:
                candidates.append(q)

    if not candidates:
        start = scene_index % len(GENERIC_VISUALS)
        candidates = [
            GENERIC_VISUALS[(start + offset) % len(GENERIC_VISUALS)]
            for offset in range(4)
        ]

    for offset in range(2):
        q = GENERIC_VISUALS[(scene_index + offset * 3) % len(GENERIC_VISUALS)]
        if q not in candidates:
            candidates.append(q)

    return candidates[:6]


def manual_scene_queries():
    if not VISUAL_QUERIES:
        return []
    return [q.strip() for q in VISUAL_QUERIES.split(",") if q.strip()]


def build_scene_plan(script: str, total_seconds: float):
    target_count = round(total_seconds / TARGET_SCENE_SECONDS)
    target_count = max(MIN_SCENES, min(MAX_SCENES, target_count))

    units = split_semantic_units(script)
    units = fit_units_to_scene_count(units, target_count)

    if not units:
        units = [script]

    while len(units) < MIN_SCENES:
        units.append(units[len(units) % len(units)])

    return units[:MAX_SCENES]


def scene_durations(scene_units, total_seconds: float):
    weights = [max(1, len(re.findall(r"\w+", u))) for u in scene_units]
    total_weight = sum(weights) or len(scene_units)

    raw = [total_seconds * w / total_weight for w in weights]
    durations = [min(4.6, max(2.1, x)) for x in raw]

    current = sum(durations)
    if current <= 0:
        return [total_seconds / len(scene_units)] * len(scene_units)

    scale = total_seconds / current
    durations = [x * scale for x in durations]

    if durations:
        durations[-1] += total_seconds - sum(durations)

    return durations


def search_videos(query: str):
    for page in [1, 2]:
        base = {
            "query": query,
            "size": "medium",
            "per_page": 24,
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


def video_relevance(video, query: str, rank: int):
    url = str(video.get("url") or "").lower()
    query_words = [
        w for w in re.findall(r"[a-z]+", query.lower())
        if w not in {"close", "up", "macro", "cinematic", "mechanical", "automatic"}
    ]

    score = max(0, 30 - rank * 3)

    if "watch" in url or "wrist" in url or "timepiece" in url:
        score += 45

    for word in query_words:
        if len(word) >= 4 and word in url:
            score += 8

    w = video.get("width") or 1
    h = video.get("height") or 1
    if h > w:
        score += 12

    return score


def pick_video(videos, query: str, used_ids: set, scene_index: int):
    viable = []

    for rank, video in enumerate(videos[:10]):
        if video.get("id") in used_ids:
            continue
        media = choose_mp4(video)
        if not media:
            continue
        viable.append((video_relevance(video, query, rank), rank, video, media))

    if not viable:
        return None, None

    viable.sort(key=lambda x: (-x[0], x[1]))

    shortlist = viable[:min(3, len(viable))]
    pick = stable_number(f"{SAFE_REQUEST_ID}:{query}:{scene_index}") % len(shortlist)
    _, _, video, media = shortlist[pick]
    return video, media


def download(url: str, dest: Path):
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)


def normalize_clip(src: Path, dst: Path, seconds: float, scene_index: int):
    scale_w = 1100 if scene_index % 2 == 0 else 1140
    scale_h = 1956 if scene_index % 2 == 0 else 2027

    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{seconds:.3f}",
        "-vf",
        f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=30,eq=contrast=1.03:saturation=0.97",
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


def write_ass(path: Path, boundaries):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,DejaVu Sans,58,&H00FFFFFF,&H000000FF,&H00101010,&H66000000,-1,0,0,0,100,100,0,0,1,5,0,2,90,90,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]

    for start, end, text in caption_groups(boundaries, 4):
        end = max(end, start + 0.45)
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Captions,,0,0,0,,{esc(text)}\n"
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

        scene_units = build_scene_plan(SCRIPT, total)
        durations = scene_durations(scene_units, total)
        manual_queries = manual_scene_queries()

        print(f"Scene automatiche: {len(scene_units)}")
        for i, unit in enumerate(scene_units, 1):
            print(f"{i}. {unit}")

        print("2/5 Cerco visual coerenti su Pexels...")
        raw_clips = []
        credits = []
        used_ids = set()
        scene_debug = []

        for scene_index, unit in enumerate(scene_units):
            if manual_queries:
                base_query = manual_queries[scene_index % len(manual_queries)]
                query_candidates = [base_query]
                for q in GENERIC_VISUALS:
                    if q not in query_candidates:
                        query_candidates.append(q)
            else:
                query_candidates = query_candidates_for_unit(unit, scene_index)

            chosen = None
            media = None
            chosen_query = None

            for query in query_candidates:
                videos = search_videos(query)
                chosen, media = pick_video(
                    videos,
                    query,
                    used_ids,
                    scene_index,
                )
                if chosen and media:
                    chosen_query = query
                    break

            if not chosen or not media:
                raise RuntimeError(
                    f"Nessun visual utilizzabile per la scena {scene_index + 1}: {unit}"
                )

            dest = work / f"raw_{scene_index}.mp4"
            download(media["link"], dest)

            raw_clips.append(dest)
            used_ids.add(chosen.get("id"))

            creator = (chosen.get("user") or {}).get("name", "Pexels creator")
            chosen_url = chosen.get("url", "https://www.pexels.com")

            credits.append(
                f"Scena {scene_index + 1} | {chosen_query} | "
                f"id={chosen.get('id')} | {creator} | {chosen_url}"
            )

            scene_debug.append({
                "scene": scene_index + 1,
                "text": unit,
                "query": chosen_query,
                "video_id": chosen.get("id"),
                "seconds": round(durations[scene_index], 2),
            })

            print(
                f"Scena {scene_index + 1}: query='{chosen_query}' "
                f"id={chosen.get('id')} durata={durations[scene_index]:.1f}s"
            )

        print("3/5 Adatto le clip al formato TikTok...")
        norm = []

        for i, (src, seconds) in enumerate(zip(raw_clips, durations)):
            dst = work / f"norm_{i}.mp4"
            normalize_clip(src, dst, seconds, i)
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

        print("4/5 Creo sottotitoli...")
        ass = work / "captions.ass"
        write_ass(ass, boundaries)

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

        (OUT / "scene_plan.json").write_text(
            json.dumps(scene_debug, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        caption = (
            f"{HOOK or 'Orologi in parole semplici'} ⌚\n\n"
            "Curiosità, meccanica e falsi miti sugli orologi.\n\n"
            "#orologi #orologeria #watches #watchtok"
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
                "scene_count": len(scene_debug),
                "scenes": scene_debug,
                "video_ids": list(used_ids),
            }
        )

        print(f"VIDEO PRONTO: {final} ({total:.1f}s)")

    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
