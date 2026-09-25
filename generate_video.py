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

# V9: meno scene, ma molto più controllate. Meglio 8 clip coerenti
# che 11 clip con 2-3 visual fuori tema.
TARGET_SCENE_SECONDS = 4.0
MIN_SCENES = 7
MAX_SCENES = 9


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


# -----------------------------------------------------------------------------
# V9 — TEMI CHIUSI
# Ogni argomento può usare solo famiglie visuali coerenti.
# -----------------------------------------------------------------------------
THEMES = {
    "crown_waterproof": {
        "allowed_types": {"crown", "case", "caseback", "watchmaker", "wrist", "water"},
        "queries": [
            ("wristwatch crown close up", "crown"),
            ("watchmaker adjusting wristwatch crown", "watchmaker"),
            ("watchmaker inspecting wristwatch close up", "watchmaker"),
            ("wristwatch case back close up", "caseback"),
            ("stainless steel wristwatch case macro", "case"),
            ("diver wristwatch on wrist close up", "wrist"),
            ("wristwatch water splash close up", "water"),
        ],
    },
    "water_resistance": {
        "allowed_types": {"case", "caseback", "crown", "wrist", "water", "diver", "bezel", "watchmaker"},
        "queries": [
            ("wristwatch case back close up", "caseback"),
            ("wristwatch crown close up", "crown"),
            ("stainless steel wristwatch case macro", "case"),
            ("wristwatch on wrist everyday close up", "wrist"),
            ("wristwatch water splash close up", "water"),
            ("diver wristwatch on wrist close up", "diver"),
            ("diver watch bezel macro", "bezel"),
            ("watchmaker inspecting wristwatch close up", "watchmaker"),
        ],
    },
    "jewels": {
        "allowed_types": {"movement", "watchmaker", "caseback"},
        "queries": [
            ("mechanical wristwatch ruby jewel movement macro", "movement"),
            ("watch movement jewel close up", "movement"),
            ("mechanical wristwatch movement gears macro", "movement"),
            ("watchmaker mechanical movement ruby jewel", "watchmaker"),
            ("watchmaker wristwatch movement close up", "watchmaker"),
            ("transparent case back mechanical wristwatch", "caseback"),
        ],
    },
    "quartz_seconds": {
        "allowed_types": {"dial", "movement", "wrist", "watchmaker"},
        "queries": [
            ("wristwatch dial second hand macro", "dial"),
            ("wristwatch second hand close up", "dial"),
            ("mechanical wristwatch dial close up", "dial"),
            ("quartz wristwatch movement battery close up", "movement"),
            ("mechanical wristwatch movement gears macro", "movement"),
            ("mechanical wristwatch on wrist close up", "wrist"),
            ("watchmaker inspecting wristwatch movement", "watchmaker"),
        ],
    },
    "power_reserve": {
        "allowed_types": {"movement", "crown", "wrist", "table", "watchmaker"},
        "queries": [
            ("automatic wristwatch rotor movement macro", "movement"),
            ("mechanical wristwatch movement gears macro", "movement"),
            ("hand winding mechanical wristwatch crown close up", "crown"),
            ("mechanical wristwatch on wrist close up", "wrist"),
            ("mechanical wristwatch on table close up", "table"),
            ("watchmaker automatic movement close up", "watchmaker"),
        ],
    },
    "investment": {
        "allowed_types": {"store", "collection", "wrist", "case"},
        "queries": [
            ("luxury wristwatch store display close up", "store"),
            ("wristwatch shopping display close up", "store"),
            ("luxury mechanical wristwatch showcase", "store"),
            ("wristwatch collection close up", "collection"),
            ("luxury wristwatch on wrist close up", "wrist"),
            ("premium wristwatch macro", "case"),
        ],
    },
    "chronograph": {
        "allowed_types": {"dial", "pushers", "wrist", "case"},
        "queries": [
            ("chronograph wristwatch pushers close up", "pushers"),
            ("chronograph watch dial macro", "dial"),
            ("chronograph on wrist close up", "wrist"),
            ("chronograph wristwatch case close up", "case"),
        ],
    },
    "bracelet": {
        "allowed_types": {"bracelet", "wrist", "case", "watchmaker"},
        "queries": [
            ("wristwatch strap bracelet close up", "bracelet"),
            ("watch bracelet clasp macro", "bracelet"),
            ("wristwatch leather strap close up", "bracelet"),
            ("mechanical wristwatch on wrist close up", "wrist"),
            ("watchmaker changing watch strap close up", "watchmaker"),
        ],
    },
    "maintenance": {
        "allowed_types": {"watchmaker", "movement", "crown", "caseback", "case"},
        "queries": [
            ("watchmaker repairing wristwatch macro", "watchmaker"),
            ("watchmaker hands watch movement close up", "watchmaker"),
            ("mechanical wristwatch movement gears macro", "movement"),
            ("mechanical wristwatch crown macro", "crown"),
            ("wristwatch case back close up", "caseback"),
            ("stainless steel wristwatch case macro", "case"),
        ],
    },
    "generic_watch": {
        "allowed_types": {"dial", "movement", "wrist", "watchmaker", "case"},
        "queries": [
            ("wristwatch dial macro", "dial"),
            ("mechanical wristwatch movement gears macro", "movement"),
            ("mechanical wristwatch on wrist close up", "wrist"),
            ("watchmaker hands wristwatch close up", "watchmaker"),
            ("stainless steel wristwatch macro", "case"),
        ],
    },
}


SCENE_HINTS = [
    (["lancetta", "secondi", "quadrante", "precisione", "preciso", "scatto"], ["dial"]),
    (["quarzo", "batteria", "movimento al quarzo"], ["movement", "dial"]),
    (["movimento meccanico", "meccanico", "ingranaggi", "rotore", "molla", "rubini", "jewels"], ["movement", "watchmaker"]),
    (["corona", "vite", "avvitata", "carica manuale"], ["crown", "watchmaker"]),
    (["guarnizione", "guarnizioni", "cassa", "fondello"], ["caseback", "case", "watchmaker"]),
    (["pioggia", "schizzi", "acqua", "bagn", "doccia"], ["water", "wrist", "case"]),
    (["nuoto", "nuotare", "immersione", "subacqueo", "diver"], ["diver", "wrist", "bezel", "water"]),
    (["polso", "indoss", "indossi"], ["wrist"]),
    (["comodino", "tavolo", "fermo", "si ferma"], ["table", "movement"]),
    (["prezzo", "valore", "mercato", "rivendita", "investimento", "listino"], ["store", "collection", "case"]),
    (["orologiaio", "riparazione", "manutenzione", "assemblaggio"], ["watchmaker", "movement"]),
    (["cinturino", "bracciale", "fibbie", "fibbia"], ["bracelet", "wrist"]),
    (["cronografo", "pulsante", "pulsanti"], ["pushers", "dial", "wrist"]),
]


# Risultati che NON vogliamo mai usare per questa pagina.
BLOCKED_PHRASES = (
    "alarm clock",
    "alarm-clock",
    "wall clock",
    "wall-clock",
    "clock tower",
    "clock-tower",
    "tower clock",
    "tower-clock",
    "grandfather clock",
    "cuckoo clock",
    "hourglass",
    "sand timer",
    "kitchen timer",
    "kitchen clock",
    "digital clock",
    "desk clock",
    "table clock",
    "smartwatch",
    "smart watch",
    "apple watch",
    "fitness tracker",
    "pocket watch",
)

POSITIVE_PATTERNS = (
    r"\bwristwatch\b",
    r"\bwrist\b",
    r"\bwatchmaker\b",
    r"\btimepiece\b",
    r"\bchronograph\b",
    r"\bwatch\b",
)

TYPE_SIGNALS = {
    "dial": ("dial", "second", "hand", "face"),
    "movement": ("movement", "gear", "rotor", "spring", "mechanism", "automatic", "mechanical"),
    "watchmaker": ("watchmaker", "repair", "workshop", "tool"),
    "crown": ("crown", "wind", "winding", "setting"),
    "wrist": ("wrist", "wear", "wearing"),
    "caseback": ("case back", "caseback", "rear", "back"),
    "case": ("case", "steel", "stainless"),
    "water": ("water", "splash", "rain", "wet"),
    "diver": ("diver", "diving", "underwater"),
    "bezel": ("bezel", "diver"),
    "bracelet": ("bracelet", "strap", "clasp", "band"),
    "pushers": ("pusher", "pushers", "chronograph", "button"),
    "table": ("table", "desk", "bedside"),
    "store": ("store", "shop", "display", "showcase"),
    "collection": ("collection", "display", "watches"),
}


_PEXELS_CACHE = {}
_PIXABAY_CACHE = {}


def classify_theme(script: str) -> str:
    s = script.lower()

    has_water = any(k in s for k in [
        "water resistant", "impermeabile", "impermeabilità", "acqua", "pioggia",
        "schizzi", "nuoto", "nuotare", "immersione", "metri"
    ])
    has_crown = any(k in s for k in ["corona", "corona a vite", "vite"])

    if has_water and has_crown:
        return "crown_waterproof"
    if has_water:
        return "water_resistance"
    if any(k in s for k in ["21 jewels", "jewels", "rubini", "rubino"]):
        return "jewels"
    if any(k in s for k in ["quarzo", "lancetta dei secondi", "secondi", "scatto al secondo"]):
        return "quartz_seconds"
    if any(k in s for k in ["riserva di carica", "si ferma", "comodino", "rotore", "molla", "energia accumulata"]):
        return "power_reserve"
    if any(k in s for k in ["investimento", "rivendita", "mercato dell'usato", "mercato usato", "prezzo di listino", "valore"]):
        return "investment"
    if any(k in s for k in ["cronografo", "pulsanti", "pulsante"]):
        return "chronograph"
    if any(k in s for k in ["cinturino", "bracciale", "fibbia", "fibbie"]):
        return "bracelet"
    if any(k in s for k in ["orologiaio", "riparazione", "manutenzione", "assemblaggio"]):
        return "maintenance"

    return "generic_watch"


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
        if len(piece) <= 110:
            result.append(piece)
            continue

        words = piece.split()
        mid = len(words) // 2
        left = " ".join(words[:mid]).strip()
        right = " ".join(words[mid:]).strip()
        if len(left) >= 12 and len(right) >= 12:
            result.extend([left, right])
        else:
            result.append(piece)

    return result


def fit_scene_count(units, target_count):
    units = [u for u in units if u]

    while len(units) < target_count:
        idx = max(range(len(units)), key=lambda i: len(units[i]))
        words = units[idx].split()
        if len(words) < 14:
            break
        mid = len(words) // 2
        left = " ".join(words[:mid]).strip()
        right = " ".join(words[mid:]).strip()
        if len(left) < 10 or len(right) < 10:
            break
        units[idx:idx + 1] = [left, right]

    while len(units) > target_count:
        idx = min(
            range(len(units) - 1),
            key=lambda i: len(units[i]) + len(units[i + 1])
        )
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


def preferred_types_for_sentence(sentence: str):
    s = sentence.lower()
    out = []

    for keywords, types in SCENE_HINTS:
        if any(k in s for k in keywords):
            for t in types:
                if t not in out:
                    out.append(t)

    return out


def ordered_theme_entries(theme_name: str, sentence: str, scene_index: int, used_queries: list[str], used_types: list[str]):
    theme = THEMES[theme_name]
    allowed = theme["allowed_types"]
    entries = [e for e in theme["queries"] if e[1] in allowed]

    hints = preferred_types_for_sentence(sentence)
    recent_queries = set(used_queries[-3:])
    recent_types = set(used_types[-2:])

    def key(entry):
        query, query_type = entry
        relevance = hints.index(query_type) if query_type in hints else 99
        repeat_penalty = 0
        if query in recent_queries:
            repeat_penalty += 5
        if query_type in recent_types:
            repeat_penalty += 2
        if query in used_queries:
            repeat_penalty += 1
        tie = stable_number(f"{SAFE_REQUEST_ID}:{scene_index}:{query}") % 1000
        return (relevance, repeat_penalty, tie)

    return sorted(entries, key=key)


def _query_words(query: str):
    return [
        w for w in re.findall(r"[a-z]+", query.lower())
        if len(w) >= 4 and w not in {
            "close", "macro", "mechanical", "automatic", "wristwatch", "watch",
            "luxury", "steel", "person", "stainless"
        }
    ]


def metadata_text(candidate):
    return f"{candidate.get('tags') or ''} {candidate.get('page_url') or ''}".lower()


def blocked_candidate(candidate) -> bool:
    haystack = metadata_text(candidate)
    return any(phrase in haystack for phrase in BLOCKED_PHRASES)


def has_watch_signal(candidate) -> bool:
    haystack = metadata_text(candidate)
    return any(re.search(pattern, haystack) for pattern in POSITIVE_PATTERNS)


def candidate_score(candidate, query: str, query_type: str, allow_reuse: bool):
    if blocked_candidate(candidate):
        return None

    if not has_watch_signal(candidate):
        return None

    score = 100
    rank = int(candidate.get("rank", 0))
    score += max(0, 28 - rank * 2)

    w = candidate.get("width") or 1
    h = candidate.get("height") or 1
    if h > w:
        score += 10

    haystack = metadata_text(candidate)

    # Parole della query: forte segnale di pertinenza.
    query_matches = 0
    for word in _query_words(query):
        if word in haystack:
            score += 12
            query_matches += 1

    # Tipo visuale atteso.
    type_matches = 0
    for word in TYPE_SIGNALS.get(query_type, ()):
        if word in haystack:
            score += 14
            type_matches += 1

    # Se i metadati sono generici, la clip resta utilizzabile ma non supera
    # una clip che conferma davvero il tipo richiesto.
    if query_matches == 0 and type_matches == 0:
        score -= 18

    if allow_reuse:
        score -= 22

    return score


def search_pexels(query: str, scene_index: int):
    if not PEXELS_API_KEY:
        return []

    cache_key = (query, scene_index % 2)
    if cache_key in _PEXELS_CACHE:
        return _PEXELS_CACHE[cache_key]

    first_page = 1 if scene_index % 2 == 0 else 2
    pages = [first_page, 2 if first_page == 1 else 1]

    all_results = []

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
                print(f"Pexels {r.status_code}: {r.text[:200]}")
                continue

            for rank, video in enumerate(r.json().get("videos", [])[:20]):
                media = choose_pexels_mp4(video)
                if not media:
                    continue

                all_results.append({
                    "source": "pexels",
                    "id": str(video.get("id")),
                    "ref": f"pexels:{video.get('id')}",
                    "media_url": media.get("link"),
                    "page_url": video.get("url") or "https://www.pexels.com",
                    "creator": (video.get("user") or {}).get("name", "Pexels creator"),
                    "width": video.get("width") or media.get("width") or 1,
                    "height": video.get("height") or media.get("height") or 1,
                    "tags": str(video.get("url") or ""),
                    "rank": rank + (page - 1) * 20,
                })

    # Dedupe ref mantenendo il primo risultato.
    deduped = []
    seen = set()
    for item in all_results:
        if item["ref"] in seen:
            continue
        seen.add(item["ref"])
        deduped.append(item)

    _PEXELS_CACHE[cache_key] = deduped
    return deduped


def search_pixabay(query: str, scene_index: int):
    if not PIXABAY_API_KEY:
        return []

    cache_key = (query, scene_index % 2)
    if cache_key in _PIXABAY_CACHE:
        return _PIXABAY_CACHE[cache_key]

    preferred_page = 1 if scene_index % 2 == 0 else 2
    pages = [preferred_page, 2 if preferred_page == 1 else 1]
    all_results = []

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

        for rank, hit in enumerate(r.json().get("hits", [])[:24]):
            media = choose_pixabay_mp4(hit)
            if not media:
                continue

            all_results.append({
                "source": "pixabay",
                "id": str(hit.get("id")),
                "ref": f"pixabay:{hit.get('id')}",
                "media_url": media.get("url"),
                "page_url": hit.get("pageURL") or "https://pixabay.com/videos/",
                "creator": hit.get("user") or "Pixabay creator",
                "width": media.get("width") or 1,
                "height": media.get("height") or 1,
                "tags": hit.get("tags") or "",
                "rank": rank + (page - 1) * 24,
            })

    deduped = []
    seen = set()
    for item in all_results:
        if item["ref"] in seen:
            continue
        seen.add(item["ref"])
        deduped.append(item)

    _PIXABAY_CACHE[cache_key] = deduped
    return deduped


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


def best_candidate_for_query(query: str, query_type: str, scene_index: int, used_refs: set, used_sources: list[str], allow_reuse=False):
    candidates = search_pexels(query, scene_index) + search_pixabay(query, scene_index)
    previous_source = used_sources[-1] if used_sources else None
    scored = []

    for candidate in candidates:
        already_used = candidate.get("ref") in used_refs
        if already_used and not allow_reuse:
            continue

        score = candidate_score(candidate, query, query_type, allow_reuse=already_used)
        if score is None:
            continue

        # Fonte diversa è solo un piccolo bonus. Non può battere la pertinenza.
        if previous_source and candidate.get("source") != previous_source:
            score += 3

        scored.append((score, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1].get("rank", 0)))

    # Scegli tra le prime clip solo se il punteggio è quasi identico.
    best_score = scored[0][0]
    shortlist = [c for s, c in scored[:3] if best_score - s <= 5]
    idx = stable_number(f"{SAFE_REQUEST_ID}:{scene_index}:{query}:v9") % len(shortlist)
    return shortlist[idx]


def choose_scene_candidate(theme_name: str, sentence: str, scene_index: int, used_refs: set, used_sources: list[str], used_queries: list[str], used_types: list[str]):
    entries = ordered_theme_entries(
        theme_name,
        sentence,
        scene_index,
        used_queries,
        used_types,
    )

    # Primo passaggio: solo clip nuove e pertinenti.
    for query, query_type in entries:
        chosen = best_candidate_for_query(
            query,
            query_type,
            scene_index,
            used_refs,
            used_sources,
            allow_reuse=False,
        )
        if chosen:
            return chosen, query, query_type, False

    # Secondo passaggio: meglio RIUSARE una clip pertinente che inserire
    # una sveglia o un visual fuori tema.
    for query, query_type in entries:
        chosen = best_candidate_for_query(
            query,
            query_type,
            scene_index,
            used_refs,
            used_sources,
            allow_reuse=True,
        )
        if chosen:
            return chosen, query, query_type, True

    return None, None, None, False


def download(url: str, dest: Path):
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)


def normalize_clip(src: Path, dst: Path, seconds: float, scene_index: int, reused=False):
    # Crop leggermente diverso in caso di clip riutilizzata.
    if reused:
        scale_w = 1180 if scene_index % 2 == 0 else 1220
        scale_h = 2098 if scene_index % 2 == 0 else 2169
    else:
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
        encoding="utf-8",
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

        theme_name = classify_theme(SCRIPT)
        scene_units = build_scene_plan(SCRIPT, total)
        durations = scene_durations(scene_units, total)
        manual_queries = [q.strip() for q in VISUAL_QUERIES.split(",") if q.strip()]

        print(f"Tema V9: {theme_name}")
        print(f"Scene automatiche: {len(scene_units)}")
        print("2/5 Cerco visual controllati su Pexels + Pixabay...")

        raw_clips = []
        reused_flags = []
        credits = []
        used_refs = set()
        used_sources = []
        used_queries = []
        used_types = []
        scene_debug = []

        for scene_index, unit in enumerate(scene_units):
            if manual_queries:
                query = manual_queries[scene_index % len(manual_queries)]
                query_type = "manual"

                chosen = best_candidate_for_query(
                    query,
                    query_type,
                    scene_index,
                    used_refs,
                    used_sources,
                    allow_reuse=False,
                )
                reused = False

                if not chosen:
                    chosen = best_candidate_for_query(
                        query,
                        query_type,
                        scene_index,
                        used_refs,
                        used_sources,
                        allow_reuse=True,
                    )
                    reused = bool(chosen)
            else:
                chosen, query, query_type, reused = choose_scene_candidate(
                    theme_name,
                    unit,
                    scene_index,
                    used_refs,
                    used_sources,
                    used_queries,
                    used_types,
                )

            if not chosen:
                raise RuntimeError(
                    f"Nessun visual affidabile per la scena {scene_index + 1}. "
                    "V9 preferisce fermarsi piuttosto che inserire una clip fuori tema."
                )

            dest = work / f"raw_{scene_index}.mp4"
            download(chosen["media_url"], dest)

            raw_clips.append(dest)
            reused_flags.append(reused)
            used_refs.add(chosen["ref"])
            used_sources.append(chosen["source"])
            used_queries.append(query)
            used_types.append(query_type)

            credits.append(
                f"Scena {scene_index + 1} | tema={theme_name} | fonte={chosen['source']} | "
                f"{query} | tipo={query_type} | reused={reused} | "
                f"id={chosen['id']} | {chosen['creator']} | {chosen['page_url']}"
            )

            scene_debug.append({
                "scene": scene_index + 1,
                "text": unit,
                "theme": theme_name,
                "query": query,
                "query_type": query_type,
                "source": chosen["source"],
                "video_id": chosen["id"],
                "reused": reused,
                "seconds": round(durations[scene_index], 2),
            })

            print(
                f"Scena {scene_index + 1}: tema='{theme_name}' fonte='{chosen['source']}' "
                f"tipo='{query_type}' query='{query}' id={chosen['id']} reused={reused}"
            )

        print("3/5 Adatto le clip al formato TikTok...")
        norm = []

        for i, (src, seconds, reused) in enumerate(zip(raw_clips, durations, reused_flags)):
            dst = work / f"norm_{i}.mp4"
            normalize_clip(src, dst, seconds, i, reused=reused)
            norm.append(dst)

        concat_file = work / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in norm),
            encoding="utf-8",
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
            encoding="utf-8",
        )

        (OUT / "credits.txt").write_text(
            "Visual forniti tramite Pexels e Pixabay.\n\n" + "\n".join(credits),
            encoding="utf-8",
        )

        write_meta(
            "generate_video",
            "ok",
            {
                "file": video_filename,
                "duration": round(total, 2),
                "theme": theme_name,
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
