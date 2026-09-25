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
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
OUT = Path("output")
OUT.mkdir(exist_ok=True)

SCRIPT = os.environ.get("SCRIPT", "").strip()
HOOK = os.environ.get("HOOK", "").strip()
OUTRO = os.environ.get("OUTRO", "").strip()
VOICE = os.environ.get("VOICE", "it-IT-GiuseppeMultilingualNeural").strip()
RATE = os.environ.get("RATE", "+6%").strip()
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
VISUAL_QUERIES = os.environ.get("VISUAL_QUERIES", "").strip()
MODE = os.environ.get("MODE", "generate_video").strip()
REQUEST_ID = os.environ.get("REQUEST_ID", "manual").strip()

SAFE_REQUEST_ID = "".join(ch for ch in REQUEST_ID if ch.isalnum())[:40] or "manual"
GIUSEPPE = "it-IT-GiuseppeMultilingualNeural"

TARGET_SCENE_SECONDS = 3.2
MIN_SCENES = 8
MAX_SCENES = 11


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
    selected_voice = GIUSEPPE
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


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# Query catalog con tipi visuali. Il motore non deve solo cambiare query,
# ma anche alternare famiglie visive: quadrante, polso, movimento, watchmaker...
CONCEPTS = [
    {
        "keywords": ["21 jewels", "jewels", "rubini", "rubino", "pietre", "gioielli"],
        "queries": [
            ("mechanical wristwatch ruby jewel movement macro", "movement"),
            ("watch movement jewel close up", "movement"),
            ("watchmaker mechanical movement ruby jewel", "watchmaker"),
            ("mechanical watch gears macro", "movement"),
            ("watchmaker wristwatch movement close up", "watchmaker"),
        ],
    },
    {
        "keywords": ["water resistant", "30 metri", "50 metri", "100 metri", "impermeabile", "impermeabilità", "acqua"],
        "queries": [
            ("wristwatch case back close up", "caseback"),
            ("wristwatch crown close up", "crown"),
            ("wristwatch on wrist everyday close up", "wrist"),
            ("watchmaker inspecting wristwatch close up", "watchmaker"),
            ("wristwatch water splash close up", "water"),
            ("diver watch bezel macro", "bezel"),
            ("diver wristwatch on wrist close up", "diver"),
            ("stainless steel wristwatch macro", "case"),
        ],
    },
    {
        "keywords": ["pioggia", "schizzi", "lavarsi le mani", "lavare le mani", "rubinetto"],
        "queries": [
            ("person washing hands wearing wristwatch", "lifestyle"),
            ("wristwatch water splash close up", "water"),
            ("wristwatch in rain close up", "water"),
            ("wristwatch on wrist everyday close up", "wrist"),
        ],
    },
    {
        "keywords": ["nuotare", "nuoto", "piscina", "immersione", "subacqueo", "diver"],
        "queries": [
            ("diver wristwatch on wrist close up", "diver"),
            ("diver watch bezel macro", "bezel"),
            ("diver watch crown close up", "crown"),
            ("diver wristwatch underwater close up", "diver"),
        ],
    },
    {
        "keywords": ["comodino", "tavolo", "fermo", "si ferma", "ritrovato fermo", "lasciato"],
        "queries": [
            ("mechanical wristwatch on table close up", "table"),
            ("wristwatch resting on desk close up", "table"),
            ("wristwatch bedside table close up", "table"),
            ("automatic wristwatch close up", "case"),
        ],
    },
    {
        "keywords": ["rotore", "massa oscillante", "oscillante"],
        "queries": [
            ("automatic wristwatch rotor movement macro", "movement"),
            ("automatic watch rotor close up", "movement"),
            ("watchmaker automatic movement close up", "watchmaker"),
            ("mechanical wristwatch movement macro", "movement"),
        ],
    },
    {
        "keywords": ["molla", "riserva di carica", "energia accumulata", "autonomia"],
        "queries": [
            ("mechanical wristwatch mainspring movement macro", "movement"),
            ("mechanical watch gears spring macro", "movement"),
            ("watchmaker mechanical movement close up", "watchmaker"),
            ("mechanical wristwatch movement close up", "movement"),
        ],
    },
    {
        "keywords": ["corona", "carica manuale", "ricaricare", "ricarica", "caricare"],
        "queries": [
            ("hand winding mechanical wristwatch crown close up", "crown"),
            ("wristwatch crown being wound close up", "crown"),
            ("mechanical wristwatch crown macro", "crown"),
            ("watchmaker adjusting wristwatch crown", "watchmaker"),
        ],
    },
    {
        "keywords": ["polso", "indossarlo", "indossare", "indossi", "indossato"],
        "queries": [
            ("mechanical wristwatch on wrist close up", "wrist"),
            ("person wearing mechanical wristwatch", "wrist"),
            ("wristwatch wrist lifestyle close up", "lifestyle"),
            ("luxury wristwatch on wrist close up", "wrist"),
        ],
    },
    {
        "keywords": ["quarzo", "batteria", "cristallo"],
        "queries": [
            ("quartz wristwatch movement battery close up", "movement"),
            ("quartz watch movement macro", "movement"),
            ("wristwatch battery movement close up", "movement"),
            ("watchmaker quartz watch close up", "watchmaker"),
        ],
    },
    {
        "keywords": ["precisione", "preciso", "precisa", "secondi", "lancetta"],
        "queries": [
            ("wristwatch dial second hand macro", "dial"),
            ("wristwatch second hand close up", "dial"),
            ("watch dial macro", "dial"),
            ("mechanical wristwatch dial close up", "dial"),
        ],
    },
    {
        "keywords": ["ingranaggi", "ruote", "meccanica", "movimento meccanico", "movimento"],
        "queries": [
            ("mechanical wristwatch movement gears macro", "movement"),
            ("watch gears movement close up", "movement"),
            ("watchmaker mechanical movement close up", "watchmaker"),
            ("mechanical watch movement macro", "movement"),
        ],
    },
    {
        "keywords": ["orologiaio", "riparazione", "assemblaggio", "lavorazione", "manutenzione"],
        "queries": [
            ("watchmaker repairing wristwatch macro", "watchmaker"),
            ("watchmaker hands watch movement close up", "watchmaker"),
            ("watchmaker workshop wristwatch", "watchmaker"),
            ("watchmaker tools wristwatch close up", "watchmaker"),
        ],
    },
    {
        "keywords": ["investimento", "valore", "rivendita", "mercato dell'usato", "mercato usato", "prezzo", "listino"],
        "queries": [
            ("luxury wristwatch store display close up", "store"),
            ("wristwatch shopping display close up", "store"),
            ("luxury mechanical wristwatch showcase", "store"),
            ("person trying luxury wristwatch in store", "store"),
            ("wristwatch collection close up", "collection"),
        ],
    },
    {
        "keywords": ["lusso", "migliaia", "costoso", "rolex", "omega", "patek", "marchio"],
        "queries": [
            ("luxury mechanical wristwatch close up", "case"),
            ("premium wristwatch macro", "case"),
            ("luxury wristwatch on wrist close up", "wrist"),
            ("luxury wristwatch showcase close up", "store"),
        ],
    },
    {
        "keywords": ["quadrante", "indici", "numeri romani", "lancette"],
        "queries": [
            ("wristwatch dial macro", "dial"),
            ("watch face close up", "dial"),
            ("wristwatch hands dial close up", "dial"),
            ("mechanical wristwatch dial close up", "dial"),
        ],
    },
    {
        "keywords": ["cinturino", "bracciale", "fibbie"],
        "queries": [
            ("wristwatch strap bracelet close up", "bracelet"),
            ("watch bracelet clasp macro", "bracelet"),
            ("wristwatch leather strap close up", "bracelet"),
            ("stainless steel watch bracelet macro", "bracelet"),
        ],
    },
    {
        "keywords": ["cronografo", "cronometro", "pulsante", "pulsanti"],
        "queries": [
            ("chronograph wristwatch pushers close up", "pushers"),
            ("chronograph watch dial macro", "dial"),
            ("chronograph wristwatch close up", "case"),
            ("chronograph on wrist close up", "wrist"),
        ],
    },
    {
        "keywords": ["automatico", "automatic"],
        "queries": [
            ("automatic mechanical wristwatch close up", "case"),
            ("automatic wristwatch movement macro", "movement"),
            ("mechanical wristwatch on wrist", "wrist"),
            ("watchmaker automatic wristwatch close up", "watchmaker"),
        ],
    },
]

GENERIC_QUERIES = [
    ("mechanical wristwatch movement gears macro", "movement"),
    ("wristwatch dial macro", "dial"),
    ("mechanical wristwatch on wrist close up", "wrist"),
    ("watchmaker hands wristwatch close up", "watchmaker"),
    ("mechanical wristwatch crown macro", "crown"),
    ("luxury mechanical wristwatch close up", "case"),
    ("wristwatch case back close up", "caseback"),
    ("wristwatch bracelet close up", "bracelet"),
    ("watchmaker workshop wristwatch", "watchmaker"),
    ("stainless steel wristwatch macro", "case"),
    ("diver watch bezel macro", "bezel"),
    ("wristwatch resting on desk close up", "table"),
]


def split_semantic_units(script: str):
    script = clean_text(script)
    if not script:
        return []

    pieces = re.split(
        r"(?<=[.!?])\s+|;\s*|:\s*|,\s+(?=(?:ma|perché|quindi|quando|mentre|invece|oppure|però|e)\b)",
        script,
        flags=re.I,
    )
    pieces = [clean_text(p) for p in pieces if len(clean_text(p)) >= 12]

    result = []
    for piece in pieces:
        if len(piece) <= 95:
            result.append(piece)
            continue
        words = piece.split()
        mid = len(words) // 2
        left = " ".join(words[:mid]).strip()
        right = " ".join(words[mid:]).strip()
        if len(left) >= 10 and len(right) >= 10:
            result.extend([left, right])
        else:
            result.append(piece)
    return result


def fit_scene_count(units, target_count):
    units = [u for u in units if u]
    while len(units) < target_count:
        idx = max(range(len(units)), key=lambda i: len(units[i]))
        words = units[idx].split()
        if len(words) < 12:
            break
        mid = len(words) // 2
        left = " ".join(words[:mid]).strip()
        right = " ".join(words[mid:]).strip()
        if len(left) < 10 or len(right) < 10:
            break
        units[idx:idx + 1] = [left, right]
    while len(units) > target_count:
        idx = min(range(len(units) - 1), key=lambda i: len(units[i]) + len(units[i + 1]))
        units[idx:idx + 2] = [units[idx] + " " + units[idx + 1]]
    return units


def build_scene_plan(script: str, total_seconds: float):
    target_count = round(total_seconds / TARGET_SCENE_SECONDS)
    target_count = max(MIN_SCENES, min(MAX_SCENES, target_count))
    units = split_semantic_units(script)
    if not units:
        units = [script]
    units = fit_scene_count(units, target_count)
    while len(units) < target_count:
        units.append(units[len(units) % len(units)])
    return units[:target_count]


def scene_durations(scene_units, total_seconds: float):
    base = total_seconds / len(scene_units)
    durations = [base for _ in scene_units]
    durations[-1] += total_seconds - sum(durations)
    return durations


def concept_query_entries_for_text(text: str):
    """Restituisce SOLO visual semanticamente collegati al testo della scena."""
    t = text.lower()
    ranked = []

    for idx, concept in enumerate(CONCEPTS):
        score = 0
        for kw in concept["keywords"]:
            if kw in t:
                score += 3 if " " in kw else 1
        if score:
            ranked.append((score, -idx, concept["queries"]))

    ranked.sort(reverse=True)

    result = []
    for _, _, entries in ranked[:2]:
        for entry in entries:
            if entry not in result:
                result.append(entry)

    return result


def resolve_scene_entries(scene_units):
    """
    V8: una frase generica (es. "Non necessariamente") non riceve più un visual
    casuale. Eredita il tema dalla scena semanticamente più vicina.
    """
    direct = [concept_query_entries_for_text(unit) for unit in scene_units]

    # Pool dell'intero video, usato soltanto se una scena non ha parole chiave.
    topic_pool = []
    for entries in direct:
        for entry in entries:
            if entry not in topic_pool:
                topic_pool.append(entry)

    resolved = []
    for i, entries in enumerate(direct):
        if entries:
            resolved.append(entries)
            continue

        inherited = []

        # Cerca prima nelle scene adiacenti: 1 indietro, 1 avanti, 2 indietro...
        for distance in range(1, len(scene_units)):
            indexes = (i - distance, i + distance)
            for j in indexes:
                if 0 <= j < len(direct) and direct[j]:
                    inherited = direct[j][:]
                    break
            if inherited:
                break

        if not inherited:
            inherited = topic_pool[:] if topic_pool else GENERIC_QUERIES[:]

        resolved.append(inherited)

    return resolved


def order_scene_queries(entries, scene_index: int, used_queries: list[str], used_types: list[str]):
    """
    La pertinenza è già garantita da `entries`.
    Qui la varietà viene usata SOLO come criterio secondario.
    """
    recent_queries = set(used_queries[-3:])
    recent_types = set(used_types[-2:])

    def penalty(entry):
        query, query_type = entry
        p = 0
        if query in recent_queries:
            p += 4
        if query_type in recent_types:
            p += 2
        if query in used_queries:
            p += 1
        # Tie-break deterministico, così rigenerazioni diverse possono variare.
        tie = stable_number(f"{SAFE_REQUEST_ID}:{scene_index}:{query}") % 1000
        return (p, tie)

    return sorted(entries, key=penalty)


def _query_words(query: str):
    return [
        w for w in re.findall(r"[a-z]+", query.lower())
        if len(w) >= 4 and w not in {
            "close", "macro", "mechanical", "automatic", "wristwatch", "watch",
            "luxury", "steel", "person"
        }
    ]


WATCH_SIGNALS = (
    "watch", "wrist", "wristwatch", "timepiece", "watchmaker", "horolog",
    "chronograph", "clockwork", "dial"
)

TYPE_SIGNALS = {
    "dial": ("dial", "second", "hand", "face", "hands"),
    "movement": ("movement", "gear", "gears", "rotor", "spring", "clockwork", "mechanism"),
    "watchmaker": ("watchmaker", "repair", "workshop", "tool", "tools", "movement"),
    "crown": ("crown", "wind", "winding", "adjust", "setting"),
    "wrist": ("wrist", "wear", "wearing"),
    "lifestyle": ("wrist", "wear", "wearing", "hand", "hands"),
    "caseback": ("case", "back", "caseback", "rear"),
    "case": ("case", "steel", "watch", "wristwatch"),
    "water": ("water", "splash", "rain", "wet", "wrist"),
    "diver": ("diver", "diving", "underwater", "bezel", "wrist"),
    "bezel": ("bezel", "diver", "dial"),
    "bracelet": ("bracelet", "strap", "clasp", "band"),
    "pushers": ("pusher", "pushers", "chronograph", "button", "buttons"),
    "table": ("table", "desk", "bedside", "watch", "wristwatch"),
    "store": ("store", "shop", "display", "showcase", "watch", "wristwatch"),
    "collection": ("collection", "watches", "watch", "display"),
}


def candidate_semantic_score(candidate, query: str, query_type: str):
    """
    Punteggio di pertinenza prima della varietà.
    Una clip senza alcun segnale di orologeria viene scartata.
    """
    haystack = (
        f"{candidate.get('tags') or ''} {candidate.get('page_url') or ''}"
    ).lower()

    if not any(signal in haystack for signal in WATCH_SIGNALS):
        return None

    score = 100

    # Il ranking del provider conta, ma meno della pertinenza.
    score += max(0, 24 - int(candidate.get("rank", 0)) * 2)

    w = candidate.get("width") or 1
    h = candidate.get("height") or 1
    if h > w:
        score += 12

    for word in _query_words(query):
        if word in haystack:
            score += 10

    for word in TYPE_SIGNALS.get(query_type, ()):
        if word in haystack:
            score += 12

    return score


_PEXELS_CACHE = {}
_PIXABAY_CACHE = {}


def search_pexels(query: str, scene_index: int):
    if not PEXELS_API_KEY:
        return []

    cache_key = (query, scene_index % 2)
    if cache_key in _PEXELS_CACHE:
        return _PEXELS_CACHE[cache_key]

    first_page = 1 if scene_index % 2 == 0 else 2
    pages = [first_page, 2 if first_page == 1 else 1]

    for page in pages:
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
                print(f"Pexels {r.status_code}: {r.text[:200]}")
                continue

            out = []
            for rank, video in enumerate(r.json().get("videos", [])[:18]):
                media = choose_pexels_mp4(video)
                if not media:
                    continue
                out.append({
                    "source": "pexels",
                    "id": str(video.get("id")),
                    "ref": f"pexels:{video.get('id')}",
                    "media_url": media.get("link"),
                    "page_url": video.get("url") or "https://www.pexels.com",
                    "creator": (video.get("user") or {}).get("name", "Pexels creator"),
                    "width": video.get("width") or media.get("width") or 1,
                    "height": video.get("height") or media.get("height") or 1,
                    "tags": str(video.get("url") or ""),
                    "rank": rank,
                })
            if out:
                _PEXELS_CACHE[cache_key] = out
                return out

    _PEXELS_CACHE[cache_key] = []
    return []


def search_pixabay(query: str, scene_index: int):
    if not PIXABAY_API_KEY:
        return []

    cache_key = (query, scene_index % 2)
    if cache_key in _PIXABAY_CACHE:
        return _PIXABAY_CACHE[cache_key]

    preferred_page = 1 if scene_index % 2 == 0 else 2
    pages = [preferred_page, 2 if preferred_page == 1 else 1]

    for page in pages:
        params = {
            "key": PIXABAY_API_KEY,
            "q": query[:100],
            "lang": "en",
            "video_type": "film",
            "safesearch": "true",
            "order": "popular",
            "page": page,
            "per_page": 30,
        }

        r = requests.get(PIXABAY_SEARCH, params=params, timeout=30)
        if not r.ok:
            print(f"Pixabay {r.status_code}: {r.text[:200]}")
            continue

        out = []
        for rank, hit in enumerate(r.json().get("hits", [])[:24]):
            media = choose_pixabay_mp4(hit)
            if not media:
                continue
            out.append({
                "source": "pixabay",
                "id": str(hit.get("id")),
                "ref": f"pixabay:{hit.get('id')}",
                "media_url": media.get("url"),
                "page_url": hit.get("pageURL") or "https://pixabay.com/videos/",
                "creator": hit.get("user") or "Pixabay creator",
                "width": media.get("width") or 1,
                "height": media.get("height") or 1,
                "tags": hit.get("tags") or "",
                "rank": rank,
            })

        if out:
            _PIXABAY_CACHE[cache_key] = out
            return out

    _PIXABAY_CACHE[cache_key] = []
    return []


def choose_pexels_mp4(video):
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


def choose_pixabay_mp4(hit):
    videos = hit.get("videos") or {}
    candidates = []
    for name in ("medium", "large", "small", "tiny"):
        item = videos.get(name) or {}
        if item.get("url"):
            candidates.append(item)
    if not candidates:
        return None

    def score(item):
        w = item.get("width") or 1
        h = item.get("height") or 1
        portrait = 1 if h >= w else 0
        ratio_penalty = abs((w / h) - (9 / 16))
        pixels = min(w * h, 1080 * 1920) / (1080 * 1920)
        return portrait * 20 + pixels * 2 - ratio_penalty

    return max(candidates, key=score)


def best_provider_candidate(query: str, query_type: str, scene_index: int, used_refs: set, used_sources: list[str]):
    """
    Cerca in entrambe le fonti. La fonte diversa vale solo pochi punti:
    non può più battere una clip molto più pertinente.
    """
    candidates = search_pexels(query, scene_index) + search_pixabay(query, scene_index)
    scored = []
    previous_source = used_sources[-1] if used_sources else None

    for candidate in candidates:
        if candidate.get("ref") in used_refs or not candidate.get("media_url"):
            continue

        semantic = candidate_semantic_score(candidate, query, query_type)
        if semantic is None:
            continue

        # Varietà fonte = bonus piccolo, mai priorità principale.
        if previous_source and candidate.get("source") != previous_source:
            semantic += 4

        scored.append((semantic, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1].get("rank", 0)))

    # V8: scegli tra le prime 2 SOLO se quasi equivalenti.
    best_score = scored[0][0]
    shortlist = [c for s, c in scored[:3] if best_score - s <= 8]
    idx = stable_number(
        f"{SAFE_REQUEST_ID}:{scene_index}:{query}:semantic"
    ) % len(shortlist)
    return shortlist[idx]


def choose_scene_candidate(entries, scene_index: int, used_refs: set, used_sources: list[str], used_queries: list[str], used_types: list[str]):
    """
    Prova soltanto query semanticamente valide per quella scena.
    Se una query non produce clip affidabili, passa alla successiva DELLO STESSO TEMA.
    """
    ordered = order_scene_queries(entries, scene_index, used_queries, used_types)

    for query, query_type in ordered[:6]:
        chosen = best_provider_candidate(
            query, query_type, scene_index, used_refs, used_sources
        )
        if chosen:
            return chosen, query, query_type

    return None, None, None

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
    (OUT / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


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
        synthesize(sample, dest, voice=GIUSEPPE)
        write_meta("test_voice", "ok", {"file": filename})
        return
    if not PEXELS_API_KEY and not PIXABAY_API_KEY:
        raise SystemExit("Serve almeno una chiave tra PEXELS_API_KEY e PIXABAY_API_KEY.")

    work = Path(tempfile.mkdtemp(prefix="watchvideo_"))
    try:
        print("1/5 Creo la voce...")
        voice_mp3 = work / "voice.mp3"
        boundaries = synthesize(SCRIPT, voice_mp3, voice=GIUSEPPE)
        total = duration(voice_mp3)

        scene_units = build_scene_plan(SCRIPT, total)
        durations = scene_durations(scene_units, total)
        manual_queries = [q.strip() for q in VISUAL_QUERIES.split(",") if q.strip()]
        resolved_entries = resolve_scene_entries(scene_units)

        print(f"Scene automatiche: {len(scene_units)}")
        print("2/5 Cerco visual pertinenti su Pexels + Pixabay...")

        raw_clips = []
        credits = []
        used_refs = set()
        used_queries = []
        used_types = []
        used_sources = []
        scene_debug = []

        for scene_index, unit in enumerate(scene_units):
            if manual_queries:
                # Modalità manuale: rispetta la query dell'utente, ma continua
                # a filtrare i risultati che non sembrano affatto orologi.
                query = manual_queries[scene_index % len(manual_queries)]
                query_type = "manual"
                chosen = best_provider_candidate(
                    query, query_type, scene_index, used_refs, used_sources
                )
            else:
                chosen, query, query_type = choose_scene_candidate(
                    resolved_entries[scene_index],
                    scene_index,
                    used_refs,
                    used_sources,
                    used_queries,
                    used_types,
                )

            if not chosen:
                # Ultimo fallback: resta comunque dentro il tema generale del video,
                # NON usa più corona/diver/fondello a caso.
                topic_entries = []
                for entries in resolved_entries:
                    for entry in entries:
                        if entry not in topic_entries:
                            topic_entries.append(entry)

                chosen, query, query_type = choose_scene_candidate(
                    topic_entries or GENERIC_QUERIES,
                    scene_index,
                    used_refs,
                    used_sources,
                    used_queries,
                    used_types,
                )

            if not chosen:
                raise RuntimeError(
                    f"Nessun visual sufficientemente pertinente per la scena {scene_index + 1}. "
                    "Meglio fermarsi che inserire una clip fuori tema."
                )

            dest = work / f"raw_{scene_index}.mp4"
            download(chosen["media_url"], dest)
            raw_clips.append(dest)
            used_refs.add(chosen["ref"])
            used_queries.append(query)
            used_types.append(query_type)
            used_sources.append(chosen["source"])

            credits.append(
                f"Scena {scene_index + 1} | fonte={chosen['source']} | {query} | "
                f"tipo={query_type} | id={chosen['id']} | {chosen['creator']} | {chosen['page_url']}"
            )
            scene_debug.append({
                "scene": scene_index + 1,
                "text": unit,
                "query": query,
                "query_type": query_type,
                "source": chosen["source"],
                "video_id": chosen["id"],
                "seconds": round(durations[scene_index], 2),
            })
            print(
                f"Scena {scene_index + 1}: fonte='{chosen['source']}' tipo='{query_type}' "
                f"query='{query}' id={chosen['id']} durata={durations[scene_index]:.1f}s"
            )

        print("3/5 Adatto le clip al formato TikTok...")
        norm = []
        for i, (src, seconds) in enumerate(zip(raw_clips, durations)):
            dst = work / f"norm_{i}.mp4"
            normalize_clip(src, dst, seconds, i)
            norm.append(dst)

        concat_file = work / "concat.txt"
        concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in norm), encoding="utf-8")
        visuals = work / "visuals.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(visuals)])

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

        (OUT / "scene_plan.json").write_text(json.dumps(scene_debug, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "credits.txt").write_text("Visual forniti tramite Pexels e Pixabay.\n\n" + "\n".join(credits), encoding="utf-8")

        write_meta(
            "generate_video",
            "ok",
            {
                "file": video_filename,
                "duration": round(total, 2),
                "scene_count": len(scene_debug),
                "scenes": scene_debug,
                "video_refs": list(used_refs),
                "sources": used_sources,
            },
        )

        print(f"VIDEO PRONTO: {final} ({total:.1f}s)")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
