import asyncio
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import edge_tts
import requests
from PIL import Image

try:
    import torch
    from transformers import CLIPModel, CLIPProcessor
except Exception:
    torch = None
    CLIPModel = None
    CLIPProcessor = None


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
PEXELS_SEARCH = "https://api.pexels.com/v1/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
CLIP_MODEL_NAME = os.environ.get("CLIP_MODEL", "openai/clip-vit-base-patch32")

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

# 25-35 sec -> normalmente 5-7 scene. Niente scene duplicate create solo per riempire.
TARGET_SCENE_SECONDS = 4.4
MIN_SCENES = 5
MAX_SCENES = 7

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "orologi-video-generator/10"})


# -----------------------------------------------------------------------------
# SHELL / AUDIO
# -----------------------------------------------------------------------------
def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-6000:] or f"Comando fallito: {' '.join(cmd)}")
    return proc.stdout


def duration(path: Path) -> float:
    out = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ])
    return max(0.1, float(out.strip()))


async def _synth_once(text: str, out_mp3: Path):
    comm = edge_tts.Communicate(text, GIUSEPPE, rate=RATE, boundary="WordBoundary")
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


def synthesize(text: str, out_mp3: Path):
    last_error = None
    for attempt in range(1, 4):
        try:
            if out_mp3.exists():
                out_mp3.unlink()
            boundaries = asyncio.run(
                asyncio.wait_for(_synth_once(text, out_mp3), timeout=75)
            )
            if not out_mp3.exists() or out_mp3.stat().st_size < 1000:
                raise RuntimeError("TTS ha restituito un file vuoto")
            return boundaries
        except Exception as exc:
            last_error = exc
            print(f"TTS tentativo {attempt}/3 fallito: {exc}")
    raise RuntimeError(f"TTS non riuscito: {last_error}")


def stable_number(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# -----------------------------------------------------------------------------
# SEMANTIC PLANNER
# -----------------------------------------------------------------------------
# Ogni tipo visuale ha query molto strette. Evitiamo "watch" generico: quasi
# tutte le query contengono wristwatch/watchmaker/mechanical wristwatch.
TYPE_QUERIES = {
    "generic": [
        "real wristwatch close up",
        "mechanical wristwatch on wrist close up",
    ],
    "dial": [
        "wristwatch dial hands close up",
        "mechanical wristwatch dial macro",
    ],
    "seconds": [
        "wristwatch second hand dial close up",
        "mechanical wristwatch seconds hand macro",
    ],
    "date_window": [
        "wristwatch date window close up",
        "mechanical wristwatch date display macro",
    ],
    "movement": [
        "mechanical wristwatch movement gears macro",
        "automatic wristwatch movement close up",
    ],
    "quartz_movement": [
        "quartz wristwatch movement battery close up",
        "quartz wristwatch mechanism close up",
    ],
    "crown": [
        "wristwatch crown close up",
        "hand adjusting wristwatch crown close up",
    ],
    "caseback": [
        "wristwatch case back close up",
        "mechanical wristwatch transparent caseback close up",
    ],
    "case": [
        "stainless steel wristwatch case close up",
        "mechanical wristwatch case macro",
    ],
    "wrist": [
        "mechanical wristwatch on wrist close up",
        "person wearing wristwatch close up",
    ],
    "watchmaker": [
        "watchmaker repairing wristwatch close up",
        "watchmaker working on mechanical wristwatch",
    ],
    "water": [
        "wristwatch water splash close up",
        "wristwatch wet water droplets close up",
    ],
    "diver": [
        "diver wristwatch on wrist close up",
        "diving wristwatch underwater close up",
    ],
    "bezel": [
        "diver wristwatch bezel close up",
        "wristwatch rotating bezel macro",
    ],
    "rotor": [
        "automatic wristwatch rotor movement close up",
        "mechanical wristwatch rotor macro",
    ],
    "strap": [
        "wristwatch bracelet clasp close up",
        "wristwatch leather strap close up",
    ],
    "chronograph": [
        "chronograph wristwatch dial close up",
        "chronograph wristwatch pushers close up",
    ],
    "store": [
        "luxury wristwatch store display close up",
        "wristwatch showcase store close up",
    ],
    "collection": [
        "wristwatch collection close up",
        "mechanical wristwatch collection display",
    ],
}

TYPE_PROMPTS = {
    "generic": "a close-up photo of a real wristwatch",
    "dial": "a close-up photo of the dial and hands of a wristwatch",
    "seconds": "a close-up photo of the seconds hand on a wristwatch dial",
    "date_window": "a close-up photo of the date window on a wristwatch dial",
    "movement": "a macro photo of a mechanical wristwatch movement with gears",
    "quartz_movement": "a close-up photo of a quartz wristwatch movement and battery",
    "crown": "a close-up photo of the crown on the side of a wristwatch",
    "caseback": "a close-up photo of the back case of a wristwatch",
    "case": "a close-up photo of the metal case of a wristwatch",
    "wrist": "a photo of a wristwatch worn on a person's wrist",
    "watchmaker": "a watchmaker repairing a wristwatch",
    "water": "a wristwatch with water droplets or splashes on it",
    "diver": "a diving wristwatch on a wrist or underwater",
    "bezel": "a close-up photo of the bezel of a diving wristwatch",
    "rotor": "a close-up photo of the automatic rotor inside a wristwatch movement",
    "strap": "a close-up photo of a wristwatch strap bracelet or clasp",
    "chronograph": "a close-up photo of a chronograph wristwatch dial and pushers",
    "store": "wristwatches displayed inside a watch store",
    "collection": "a collection of wristwatches",
}

NEGATIVE_PROMPTS = [
    "an alarm clock",
    "a wall clock",
    "a clock tower",
    "a digital desk clock",
    "a smartwatch screen",
    "a pocket watch",
    "an hourglass",
    "a kitchen timer",
    "a phone screen",
]

THEMES = {
    "date_setting": {
        "types": ["date_window", "crown", "dial", "movement", "watchmaker"],
        "fallback": ["date_window", "crown", "dial"],
    },
    "crown_waterproof": {
        "types": ["crown", "caseback", "case", "water", "wrist", "diver", "watchmaker"],
        "fallback": ["crown", "caseback", "water", "wrist"],
    },
    "water_resistance": {
        "types": ["caseback", "case", "crown", "water", "wrist", "diver", "bezel"],
        "fallback": ["caseback", "water", "wrist", "diver"],
    },
    "jewels": {
        "types": ["movement", "watchmaker", "caseback", "rotor"],
        "fallback": ["movement", "watchmaker"],
    },
    "accuracy": {
        "types": ["seconds", "dial", "movement", "wrist", "watchmaker"],
        "fallback": ["seconds", "dial", "movement"],
    },
    "quartz_vs_mechanical": {
        "types": ["seconds", "dial", "quartz_movement", "movement", "wrist"],
        "fallback": ["seconds", "dial", "movement"],
    },
    "power_reserve": {
        "types": ["rotor", "movement", "crown", "wrist", "watchmaker"],
        "fallback": ["rotor", "movement", "crown"],
    },
    "investment": {
        "types": ["store", "collection", "wrist", "case"],
        "fallback": ["store", "collection", "wrist"],
    },
    "chronograph": {
        "types": ["chronograph", "dial", "wrist", "case"],
        "fallback": ["chronograph", "dial"],
    },
    "strap": {
        "types": ["strap", "wrist", "case", "watchmaker"],
        "fallback": ["strap", "wrist"],
    },
    "maintenance": {
        "types": ["watchmaker", "movement", "caseback", "crown", "case"],
        "fallback": ["watchmaker", "movement"],
    },
    "generic": {
        "types": ["dial", "movement", "wrist", "watchmaker", "case"],
        "fallback": ["dial", "movement", "wrist"],
    },
}

SCENE_RULES = [
    (r"\b(data|datario|giorno|mezzanotte|ore notturne)\b", ["date_window", "dial"]),
    (r"\b(corona|vite|avvit|ricaric|carica manuale)\w*", ["crown", "watchmaker"]),
    (r"\b(second|lancett|precision|anticip|ritard|scatt)\w*", ["seconds", "dial"]),
    (r"\b(quarzo|batteria)\w*", ["quartz_movement", "seconds", "dial"]),
    (r"\b(movimento|ingranagg|molla|bilancier|meccanismo|rubin|jewel)\w*", ["movement", "watchmaker"]),
    (r"\b(rotore|massa oscillante|riserva di carica|energia accumulata)\b", ["rotor", "movement"]),
    (r"\b(guarnizion|fondello|cassa)\w*", ["caseback", "case", "watchmaker"]),
    (r"\b(acqua|pioggia|schizz|bagn|doccia|water resistant)\w*", ["water", "wrist", "case"]),
    (r"\b(nuot|immersion|subacque|diver)\w*", ["diver", "water", "bezel", "wrist"]),
    (r"\b(polso|indoss)\w*", ["wrist"]),
    (r"\b(orologiaio|ripar|manutenz|assembl)\w*", ["watchmaker", "movement"]),
    (r"\b(cinturino|bracciale|fibbia|fibbie)\b", ["strap", "wrist"]),
    (r"\b(cronografo|pulsant)\w*", ["chronograph", "dial"]),
    (r"\b(prezzo|valore|rivendita|mercato|investimento|listino)\w*", ["store", "collection", "wrist"]),
]

BLOCKED_PHRASES = (
    "alarm clock", "alarm-clock", "wall clock", "wall-clock", "clock tower",
    "clock-tower", "tower clock", "grandfather clock", "cuckoo clock",
    "hourglass", "sand timer", "kitchen timer", "desk clock", "table clock",
    "digital clock", "smartwatch", "smart watch", "apple watch", "fitness tracker",
    "pocket watch",
)

WATCH_TERMS = ("wristwatch", "wrist watch", "watchmaker", "mechanical watch", "watch bracelet", "watch dial", "watch movement", "watch crown")


def classify_theme(script: str) -> str:
    s = script.lower()
    has = lambda *terms: any(t in s for t in terms)

    if has("data", "datario", "mezzanotte") and has("corona", "movimento", "orologio"):
        return "date_setting"
    if has("water resistant", "impermeab", "acqua", "nuoto", "immersion", "metri") and has("corona"):
        return "crown_waterproof"
    if has("water resistant", "impermeab", "acqua", "nuoto", "immersion", "metri"):
        return "water_resistance"
    if has("21 jewels", "jewels", "rubini", "rubino"):
        return "jewels"
    if has("quarzo") and has("second", "lancetta", "meccanico", "automatico"):
        return "quartz_vs_mechanical"
    if has("anticipa", "ritarda", "precisione", "secondi al giorno", "magnetismo"):
        return "accuracy"
    if has("riserva di carica", "si ferma", "rotore", "massa oscillante", "energia accumulata"):
        return "power_reserve"
    if has("investimento", "rivendita", "mercato dell'usato", "mercato usato", "prezzo di listino", "valore"):
        return "investment"
    if has("cronografo", "pulsanti", "pulsante"):
        return "chronograph"
    if has("cinturino", "bracciale", "fibbia", "fibbie"):
        return "strap"
    if has("orologiaio", "riparazione", "manutenzione", "assemblaggio"):
        return "maintenance"
    return "generic"


def split_semantic_units(script: str):
    script = clean_text(script)
    if not script:
        return []

    raw = re.split(
        r"(?<=[.!?])\s+|;\s*|:\s*|,\s+(?=(?:ma|perché|quindi|quando|mentre|invece|oppure|però)\b)",
        script,
        flags=re.I,
    )
    units = [clean_text(x) for x in raw if len(clean_text(x)) >= 10]

    # Spezza solo frasi molto lunghe; non crea duplicati artificiali.
    out = []
    for unit in units:
        words = unit.split()
        if len(words) <= 24:
            out.append(unit)
            continue
        mid = len(words) // 2
        out.extend([" ".join(words[:mid]), " ".join(words[mid:])])
    return out or [script]


def fit_scene_count(units, target_count):
    units = list(units)

    while len(units) > target_count and len(units) > 1:
        idx = min(range(len(units) - 1), key=lambda i: len(units[i]) + len(units[i + 1]))
        units[idx:idx + 2] = [clean_text(units[idx] + " " + units[idx + 1])]

    while len(units) < target_count:
        candidates = [(i, len(u.split())) for i, u in enumerate(units) if len(u.split()) >= 16]
        if not candidates:
            break
        idx = max(candidates, key=lambda x: x[1])[0]
        words = units[idx].split()
        mid = len(words) // 2
        left, right = " ".join(words[:mid]), " ".join(words[mid:])
        units[idx:idx + 1] = [left, right]

    return units


def build_scene_plan(script: str, total_seconds: float):
    target = round(total_seconds / TARGET_SCENE_SECONDS)
    target = max(MIN_SCENES, min(MAX_SCENES, target))
    return fit_scene_count(split_semantic_units(script), target)


def scene_durations(scene_units, total_seconds: float):
    weights = [max(1, len(re.findall(r"\w+", u))) for u in scene_units]
    total_weight = sum(weights)
    durations = [total_seconds * w / total_weight for w in weights]

    # Evita clip troppo lampo; riequilibra senza cambiare il totale.
    minimum = 2.7
    for _ in range(3):
        short = [i for i, d in enumerate(durations) if d < minimum]
        if not short:
            break
        deficit = sum(minimum - durations[i] for i in short)
        for i in short:
            durations[i] = minimum
        donors = [i for i, d in enumerate(durations) if d > minimum + 0.5 and i not in short]
        donor_space = sum(durations[i] - minimum for i in donors)
        if donor_space <= 0:
            break
        for i in donors:
            take = deficit * ((durations[i] - minimum) / donor_space)
            durations[i] -= take

    scale = total_seconds / sum(durations)
    durations = [d * scale for d in durations]
    durations[-1] += total_seconds - sum(durations)
    return durations


def types_for_scene(theme: str, sentence: str, used_types: list[str]):
    allowed = THEMES[theme]["types"]
    found = []
    s = sentence.lower()

    for pattern, types in SCENE_RULES:
        if re.search(pattern, s, flags=re.I):
            for t in types:
                if t in allowed and t not in found:
                    found.append(t)

    # Se la frase è generica, usa tipi del tema e bilancia davvero la varietà.
    counts = Counter(used_types)
    for t in sorted(allowed, key=lambda x: (counts[x], x in used_types[-2:], stable_number(f"{SAFE_REQUEST_ID}:{sentence}:{x}") % 997)):
        if t not in found:
            found.append(t)

    return found[:4]


# -----------------------------------------------------------------------------
# STOCK SOURCES
# -----------------------------------------------------------------------------
@dataclass
class Candidate:
    source: str
    id: str
    ref: str
    media_url: str
    thumb_url: str
    page_url: str
    creator: str
    width: int
    height: int
    tags: str
    rank: int
    query: str


_PEXELS_CACHE = {}
_PIXABAY_CACHE = {}


def choose_pexels_mp4(video):
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
    if not files:
        return None

    def score(f):
        w, h = f.get("width") or 1, f.get("height") or 1
        portrait_bonus = 10 if h >= w else 0
        target_pixels = min(w * h, 1080 * 1920) / (1080 * 1920)
        return portrait_bonus + target_pixels

    return max(files, key=score)


def choose_pixabay_mp4(hit):
    videos = hit.get("videos") or {}
    items = []
    for name in ("medium", "large", "small", "tiny"):
        v = videos.get(name) or {}
        if v.get("url"):
            items.append(v)
    if not items:
        return None

    def score(v):
        w, h = v.get("width") or 1, v.get("height") or 1
        return (8 if h >= w else 0) + min(w * h, 1080 * 1920) / (1080 * 1920)

    return max(items, key=score)


def blocked_text(text: str) -> bool:
    t = (text or "").lower()
    return any(x in t for x in BLOCKED_PHRASES)


def search_pexels(query: str):
    if not PEXELS_API_KEY:
        return []
    if query in _PEXELS_CACHE:
        return _PEXELS_CACHE[query]

    results = []
    seen = set()

    # Prima portrait, poi senza vincolo. Solo prima pagina: pertinenza > quantità.
    variants = [
        {"query": query, "orientation": "portrait", "size": "medium", "per_page": 14, "page": 1, "locale": "en-US"},
        {"query": query, "size": "medium", "per_page": 14, "page": 1, "locale": "en-US"},
    ]

    for params in variants:
        try:
            r = HTTP.get(PEXELS_SEARCH, headers={"Authorization": PEXELS_API_KEY}, params=params, timeout=25)
            if not r.ok:
                print(f"Pexels {r.status_code}: {r.text[:160]}")
                continue
            for rank, video in enumerate(r.json().get("videos", [])[:12]):
                vid = str(video.get("id"))
                if not vid or vid in seen:
                    continue
                media = choose_pexels_mp4(video)
                thumb = video.get("image")
                if not thumb and video.get("video_pictures"):
                    thumb = video["video_pictures"][0].get("picture")
                if not media or not thumb:
                    continue
                page_url = video.get("url") or "https://www.pexels.com"
                if blocked_text(page_url):
                    continue
                seen.add(vid)
                results.append(Candidate(
                    source="pexels",
                    id=vid,
                    ref=f"pexels:{vid}",
                    media_url=media["link"],
                    thumb_url=thumb,
                    page_url=page_url,
                    creator=(video.get("user") or {}).get("name", "Pexels creator"),
                    width=int(video.get("width") or media.get("width") or 1),
                    height=int(video.get("height") or media.get("height") or 1),
                    tags="",
                    rank=rank,
                    query=query,
                ))
        except Exception as exc:
            print(f"Errore Pexels: {exc}")

    _PEXELS_CACHE[query] = results
    return results


def search_pixabay(query: str):
    if not PIXABAY_API_KEY:
        return []
    if query in _PIXABAY_CACHE:
        return _PIXABAY_CACHE[query]

    params = {
        "key": PIXABAY_API_KEY,
        "q": query[:100],
        "lang": "en",
        "video_type": "film",
        "safesearch": "true",
        "order": "popular",
        "page": 1,
        "per_page": 24,
    }

    results = []
    try:
        r = HTTP.get(PIXABAY_SEARCH, params=params, timeout=25)
        if not r.ok:
            print(f"Pixabay {r.status_code}: {r.text[:160]}")
            _PIXABAY_CACHE[query] = []
            return []

        for rank, hit in enumerate(r.json().get("hits", [])[:18]):
            media = choose_pixabay_mp4(hit)
            if not media:
                continue
            tags = str(hit.get("tags") or "")
            page_url = hit.get("pageURL") or "https://pixabay.com/videos/"
            combined = f"{tags} {page_url}".lower()
            if blocked_text(combined):
                continue

            # Pixabay ha tag veri: se parla chiaramente di clock ma non di watch,
            # scartiamo prima ancora del controllo visivo.
            if "clock" in combined and not any(t in combined for t in ("watch", "wrist", "timepiece")):
                continue

            thumb = media.get("thumbnail")
            if not thumb:
                continue

            vid = str(hit.get("id"))
            results.append(Candidate(
                source="pixabay",
                id=vid,
                ref=f"pixabay:{vid}",
                media_url=media["url"],
                thumb_url=thumb,
                page_url=page_url,
                creator=hit.get("user") or "Pixabay creator",
                width=int(media.get("width") or 1),
                height=int(media.get("height") or 1),
                tags=tags,
                rank=rank,
                query=query,
            ))
    except Exception as exc:
        print(f"Errore Pixabay: {exc}")

    _PIXABAY_CACHE[query] = results
    return results


# -----------------------------------------------------------------------------
# VISUAL VALIDATOR (CLIP)
# -----------------------------------------------------------------------------
class VisualValidator:
    def __init__(self):
        self.available = False
        self.model = None
        self.processor = None
        self.text_cache = {}
        self.image_cache = {}
        self.image_obj_cache = {}

        if not torch or not CLIPModel or not CLIPProcessor:
            print("CLIP non disponibile: userò fallback metadata molto conservativo")
            return

        try:
            print(f"Carico controllo visuale AI: {CLIP_MODEL_NAME}")
            self.model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
            self.processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
            self.model.eval()
            self.available = True
            print("Controllo visuale AI pronto")
        except Exception as exc:
            print(f"CLIP non caricabile ({exc}). Fallback conservativo attivo")

    def _text_embeddings(self, prompts):
        key = tuple(prompts)
        if key in self.text_cache:
            return self.text_cache[key]

        inputs = self.processor(text=list(prompts), return_tensors="pt", padding=True, truncation=True)
        with torch.no_grad():
            feats = self.model.get_text_features(
                input_ids=inputs["input_ids"],
                attention_mask=inputs.get("attention_mask"),
            )
        feats = feats / feats.norm(dim=-1, keepdim=True)
        self.text_cache[key] = feats.cpu()
        return self.text_cache[key]

    def _download_image(self, candidate: Candidate):
        if candidate.ref in self.image_obj_cache:
            return self.image_obj_cache[candidate.ref]
        try:
            r = HTTP.get(candidate.thumb_url, timeout=20)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            self.image_obj_cache[candidate.ref] = img
            return img
        except Exception as exc:
            print(f"Thumbnail fallita {candidate.ref}: {exc}")
            return None

    def embeddings_for(self, candidates):
        out = {}
        if not self.available:
            return out

        missing = [c for c in candidates if c.ref not in self.image_cache]
        valid = []
        images = []
        for c in missing:
            img = self._download_image(c)
            if img is not None:
                valid.append(c)
                images.append(img)

        if images:
            inputs = self.processor(images=images, return_tensors="pt")
            with torch.no_grad():
                feats = self.model.get_image_features(pixel_values=inputs["pixel_values"])
            feats = feats / feats.norm(dim=-1, keepdim=True)
            for c, feat in zip(valid, feats.cpu()):
                self.image_cache[c.ref] = feat

        for c in candidates:
            if c.ref in self.image_cache:
                out[c.ref] = self.image_cache[c.ref]
        return out

    def metadata_fallback_score(self, c: Candidate, visual_type: str):
        hay = f"{c.tags} {c.page_url}".lower()
        if blocked_text(hay):
            return None

        # Pixabay: usa i tag veri. Pexels non fornisce title/tags via API, quindi
        # senza CLIP accettiamo solo URL che sembrano esplicitamente da polso.
        if c.source == "pixabay":
            if not any(x in hay for x in ("watch", "wrist", "timepiece", "watchmaker")):
                return None
        else:
            if not any(x in hay for x in ("watch", "wrist", "watchmaker", "timepiece")):
                return None

        score = 40.0
        words = [w for w in re.findall(r"[a-z]+", " ".join(TYPE_QUERIES.get(visual_type, []))) if len(w) > 4]
        score += sum(2.5 for w in set(words) if w in hay)
        if c.height > c.width:
            score += 3
        score -= c.rank * 0.3
        return score

    def rank(self, candidates, visual_type: str, used_embeddings, used_refs):
        # Dedupe + filtri base.
        unique = []
        seen = set()
        for c in candidates:
            if c.ref in seen or c.ref in used_refs:
                continue
            seen.add(c.ref)
            if blocked_text(f"{c.tags} {c.page_url}"):
                continue
            unique.append(c)

        if not unique:
            return []

        if not self.available:
            ranked = []
            for c in unique:
                score = self.metadata_fallback_score(c, visual_type)
                if score is not None:
                    ranked.append((score, c, None, {"mode": "metadata"}))
            return sorted(ranked, key=lambda x: -x[0])

        emb_map = self.embeddings_for(unique)
        prompts = [
            TYPE_PROMPTS.get(visual_type, "a close-up photo of a wristwatch"),
            "a close-up photo of a real wristwatch",
            *NEGATIVE_PROMPTS,
        ]
        text_embs = self._text_embeddings(prompts)

        ranked = []
        for c in unique:
            emb = emb_map.get(c.ref)
            if emb is None:
                continue

            sims = torch.matmul(text_embs, emb).numpy().tolist()
            specific = float(sims[0])
            generic_watch = float(sims[1])
            best_positive = max(specific, generic_watch)
            best_negative = max(float(x) for x in sims[2:])
            margin = best_positive - best_negative

            hay = f"{c.tags} {c.page_url}".lower()
            metadata_watch = any(x in hay for x in ("watch", "wrist", "timepiece", "watchmaker"))

            # Regola centrale V10: una clip che CLIP vede più simile a sveglia,
            # wall clock, smartwatch ecc. non entra. Pixabay con tag chiaramente
            # da orologio ha una piccola tolleranza, Pexels no.
            tolerance = -0.012 if (c.source == "pixabay" and metadata_watch) else -0.002
            if margin < tolerance:
                continue

            score = margin * 120.0 + specific * 18.0 + generic_watch * 8.0
            if c.height > c.width:
                score += 2.5
            score += max(0, 3.0 - c.rank * 0.25)
            if c.source == "pixabay" and metadata_watch:
                score += 2.0

            max_similarity = 0.0
            if used_embeddings:
                max_similarity = max(float(torch.dot(emb, old).item()) for old in used_embeddings)
                if max_similarity > 0.965:
                    score -= 18.0
                elif max_similarity > 0.92:
                    score -= 8.0
                elif max_similarity > 0.86:
                    score -= 3.0

            audit = {
                "specific_similarity": round(specific, 4),
                "watch_similarity": round(generic_watch, 4),
                "negative_similarity": round(best_negative, 4),
                "margin": round(margin, 4),
                "similarity_to_previous": round(max_similarity, 4),
                "mode": "clip",
            }
            ranked.append((score, c, emb, audit))

        ranked.sort(key=lambda x: -x[0])
        return ranked


# -----------------------------------------------------------------------------
# CANDIDATE SEARCH + SELECTION
# -----------------------------------------------------------------------------
def candidates_for_type(visual_type: str, manual_query: Optional[str] = None):
    queries = [manual_query] if manual_query else TYPE_QUERIES.get(visual_type, TYPE_QUERIES["dial"])
    candidates = []
    for q in queries[:2]:
        candidates.extend(search_pixabay(q))
        candidates.extend(search_pexels(q))
    return candidates


def choose_for_scene(
    validator: VisualValidator,
    theme: str,
    sentence: str,
    scene_index: int,
    used_types: list[str],
    used_refs: set,
    used_embeddings: list,
    selected_history: list,
    manual_query: Optional[str] = None,
):
    if manual_query:
        visual_types = ["generic"]
    else:
        visual_types = types_for_scene(theme, sentence, used_types)

    attempts = []
    for visual_type in visual_types:
        cands = candidates_for_type(visual_type, manual_query=manual_query)
        ranked = validator.rank(cands, visual_type, used_embeddings, used_refs)
        attempts.append({"type": visual_type, "candidates": len(cands), "valid": len(ranked)})
        if ranked:
            score, candidate, emb, audit = ranked[0]
            return candidate, visual_type, emb, audit, attempts, False

    # Secondo giro: fallback SOLO nel tema corrente.
    for visual_type in THEMES[theme]["fallback"]:
        if visual_type in visual_types:
            continue
        cands = candidates_for_type(visual_type)
        ranked = validator.rank(cands, visual_type, used_embeddings, used_refs)
        attempts.append({"type": visual_type, "candidates": len(cands), "valid": len(ranked), "fallback": True})
        if ranked:
            score, candidate, emb, audit = ranked[0]
            return candidate, visual_type, emb, audit, attempts, False

    # Ultima sicurezza: riutilizza una clip GIÀ VALIDATA e coerente col tema.
    # È preferibile a inserire una sveglia o un visual scollegato.
    allowed = set(THEMES[theme]["types"])
    compatible = [x for x in reversed(selected_history) if x["visual_type"] in allowed]
    if compatible:
        best = compatible[0]
        audit = dict(best["audit"])
        audit["mode"] = "validated_reuse"
        return best["candidate"], best["visual_type"], best["embedding"], audit, attempts, True

    raise RuntimeError(f"Nessuna clip affidabile trovata per la scena {scene_index + 1}")


# -----------------------------------------------------------------------------
# VIDEO RENDER
# -----------------------------------------------------------------------------
def download(url: str, dest: Path):
    with HTTP.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)


def normalize_clip(src: Path, dst: Path, seconds: float, scene_index: int, reused: bool):
    # Se una clip validata viene riusata, cambiamo leggermente crop/zoom per non
    # far sembrare un duplicato identico. Nessun effetto aggressivo.
    extra = 1.03 if reused else (1.00 + (scene_index % 3) * 0.008)
    w = int(1080 * extra)
    h = int(1920 * extra)

    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "fps=30,"
        "eq=contrast=1.02:saturation=0.98"
    )
    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{seconds:.3f}",
        "-vf", vf,
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
    out, group = [], []
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
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Captions,,0,0,0,,{esc(text)}\n")
    path.write_text("".join(lines), encoding="utf-8")


def write_meta(kind: str, status: str, extra=None):
    data = {"request_id": SAFE_REQUEST_ID, "kind": kind, "status": status}
    if extra:
        data.update(extra)
    (OUT / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
def main():
    if MODE == "deploy_only":
        write_meta("deploy_only", "ok")
        return

    if not SCRIPT:
        raise SystemExit("SCRIPT mancante")

    if MODE == "test_voice":
        filename = f"voice_test_{SAFE_REQUEST_ID}.mp3"
        dest = OUT / filename
        synthesize(SCRIPT[:320], dest)
        write_meta("test_voice", "ok", {"file": filename})
        return

    if not PEXELS_API_KEY and not PIXABAY_API_KEY:
        raise SystemExit("Serve almeno una chiave tra PEXELS_API_KEY e PIXABAY_API_KEY")

    work = Path(tempfile.mkdtemp(prefix="watchvideo_v10_"))
    try:
        print("1/6 Creo la voce...")
        voice_mp3 = work / "voice.mp3"
        boundaries = synthesize(SCRIPT, voice_mp3)
        total = duration(voice_mp3)

        theme = classify_theme(SCRIPT)
        scene_units = build_scene_plan(SCRIPT, total)
        durations = scene_durations(scene_units, total)
        manual_queries = [q.strip() for q in VISUAL_QUERIES.split(",") if q.strip()]

        print(f"Tema V10: {theme}")
        print(f"Scene: {len(scene_units)} | durata audio {total:.1f}s")

        print("2/6 Avvio controllo visuale AI...")
        validator = VisualValidator()

        print("3/6 Cerco e valido le clip...")
        raw_clips = []
        selected = []
        used_refs = set()
        used_types = []
        used_embeddings = []
        credits = []
        audit_rows = []

        for i, sentence in enumerate(scene_units):
            manual_query = manual_queries[i % len(manual_queries)] if manual_queries else None

            candidate, visual_type, emb, audit, attempts, reused = choose_for_scene(
                validator=validator,
                theme=theme,
                sentence=sentence,
                scene_index=i,
                used_types=used_types,
                used_refs=used_refs,
                used_embeddings=used_embeddings,
                selected_history=selected,
                manual_query=manual_query,
            )

            dest = work / f"raw_{i}.mp4"
            if reused:
                previous_path = next((x["path"] for x in reversed(selected) if x["candidate"].ref == candidate.ref), None)
                if previous_path and Path(previous_path).exists():
                    shutil.copyfile(previous_path, dest)
                else:
                    download(candidate.media_url, dest)
            else:
                download(candidate.media_url, dest)

            raw_clips.append(dest)
            if not reused:
                used_refs.add(candidate.ref)
                if emb is not None:
                    used_embeddings.append(emb)
            used_types.append(visual_type)

            record = {
                "scene": i + 1,
                "text": sentence,
                "duration": round(durations[i], 2),
                "theme": theme,
                "visual_type": visual_type,
                "source": candidate.source,
                "video_id": candidate.id,
                "query": candidate.query,
                "reused": reused,
                "audit": audit,
                "attempts": attempts,
            }
            audit_rows.append(record)
            selected.append({
                "candidate": candidate,
                "visual_type": visual_type,
                "embedding": emb,
                "audit": audit,
                "path": str(dest),
            })

            credits.append(
                f"Scena {i + 1} | {candidate.source} | id={candidate.id} | "
                f"tipo={visual_type} | query={candidate.query} | {candidate.creator} | {candidate.page_url}"
            )
            print(
                f"Scena {i + 1}: tipo='{visual_type}' fonte='{candidate.source}' "
                f"id={candidate.id} reused={reused} query='{candidate.query}' audit={audit}"
            )

        print("4/6 Adatto le clip al formato TikTok...")
        norm = []
        for i, (src, seconds) in enumerate(zip(raw_clips, durations)):
            dst = work / f"norm_{i}.mp4"
            normalize_clip(src, dst, seconds, i, audit_rows[i]["reused"])
            norm.append(dst)

        concat_file = work / "concat.txt"
        concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in norm), encoding="utf-8")
        visuals = work / "visuals.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(visuals)])

        print("5/6 Creo sottotitoli...")
        ass = work / "captions.ass"
        write_ass(ass, boundaries)

        print("6/6 Render finale...")
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

        (OUT / "visual_audit.json").write_text(
            json.dumps(audit_rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUT / "credits.txt").write_text(
            "Visual forniti tramite Pexels e Pixabay.\n\n" + "\n".join(credits),
            encoding="utf-8",
        )

        write_meta("generate_video", "ok", {
            "file": video_filename,
            "duration": round(total, 2),
            "theme": theme,
            "scene_count": len(audit_rows),
            "scenes": audit_rows,
            "visual_validator": "clip" if validator.available else "metadata_fallback",
        })

        print(f"VIDEO PRONTO: {final} ({total:.1f}s)")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
