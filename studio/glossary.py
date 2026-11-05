"""Vocabulaire Breslev : concepts, orthographes multiples et erreurs connues des sous-titres."""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


@lru_cache
def data() -> dict:
    return json.loads((Path(__file__).parent / "glossaire.json").read_text(encoding="utf-8"))


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("’", "'"))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def phon_word(word: str) -> str:
    """Clé phonétique : Tikkoun, Tikoun et Tiqoun, ou Haklali et Aklali, donnent la même clé."""
    w = fold(word)
    for old, new in (("tch", "x"), ("kh", "x"), ("ch", "x"), ("sh", "x"), ("ph", "f"), ("qu", "k"), ("q", "k"),
                     ("ou", "u"), ("tz", "s"), ("ts", "s"), ("ck", "k"), ("y", "i"), ("h", "")):
        w = w.replace(old, new)
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    w = re.sub(r"(.)\1+", r"\1", w)
    return re.sub(r"[es]+$", "", w) if len(w) > 4 else w


WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def phon(text: str) -> str:
    return " ".join(phon_word(w) for w in WORD.findall(text))


def find_concepts(query: str) -> list[dict]:
    """Concepts reconnus dans la requête, en tolérant les orthographes approchées."""
    key = f" {phon(query)} "
    return [c for c in data()["concepts"]
            if any(f" {phon(v)} " in key for v in c["variants"] + [c["label"]])]


def fix_misheard(text: str) -> str:
    """Corrige les erreurs récurrentes des sous-titres automatiques, sans appel à l'IA."""
    for wrong, right in data()["misheard"].items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text, flags=re.IGNORECASE)
    return text
