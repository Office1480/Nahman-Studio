"""Claude : correction du transcript, optimisation de la vidéo et réponses aux questions."""
import json

import anthropic

from . import captions, glossary
from .config import Settings

BETA = "server-side-fallback-2026-07-01"

_strs = {"type": "array", "items": {"type": "string"}}


def _obj(**properties) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _list(**properties) -> dict:
    return {"type": "array", "items": _obj(**properties)}


_text = {"type": "string"}
_int = {"type": "integer"}
_localized = _obj(title=_text, description=_text)

VIDEO_SCHEMA = _obj(
    corrections=_list(wrong=_text, right=_text),
    titles=_strs,
    description=_text,
    chapters=_list(start=_int, title=_text),
    tags=_strs,
    hashtags=_strs,
    thumbnail_text=_text,
    shorts=_list(start=_int, end=_int, title=_text, hook=_text),
    article=_obj(title=_text, body=_text),
    pensee=_obj(text=_text, start=_int),
    sources=_list(start=_int, book=_text, ref=_text, label=_text),
    concepts=_list(start=_int, concept=_text),
    translations=_obj(en=_localized, he=_localized, es=_localized),
)

ANSWER_SCHEMA = _obj(found={"type": "boolean"},
                     paragraphs=_list(text=_text, citations={"type": "array", "items": _int}))


def system_prompt(settings: Settings) -> str:
    g = glossary.data()
    concepts = "\n".join(f"- {c['id']} : {c['label']} ({c['hebrew']}) ; aussi : {', '.join(c['variants'][:6])}"
                         for c in g["concepts"])
    return f"""Tu assistes la chaîne YouTube « {settings.channel_name} » : des cours de Torah en français, avec des
termes hébreux, sur l'enseignement de Rabbi Nahman de Breslev.

Tu reçois le transcript d'un cours, issu des sous-titres automatiques de YouTube. Il contient des erreurs, surtout
sur les mots hébreux. Chaque ligne commence par son horodatage en secondes.

Règles absolues :
- Le transcript est ta seule source. Ne rajoute aucun enseignement, histoire ou citation que l'orateur n'a pas dits.
- N'attribue rien à Rabbi Nahman, à Rabbi Nathan ou à une source si l'orateur ne l'attribue pas lui-même.
- « sources » ne contient que les références citées explicitement ; si le numéro de la Torah n'est pas dit, ref vaut
  « non précisé ». Livres connus : {', '.join(g['sources']['books'])}.
- Écris les termes hébreux avec l'orthographe du glossaire.

Ce que tu produis :
- corrections : erreurs de transcription, avec le texte fautif exact tel qu'il apparaît et sa correction. Uniquement
  ce dont tu es sûr d'après le contexte. Pas de reformulation.
- titles : 3 titres différents, moins de 70 caractères, qui donnent envie de regarder sans tromper sur le contenu,
  avec le thème central dans les premiers mots. Pas de majuscules criardes. Si le titre actuel fait partie d'une série
  (« TORAH N°14 - #3 », « DES FEUILLES POUR GUÉRIR #97 », « HAYE MOHARAN #68 », « lettre 99 »), garde ce repère dans
  les 3 titres : dans ce cas, jusqu'à 100 caractères.
- description : 2 ou 3 courts paragraphes, avec les mots que les gens cherchent. Sans les chapitres, ni liens, ni
  coordonnées : le bloc habituel de la chaîne est ajouté automatiquement en dessous.
- chapters : 6 à 15 chapitres, start en secondes, le premier à 0, titres courts et concrets.
- tags : 10 à 20 mots-clés, orthographes françaises et variantes. hashtags : 3 maximum, avec le #.
- thumbnail_text : 5 mots au maximum, pour la miniature.
- shorts : 3 moments de 30 à 90 secondes compréhensibles sans le reste du cours (une histoire, une phrase forte).
  hook : la phrase d'accroche.
- article : un article fidèle au cours, 600 à 1000 mots, en Markdown (## pour les sous-titres), à la troisième
  personne (« le Rav explique… »).
- pensee : une pensée de moins de 280 caractères, au plus près des mots de l'orateur, avec son horodatage.
- concepts : les moments où un concept du glossaire est vraiment traité, avec son identifiant.
- translations : titre et description courts en anglais, hébreu et espagnol.

Glossaire :
{concepts}"""


def _transcript(passages: list[dict]) -> str:
    return "\n".join(f"[{int(p['start'])}] {p['text']}" for p in passages)


def _user_message(video: dict, passages: list[dict], note: str = "") -> str:
    message = (f"Titre actuel : {video['title']}\nDurée : {captions.timestamp(video['duration'])}\n"
               f"Description actuelle :\n{video['description'][:1500]}\n\nTranscript :\n{_transcript(passages)}")
    return message + (f"\n\nDemande de l'éditeur pour cette nouvelle proposition : {note}" if note else "")


def _params(settings: Settings, video: dict, passages: list[dict], note: str = "") -> dict:
    return {"model": settings.claude_model, "max_tokens": 32000, "system": system_prompt(settings),
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": VIDEO_SCHEMA}},
            "messages": [{"role": "user", "content": _user_message(video, passages, note)}]}


def _parse(message) -> dict:
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé de traiter ce cours")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Réponse de Claude tronquée")
    return json.loads(next(b.text for b in message.content if b.type == "text"))


def client(settings: Settings) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def generate(settings: Settings, video: dict, passages: list[dict], note: str = "", api=None) -> dict:
    api = api or client(settings)
    with api.beta.messages.stream(betas=[BETA], fallbacks="default", **_params(settings, video, passages, note)) as stream:
        return _parse(stream.get_final_message())


def submit_batch(settings: Settings, items: list[tuple[dict, list[dict]]], api=None) -> str:
    """Traitement groupé des archives, à moitié prix. items = [(video, passages)]."""
    api = api or client(settings)
    batch = api.messages.batches.create(requests=[
        {"custom_id": video["id"], "params": _params(settings, video, passages)} for video, passages in items])
    return batch.id


def collect_batch(settings: Settings, batch_id: str, api=None) -> dict | None:
    """Résultats du lot {video_id: payload ou Exception}, ou None s'il n'est pas terminé."""
    api = api or client(settings)
    if api.messages.batches.retrieve(batch_id).processing_status != "ended":
        return None
    results = {}
    for entry in api.messages.batches.results(batch_id):
        try:
            if entry.result.type != "succeeded":
                raise RuntimeError(f"lot : {entry.result.type}")
            results[entry.custom_id] = _parse(entry.result.message)
        except (RuntimeError, ValueError, StopIteration) as exc:
            results[entry.custom_id] = exc
    return results


def answer(settings: Settings, question: str, context: list[dict], api=None) -> dict:
    """Réponse construite uniquement à partir des extraits, chaque paragraphe citant ses sources."""
    api = api or client(settings)
    extracts = "\n\n".join(f"[{c['n']}] « {c['title']} » à {c['time']} : {c['text']}" for c in context)
    message = api.beta.messages.create(
        model=settings.claude_model, max_tokens=4000, betas=[BETA], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        system="Tu réponds aux questions sur les cours de la chaîne, uniquement à partir des extraits fournis, issus "
               "de sous-titres automatiques. Chaque paragraphe cite les numéros des extraits utilisés. N'ajoute rien "
               "qui ne soit pas dans les extraits. Si les extraits ne répondent pas à la question, mets found à "
               "false et laisse paragraphs vide. Réponds en français, simplement, sans inventer de source.",
        messages=[{"role": "user", "content": f"Question : {question}\n\nExtraits :\n{extracts}"}])
    return _parse(message)


def normalize(payload: dict, duration: int) -> dict:
    """Fait respecter les contraintes de YouTube, quoi que l'IA ait produit."""
    payload["titles"] = [t.strip()[:100] for t in payload["titles"] if t.strip()][:3]
    chapters = sorted((c for c in payload["chapters"] if 0 <= c["start"] < max(duration, 1)), key=lambda c: c["start"])
    if not chapters or chapters[0]["start"] != 0:
        chapters.insert(0, {"start": 0, "title": "Introduction"})
    kept = []
    for chapter in chapters:
        if not kept or chapter["start"] - kept[-1]["start"] >= 10:
            kept.append(chapter)
    payload["chapters"] = kept
    tags, size = [], 0
    for tag in dict.fromkeys(t.strip() for t in payload["tags"] if t.strip()):
        size += len(tag) + 3
        if size > 480:
            break
        tags.append(tag)
    payload["tags"] = tags
    payload["shorts"] = [s for s in payload["shorts"] if 0 <= s["start"] < s["end"] <= max(duration, 1)]
    for source in payload["sources"]:
        # « 14 » et « Torah 14 » doivent tomber sur la même entrée de l'index.
        if source["book"] == "Likoutey Moharan" and source["ref"][:1].isdigit():
            source["ref"] = f"Torah {source['ref']}"
    return payload


def compose_description(description: str, chapters: list[dict], hashtags: list[str], footer: str = "") -> str:
    lines = [f"{captions.timestamp(c['start'])} {c['title']}" for c in chapters]
    tail = "\n\nChapitres :\n" + "\n".join(lines) if len(lines) >= 3 else ""
    tail += "\n\n" + " ".join(hashtags) if hashtags else ""
    tail += f"\n\n{footer}" if footer else ""
    room = 4900 - len(tail.encode("utf-8"))
    head = description.encode("utf-8")[:room].decode("utf-8", "ignore")
    return head + tail
