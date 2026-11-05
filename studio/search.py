"""Moteur de recherche : mots exacts, orthographes approchées, concepts Breslev et sens."""
import html
from collections import defaultdict

from . import captions, db, embeddings, glossary

STOP = set("""a à au aux avec ce ces cet cette comme dans de des du elle en est et être il ils je la le les leur lui ma
mais me même mes moi mon ne nos notre nous on ou où par pas pour plus qu que quel quelle quels qui quoi sa se ses si son
sur ta te tes toi ton tu un une vos votre vous y c d j l m n s t parle parlent dit disent sujet propos cours video videos
comment pourquoi quand faire fait""".split())


def video_url(video_id: str, start: float) -> str:
    return f"https://youtu.be/{video_id}?t={int(start)}"


def _terms(query: str) -> tuple[list[list[str]], set[str], list[dict]]:
    """Groupes de termes phonétiques (OU dans un groupe, ET entre groupes), mots à surligner, concepts."""
    concepts = glossary.find_concepts(query)
    key = f" {glossary.phon(query)} "
    groups, highlight = [], set()
    for concept in concepts:
        variants = {glossary.phon(v) for v in concept["variants"] + [concept["label"]]}
        groups.append(sorted(variants))
        for variant in sorted(variants, key=len, reverse=True):
            key = key.replace(f" {variant} ", " ")
        highlight |= {w for v in variants for w in v.split() if len(w) > 2}
    for word in glossary.WORD.findall(query):
        code = glossary.phon_word(word)
        if glossary.fold(word) not in STOP and f" {code} " in key:
            groups.append([code])
            highlight.add(code)
    return groups, highlight, concepts


def _match(groups: list[list[str]], joiner: str) -> str:
    clauses = ["(" + " OR ".join(f'phon : "{term}"' for term in group) + ")" for group in groups]
    return f" {joiner} ".join(clauses)


def _lexical(groups: list[list[str]], limit: int = 400) -> list[int]:
    if not groups:
        return []
    with db.connect() as c:
        for joiner in ("AND", "OR"):
            rows = c.execute("SELECT rowid FROM passages_fts WHERE passages_fts MATCH ? ORDER BY rank LIMIT ?",
                             (_match(groups, joiner), limit)).fetchall()
            if rows or len(groups) == 1:
                return [r[0] for r in rows]
    return []


def mark(text: str, highlight: set[str]) -> str:
    out, position = [], 0
    for match in glossary.WORD.finditer(text):
        out.append(html.escape(text[position:match.start()]))
        word = html.escape(match.group())
        out.append(f"<mark>{word}</mark>" if glossary.phon_word(match.group()) in highlight else word)
        position = match.end()
    out.append(html.escape(text[position:]))
    return "".join(out)


def search(query: str, model_dir: str, limit: int = 20, log: bool = True) -> dict:
    groups, highlight, concepts = _terms(query)
    lexical = _lexical(groups)
    semantic = embeddings.search(query, model_dir)

    # Fusion des deux classements ; les correspondances exactes pèsent plus lourd.
    scores: dict[int, float] = defaultdict(float)
    for rank, pid in enumerate(lexical):
        scores[pid] += 2 / (60 + rank)
    for rank, (pid, _) in enumerate(semantic):
        scores[pid] += 1 / (60 + rank)
    lexical_ids = set(lexical)

    results = _group(scores, lexical_ids, highlight, limit)
    if log:
        with db.connect() as c:
            c.execute("INSERT INTO search_log(query, results, mode, at) VALUES (?,?,?,?)",
                      (query, len(results), "hybride" if semantic else "mots", db.now()))
    return {"query": query, "concepts": [{"id": c["id"], "label": c["label"], "hebrew": c["hebrew"]} for c in concepts],
            "results": results, "semantic": bool(semantic), "total_moments": sum(r["count"] for r in results)}


def _group(scores: dict[int, float], lexical_ids: set[int], highlight: set[str], limit: int) -> list[dict]:
    if not scores:
        return []
    ids = sorted(scores, key=scores.get, reverse=True)[:600]
    marks = ",".join("?" * len(ids))
    with db.connect() as c:
        rows = c.execute(f"SELECT p.id, p.video_id, p.start, p.end, p.text, v.title, v.published_at, v.kind, v.views, "
                         f"v.thumbnail FROM passages p JOIN videos v ON v.id=p.video_id WHERE p.id IN ({marks})",
                         ids).fetchall()
        chapters = defaultdict(list)
        video_ids = {r["video_id"] for r in rows}
        for row in c.execute(f"SELECT video_id, start, title FROM chapters WHERE video_id IN "
                             f"({','.join('?' * len(video_ids))}) ORDER BY start", list(video_ids)):
            chapters[row["video_id"]].append((row["start"], row["title"]))

    by_video = defaultdict(list)
    for row in rows:
        by_video[row["video_id"]].append(row)

    results = []
    for video_id, passages in by_video.items():
        passages.sort(key=lambda r: r["start"])
        moments = []
        for p in passages:
            score = scores[p["id"]]
            if moments and p["start"] - moments[-1]["end"] < 60:
                moments[-1]["end"] = p["end"]
                if score > moments[-1]["score"]:
                    moments[-1].update(score=score, html=mark(p["text"], highlight), by_meaning=p["id"] not in lexical_ids)
                continue
            moments.append({"start": p["start"], "end": p["end"], "score": score,
                            "html": mark(p["text"], highlight), "by_meaning": p["id"] not in lexical_ids})
        moments.sort(key=lambda m: -m["score"])
        total = sum(m["score"] for m in moments[:3])
        for m in moments:
            m["time"] = captions.timestamp(m["start"])
            m["url"] = video_url(video_id, m["start"])
            m["chapter"] = next((t for s, t in reversed(chapters[video_id]) if s <= m["start"]), None)
        first = passages[0]
        results.append({"video_id": video_id, "title": first["title"], "published_at": first["published_at"],
                        "kind": first["kind"], "views": first["views"], "thumbnail": first["thumbnail"],
                        "score": total, "count": len(moments), "moments": moments[:4]})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


def context_for_question(question: str, model_dir: str, limit: int = 24) -> list[dict]:
    """Passages à donner à Claude pour répondre à une question."""
    found = search(question, model_dir, limit=12, log=False)
    moments = [(r, m) for r in found["results"] for m in r["moments"]]
    moments.sort(key=lambda rm: -rm[1]["score"])
    context = []
    with db.connect() as c:
        for result, moment in moments[:limit]:
            rows = c.execute("SELECT text FROM passages WHERE video_id=? AND start>=? AND end<=? ORDER BY start",
                             (result["video_id"], moment["start"], moment["end"])).fetchall()
            context.append({"n": len(context) + 1, "video_id": result["video_id"], "title": result["title"],
                            "start": moment["start"], "time": moment["time"], "url": moment["url"],
                            "text": " ".join(r["text"] for r in rows)[:1800]})
    return context


def sources() -> list[dict]:
    """Index par source : livre → références → moments."""
    with db.connect() as c:
        rows = c.execute("SELECT r.book, r.ref, r.label, r.video_id, r.start, v.title FROM refs r "
                         "JOIN videos v ON v.id=r.video_id ORDER BY r.book, r.ref").fetchall()
    books: dict[str, dict] = {}
    for r in rows:
        book = books.setdefault(r["book"], {"book": r["book"], "refs": {}})
        ref = book["refs"].setdefault(r["ref"], {"ref": r["ref"], "label": r["label"], "moments": []})
        ref["moments"].append({"video_id": r["video_id"], "title": r["title"], "time": captions.timestamp(r["start"]),
                               "url": video_url(r["video_id"], r["start"])})
    return [{"book": b["book"], "refs": list(b["refs"].values())} for b in books.values()]


def missing_topics(limit: int = 15) -> list[dict]:
    """Ce que les gens cherchent sans trouver : des idées de prochains cours."""
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT lower(query) AS query, COUNT(*) AS times FROM search_log WHERE results=0 "
            "GROUP BY lower(query) ORDER BY times DESC LIMIT ?", (limit,))]


def top_queries(limit: int = 15) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT lower(query) AS query, COUNT(*) AS times FROM search_log "
            "GROUP BY lower(query) ORDER BY times DESC LIMIT ?", (limit,))]
