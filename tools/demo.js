"use strict";
/* Version de démonstration : mêmes écrans que l'application, données réelles embarquées, sans serveur. */
(function () {
  const data = window.DEMO_DATA;
  const proposals = data.proposals;
  const log = [];

  const fold = (text) => text.toLowerCase().replace(/’/g, "'").normalize("NFD").replace(/\p{Mn}/gu, "");
  function phonWord(word) {
    let w = fold(word);
    for (const [from, to] of [["tch", "x"], ["kh", "x"], ["ch", "x"], ["sh", "x"], ["ph", "f"], ["qu", "k"], ["q", "k"], ["ou", "u"], ["tz", "s"], ["ts", "s"], ["ck", "k"], ["y", "i"], ["h", ""]]) {
      w = w.split(from).join(to);
    }
    w = w.replace(/c(?=[eiy])/g, "s").replace(/c/g, "k").replace(/(.)\1+/g, "$1");
    return w.length > 4 ? w.replace(/[es]+$/, "") : w;
  }
  const words = (text) => text.match(/[\p{L}]+|\d+/gu) || [];
  const phon = (text) => words(text).map(phonWord).join(" ");
  const esc = (t) => t.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (seconds) => {
    const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const pad = (x) => String(x).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
  };

  function terms(query) {
    const concepts = data.concepts.filter((c) => [...c.variants, c.label].some((v) => ` ${phon(query)} `.includes(` ${phon(v)} `)));
    let key = ` ${phon(query)} `;
    const groups = [], highlight = new Set();
    for (const concept of concepts) {
      const variants = [...new Set([...concept.variants, concept.label].map(phon))];
      groups.push(variants);
      variants.sort((a, b) => b.length - a.length).forEach((v) => { key = key.split(` ${v} `).join(" "); });
      variants.forEach((v) => v.split(" ").filter((w) => w.length > 2).forEach((w) => highlight.add(w)));
    }
    for (const word of words(query)) {
      const code = phonWord(word);
      if (!data.stop.includes(fold(word)) && key.includes(` ${code} `)) {
        groups.push([code]);
        highlight.add(code);
      }
    }
    return { concepts, groups, highlight };
  }

  const mark = (text, highlight) => text.replace(/[\p{L}]+|\d+|[^\p{L}\d]+/gu, (piece) => {
    const safe = esc(piece);
    return /[\p{L}\d]/u.test(piece) && highlight.has(phonWord(piece)) ? `<mark>${safe}</mark>` : safe;
  });

  function search(query) {
    const { concepts, groups, highlight } = terms(query);
    const hit = (p, group) => group.some((term) => ` ${p.phon} `.includes(` ${term} `));
    let found = data.passages.filter((p) => groups.length && groups.every((g) => hit(p, g)));
    if (!found.length) found = data.passages.filter((p) => groups.some((g) => hit(p, g)));
    const byVideo = new Map();
    for (const p of found) {
      if (!byVideo.has(p.v)) byVideo.set(p.v, []);
      byVideo.get(p.v).push(p);
    }
    const results = [...byVideo].map(([id, passages]) => {
      const video = data.videos[id];
      const moments = [];
      for (const p of passages.sort((a, b) => a.s - b.s)) {
        const last = moments[moments.length - 1];
        if (last && p.s - last.end < 60) { last.end = p.e; continue; }
        moments.push({ start: p.s, end: p.e, time: clock(p.s), url: `https://youtu.be/${id}?t=${Math.floor(p.s)}`, html: mark(p.t, highlight), by_meaning: false, chapter: (data.chapters[id] || []).filter((c) => c.start <= p.s).pop()?.title });
      }
      return { video_id: id, title: video.title, published_at: video.published_at, kind: video.kind, views: video.views, thumbnail: video.thumb, count: moments.length, moments: moments.slice(0, 4) };
    }).sort((a, b) => b.count - a.count).slice(0, 20);
    log.push({ query: query.toLowerCase(), results: results.length });
    return { query, concepts: concepts.map((c) => ({ id: c.id, label: c.label, hebrew: c.hebrew })), results, semantic: false, total_moments: results.reduce((n, r) => n + r.count, 0) };
  }

  function ask(question) {
    const found = search(question);
    const sources = found.results.flatMap((r) => r.moments.map((m) => ({ title: r.title, time: m.time, url: m.url, text: m.html.replace(/<[^>]+>/g, "") }))).slice(0, 8).map((s, i) => ({ n: i + 1, ...s }));
    return { mode: "extraits", found: sources.length > 0, paragraphs: [], sources };
  }

  const count = (items) => Object.entries(items.reduce((all, q) => ({ ...all, [q]: (all[q] || 0) + 1 }), {})).map(([query, times]) => ({ query, times })).sort((a, b) => b.times - a.times).slice(0, 15);

  window.STUDIO_API = async function (path, body) {
    const url = new URL(path, "https://demo.local");
    const route = url.pathname;
    if (route === "/api/me") return { admin: true, can_login: false, can_ask: false, channel: data.channel, demo: data.banner };
    if (route === "/api/search") return search(url.searchParams.get("q"));
    if (route === "/api/ask") return ask(body.question);
    if (route === "/api/sources") return data.sources;
    if (route === "/api/concepts") return data.concepts.map((c) => ({ id: c.id, label: c.label, hebrew: c.hebrew, videos: 0 }));
    if (route === "/api/proposals") {
      const status = url.searchParams.get("status"), kind = url.searchParams.get("kind");
      return proposals.filter((p) => p.status === status && (!kind || p.kind === kind));
    }
    const action = route.match(/^\/api\/proposals\/(\d+)\/(approve|reject|regenerate)$/);
    if (action) {
      const proposal = proposals.find((p) => p.id === Number(action[1]));
      proposal.status = action[2] === "approve" ? "approved" : "rejected";
      return { ok: true };
    }
    if (route === "/api/archives") return data.archives;
    if (route === "/api/settings") return body ? { ok: true } : { archive_enabled: false, auto_low_risk: false, validated: 0 };
    if (route === "/api/diffusion") return data.diffusion;
    if (route === "/api/insights") return { top: count(log.map((l) => l.query)), missing: count(log.filter((l) => !l.results).map((l) => l.query)), top_videos: [] };
    if (route === "/api/status") return data.status;
    return { ok: true };
  };
})();
