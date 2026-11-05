"""Fabrication d'un Short vertical sous-titré à partir d'un moment du cours.

Le téléchargement passe par yt-dlp : à lancer depuis une connexion domestique, avec ffmpeg installé.
"""
import shutil
import subprocess
import sys
from pathlib import Path

from . import captions, db, glossary

ASS_HEADER = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Sub,Arial,62,&H00FFFFFF,&H00000000,&H00000000,-1,1,5,0,2,70,70,420
Style: Hook,Arial,70,&H005CD6FF,&H00000000,&H00000000,-1,1,5,0,8,70,70,190

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(value: float) -> str:
    return f"{int(value // 3600)}:{int(value % 3600 // 60):02d}:{value % 60:05.2f}"


def build_ass(cues: list[dict], start: float, end: float, hook: str) -> str:
    lines = [ASS_HEADER, f"Dialogue: 0,0:00:00.00,{_ass_time(min(end - start, 6))},Hook,,0,0,0,,{hook}\n"]
    timed = [c for c in cues if c["end"] > start and c["start"] < end]
    for i, cue in enumerate(timed):
        # Chaque ligne reste affichée jusqu'à la suivante.
        until = timed[i + 1]["start"] if i + 1 < len(timed) else end
        begin = max(cue["start"], start) - start
        text = glossary.fix_misheard(cue["text"]).replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{_ass_time(begin)},{_ass_time(min(until, end) - start)},Sub,,0,0,0,,{text}\n")
    return "".join(lines)


def render(video_id: str, start: float, end: float, hook: str, out_dir: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg n'est pas installé")
    with db.connect() as c:
        row = c.execute("SELECT raw FROM transcripts WHERE video_id=?", (video_id,)).fetchone()
    if not row:
        raise RuntimeError("Transcript absent")
    work = out_dir / f"{video_id}_{int(start)}"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "source.mp4"
    if not source.exists():
        subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "-f", "b[height<=720]/bv*[height<=720]+ba/b",
                        "--merge-output-format", "mp4", "--download-sections", f"*{start}-{end}", "-o", str(source),
                        f"https://www.youtube.com/watch?v={video_id}"], check=True, capture_output=True, timeout=900)
    (work / "subs.ass").write_text(build_ass(captions.parse_vtt(row["raw"]), start, end, hook), encoding="utf-8")
    graph = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30[bg];"
             "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles=subs.ass[v]")
    result = work / "short.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "source.mp4", "-filter_complex", graph, "-map", "[v]",
                    "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
                    "-t", str(end - start), "short.mp4"], check=True, cwd=work, capture_output=True, timeout=900)
    return result
