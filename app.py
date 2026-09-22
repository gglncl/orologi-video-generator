import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts
import requests
import streamlit as st

APP_TITLE = "Orologi Video Generator"
PEXELS_SEARCH = "https://api.pexels.com/v1/videos/search"

VOICES = {
    "Diego — energica": "it-IT-DiegoNeural",
    "Giuseppe — naturale": "it-IT-GiuseppeNeural",
    "Rinaldo — pulita": "it-IT-RinaldoNeural",
    "Alessio — moderna": "it-IT-AlessioMultilingualNeural",
}

st.set_page_config(page_title=APP_TITLE, page_icon="⌚", layout="centered")

st.markdown(
    """
    <style>
      .block-container {max-width: 860px; padding-top: 1.4rem; padding-bottom: 3rem;}
      .stButton>button {width:100%; border-radius:12px; font-weight:700; min-height:46px;}
      .small-note {opacity:.72; font-size:.9rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def get_pexels_key() -> str:
    try:
        return st.secrets["PEXELS_API_KEY"]
    except Exception:
        return os.getenv("PEXELS_API_KEY", "")


def run(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-4000:] or "Comando fallito")
    return proc.stdout


def ffprobe_duration(path: Path) -> float:
    out = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ])
    return max(0.1, float(out.strip()))


def synthesize(text: str, voice: str, rate: int, out_mp3: Path):
    rate_str = f"{rate:+d}%"
    communicate = edge_tts.Communicate(
        text.strip(), voice=voice, rate=rate_str, boundary="WordBoundary"
    )
    boundaries = []
    with open(out_mp3, "wb") as file:
        for chunk in communicate.stream_sync():
            if chunk["type"] == "audio":
                file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10_000_000,
                    "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                })
    if not out_mp3.exists() or out_mp3.stat().st_size < 1000:
        raise RuntimeError("La voce non è stata generata correttamente.")
    return boundaries


def suggest_queries(script: str):
    s = script.lower()
    rules = [
        (["quarzo", "batteria"], "quartz watch close up"),
        (["automatic", "automatico"], "automatic watch macro"),
        (["rotore"], "automatic watch rotor movement"),
        (["ingranaggi", "ruote", "meccanica", "movimento"], "mechanical watch movement macro"),
        (["corona", "carica", "ricaricare"], "winding automatic watch crown"),
        (["cinturino"], "watch strap close up"),
        (["polso"], "luxury watch wrist close up"),
        (["lusso", "migliaia", "costoso", "rolex", "omega", "patek"], "luxury watch macro"),
        (["quadrante", "lancetta", "secondi"], "watch dial second hand macro"),
        (["gioielli", "jewels", "rubini"], "watch movement ruby jewels macro"),
    ]
    found = []
    for words, query in rules:
        if any(word in s for word in words) and query not in found:
            found.append(query)

    defaults = [
        "luxury watch macro",
        "mechanical watch movement macro",
        "automatic watch wrist close up",
        "watchmaker watch movement",
        "watch dial macro",
    ]
    for query in defaults:
        if query not in found:
            found.append(query)
        if len(found) >= 5:
            break
    return found[:5]


def pexels_search(query: str, api_key: str):
    params = {
        "query": query,
        "orientation": "portrait",
        "size": "medium",
        "per_page": 12,
        "locale": "it-IT",
    }
    response = requests.get(
        PEXELS_SEARCH,
        headers={"Authorization": api_key},
        params=params,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Pexels API: {response.status_code} — {response.text[:300]}")

    videos = response.json().get("videos", [])
    if not videos:
        params["locale"] = "en-US"
        response = requests.get(
            PEXELS_SEARCH,
            headers={"Authorization": api_key},
            params=params,
            timeout=30,
        )
        videos = response.json().get("videos", []) if response.ok else []
    return videos


def choose_file(video):
    files = [
        item for item in video.get("video_files", [])
        if item.get("file_type") == "video/mp4" and item.get("link")
    ]
    if not files:
        return None

    def score(item):
        width, height = item.get("width") or 1, item.get("height") or 1
        portrait_bonus = 1 if height >= width else 0
        target_ratio = 9 / 16
        ratio_penalty = abs((width / height) - target_ratio)
        size_bonus = min(width * height, 1080 * 1920) / (1080 * 1920)
        return portrait_bonus * 10 + size_bonus * 2 - ratio_penalty

    return max(files, key=score)


def download(url: str, path: Path):
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(1024 * 512):
                if chunk:
                    file.write(chunk)


def normalize_clip(src: Path, dst: Path, seconds: float):
    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{seconds:.3f}",
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=30,eq=contrast=1.04:saturation=0.94",
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "22", "-pix_fmt", "yuv420p", str(dst)
    ])


def ass_time(seconds: float) -> str:
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def ass_escape(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", r"\N")
    )


def grouped_caption_events(boundaries, words_per_caption=4):
    events = []
    if not boundaries:
        return events

    group = []
    for item in boundaries:
        group.append(item)
        if len(group) >= words_per_caption:
            events.append(
                (group[0]["start"], group[-1]["end"], " ".join(x["text"] for x in group))
            )
            group = []

    if group:
        events.append(
            (group[0]["start"], group[-1]["end"], " ".join(x["text"] for x in group))
        )
    return events


def write_ass(path: Path, boundaries, hook: str, outro: str, duration: float):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,DejaVu Sans,64,&H00FFFFFF,&H000000FF,&H00101010,&H66000000,-1,0,0,0,100,100,0,0,1,5,0,2,70,70,220,1
Style: Hook,DejaVu Sans,84,&H00FFFFFF,&H000000FF,&H00101010,&H44000000,-1,0,0,0,100,100,0,0,1,6,0,8,70,70,135,1
Style: Outro,DejaVu Sans,70,&H00FFFFFF,&H000000FF,&H00101010,&H44000000,-1,0,0,0,100,100,0,0,1,6,0,5,70,70,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    for start, end, text in grouped_caption_events(boundaries, 4):
        end = max(end, start + 0.55)
        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Captions,,0,0,0,,{ass_escape(text)}\n"
        )

    if hook.strip():
        lines.append(
            f"Dialogue: 1,0:00:00.00,{ass_time(min(duration, 2.8))},Hook,,0,0,0,,"
            f"{ass_escape(hook.upper())}\n"
        )

    if outro.strip() and duration > 2:
        lines.append(
            f"Dialogue: 1,{ass_time(max(0, duration - 2.3))},{ass_time(duration)},Outro,,0,0,0,,"
            f"{ass_escape(outro)}\n"
        )

    path.write_text("".join(lines), encoding="utf-8")


def build_video(script, hook, outro, queries, voice, rate, api_key, status):
    workdir = Path(tempfile.mkdtemp(prefix="orologi_"))
    try:
        audio = workdir / "voice.mp3"
        status.write("🎙️ Creo la voce e i sottotitoli…")
        boundaries = synthesize(script, voice, rate, audio)
        duration = ffprobe_duration(audio)

        clips = []
        credits = []

        status.write("🎞️ Cerco le clip più adatte su Pexels…")
        for index, query in enumerate(queries):
            videos = pexels_search(query, api_key)
            if not videos:
                continue

            chosen = videos[min(index, len(videos) - 1)]
            video_file = choose_file(chosen)
            if not video_file:
                continue

            raw = workdir / f"raw_{len(clips)}.mp4"
            download(video_file["link"], raw)
            clips.append(raw)
            credits.append({
                "query": query,
                "creator": (chosen.get("user") or {}).get("name", "Pexels creator"),
                "url": chosen.get("url", "https://www.pexels.com"),
            })

            if len(clips) >= 5:
                break

        if len(clips) < 3:
            raise RuntimeError(
                "Ho trovato troppo poche clip. Prova a rendere più generiche le ricerche visual."
            )

        status.write("✂️ Monto automaticamente il video verticale…")
        seconds_per_clip = duration / len(clips)
        normalized = []

        for index, clip in enumerate(clips):
            output = workdir / f"clip_{index}.mp4"
            clip_duration = (
                seconds_per_clip
                if index < len(clips) - 1
                else max(0.5, duration - seconds_per_clip * (len(clips) - 1))
            )
            normalize_clip(clip, output, clip_duration)
            normalized.append(output)

        concat = workdir / "concat.txt"
        concat.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in normalized),
            encoding="utf-8",
        )

        visuals = workdir / "visuals.mp4"
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-c", "copy", str(visuals)
        ])

        subtitles = workdir / "captions.ass"
        write_ass(subtitles, boundaries, hook, outro, duration)

        final_video = workdir / "video_finale.mp4"
        status.write("🔥 Inserisco hook, sottotitoli e audio…")
        run([
            "ffmpeg", "-y",
            "-i", str(visuals),
            "-i", str(audio),
            "-vf", f"ass={subtitles.as_posix()}",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(final_video),
        ])

        return final_video.read_bytes(), duration, credits
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


st.title("⌚ Orologi Video Generator")
st.caption("Script → voce → clip → sottotitoli → MP4 verticale. Un solo pulsante.")

api_key = get_pexels_key()
if not api_key:
    st.warning(
        "Manca la chiave Pexels. Aggiungila nei Secrets con nome `PEXELS_API_KEY`."
    )

script = st.text_area(
    "Script",
    height=220,
    placeholder="Incolla qui lo script del video…",
)

left, right = st.columns(2)
with left:
    hook = st.text_input("Hook iniziale", placeholder="5.000 € E SI FERMA?")
with right:
    outro = st.text_input(
        "Testo finale",
        value="OROLOGI SPIEGATI SEMPLICE ⌚",
    )

voice_label = st.selectbox("Voce", list(VOICES.keys()), index=0)
rate = st.slider(
    "Velocità voce",
    min_value=-10,
    max_value=25,
    value=6,
    step=1,
    format="%d%%",
)

default_queries = "\n".join(suggest_queries(script if script.strip() else ""))
queries_text = st.text_area(
    "Visual da cercare automaticamente su Pexels (uno per riga)",
    value=default_queries,
    height=135,
    help="Sono proposti automaticamente dallo script. Puoi lasciarli così oppure cambiarli.",
)

col_a, col_b = st.columns(2)

with col_a:
    if st.button("🔊 Prova voce"):
        if not script.strip():
            st.error("Prima incolla uno script.")
        else:
            with st.spinner("Creo un'anteprima della voce…"):
                workdir = Path(tempfile.mkdtemp(prefix="voice_test_"))
                try:
                    preview = workdir / "preview.mp3"
                    synthesize(
                        script.strip()[:240],
                        VOICES[voice_label],
                        rate,
                        preview,
                    )
                    st.audio(preview.read_bytes(), format="audio/mp3")
                except Exception as exc:
                    st.error(f"Errore voce: {exc}")
                finally:
                    shutil.rmtree(workdir, ignore_errors=True)

with col_b:
    generate = st.button("🎬 GENERA VIDEO", type="primary")

if generate:
    if not script.strip():
        st.error("Incolla prima lo script.")
    elif not api_key:
        st.error("Aggiungi prima `PEXELS_API_KEY` nei Secrets.")
    else:
        queries = [
            query.strip()
            for query in queries_text.splitlines()
            if query.strip()
        ][:5]

        if len(queries) < 3:
            st.error("Servono almeno 3 ricerche visual.")
        else:
            status = st.empty()
            try:
                with st.spinner("Sto creando il Reel completo…"):
                    video_bytes, duration, credits = build_video(
                        script.strip(),
                        hook.strip(),
                        outro.strip(),
                        queries,
                        VOICES[voice_label],
                        rate,
                        api_key,
                        status,
                    )

                status.success(f"✅ Video pronto — {duration:.1f} secondi")
                st.video(video_bytes)
                st.download_button(
                    "⬇️ Scarica MP4 pronto per TikTok",
                    data=video_bytes,
                    file_name="orologi_video.mp4",
                    mime="video/mp4",
                    type="primary",
                )

                st.text_area(
                    "Caption pronta",
                    value=(
                        f"{hook.strip() or 'Orologi spiegati semplice.'} ⌚\n\n"
                        "Segui la pagina per capire il mondo degli orologi "
                        "senza tecnicismi inutili.\n\n"
                        "#orologi #orologeria #watches #automaticwatch #luxurywatches"
                    ),
                    height=130,
                )

                with st.expander("Fonti visual Pexels"):
                    for credit in credits:
                        st.markdown(
                            f"- **{credit['query']}** — {credit['creator']} — "
                            f"[Pexels]({credit['url']})"
                        )
            except Exception as exc:
                status.empty()
                st.error(f"Generazione fallita: {exc}")
                st.info(
                    "Se l'errore riguarda la voce, riprova dopo qualche secondo. "
                    "Se riguarda Pexels, rendi più generiche le ricerche visual."
                )

st.divider()
st.markdown(
    """
    <div class='small-note'>
    Visual forniti tramite <a href="https://www.pexels.com" target="_blank">Pexels</a>.
    La voce della V1 usa il servizio online Microsoft Edge tramite la libreria open-source
    edge-tts: non richiede una chiave, ma è pensata come soluzione di prototipo e può
    cambiare in futuro.
    </div>
    """,
    unsafe_allow_html=True,
)
