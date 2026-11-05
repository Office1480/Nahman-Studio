"""Lecture des sous-titres YouTube (VTT) et découpage en passages horodatés."""
import re

TIME = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
TAG = re.compile(r"<[^>]+>")


def _seconds(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(vtt: str) -> list[dict]:
    """Retourne des lignes uniques {start, end, text}.

    Les sous-titres automatiques répètent chaque ligne dans le bloc suivant (texte « déroulant »),
    on ne garde donc que les lignes nouvelles.
    """
    cues, recent, timing = [], [], None
    for line in vtt.splitlines():
        match = TIME.search(line)
        if match:
            timing = (_seconds(*match.groups()[:4]), _seconds(*match.groups()[4:]))
            continue
        if timing is None:
            continue
        text = re.sub(r"\s+", " ", TAG.sub("", line)).replace("&nbsp;", " ").strip()
        if text and text not in recent:
            cues.append({"start": timing[0], "end": timing[1], "text": text})
            recent = (recent + [text])[-3:]
    return cues


def to_passages(cues: list[dict], target: float = 30.0, maximum: float = 45.0) -> list[dict]:
    """Regroupe les lignes en passages d'environ 30 s, coupés de préférence en fin de phrase."""
    passages, current = [], []
    for cue in cues:
        current.append(cue)
        span = cue["end"] - current[0]["start"]
        sentence_end = cue["text"].rstrip().endswith((".", "?", "!", "…"))
        if span >= maximum or (span >= target and sentence_end):
            passages.append(_merge(current))
            current = []
    if current:
        passages.append(_merge(current))
    return passages


def _merge(cues: list[dict]) -> dict:
    return {"start": cues[0]["start"], "end": cues[-1]["end"],
            "text": " ".join(c["text"] for c in cues)}


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_srt(passages: list[dict]) -> str:
    def stamp(value: float) -> str:
        ms = int(round(value * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    return "\n".join(f"{i}\n{stamp(p['start'])} --> {stamp(p['end'])}\n{p['text']}\n"
                     for i, p in enumerate(passages, 1))
