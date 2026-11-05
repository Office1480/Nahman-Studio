"use strict";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const view = $("#view");
const number = (value) => Number(value || 0).toLocaleString("fr-FR");
const clock = (seconds) => {
  const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = (x) => String(x).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
};
const toSeconds = (text) => text.split(":").reduce((total, part) => total * 60 + Number(part), 0);
const dateFr = (value) => value ? new Date(value).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }) : "";

async function http(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.detail || `Erreur ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
const api = window.STUDIO_API || http;

let me = { admin: false };
let pending = 0;

function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove("show"), 3000);
}

const PUBLIC_TABS = [["recherche", "Recherche"], ["question", "Poser une question"], ["sources", "Sources"]];
const ADMIN_TABS = [["valider", "À valider"], ["archives", "Archives"], ["diffusion", "Diffusion"], ["analyse", "Analyse"], ["etat", "État"]];

function renderNav(active) {
  const tabs = me.admin ? [...PUBLIC_TABS, ...ADMIN_TABS] : PUBLIC_TABS;
  $("#nav").innerHTML = tabs.map(([id, label]) => {
    const badge = id === "valider" && pending ? `<span class="count">${pending}</span>` : "";
    return `<a href="#${id}" class="${id === active ? "active" : ""}">${esc(label)}${badge}</a>`;
  }).join("");
  const button = $("#login-button");
  button.textContent = me.admin ? "Déconnexion" : "Connexion";
  button.hidden = me.demo || (!me.admin && !me.can_login);
}

/* Recherche */

const SUGGESTIONS = ["Tikoun Haklali", "Hitbodedout", "la tristesse", "Azamra", "Ouman", "la foi", "les désirs", "la prière"];

function viewSearch(query) {
  view.innerHTML = `
    <section class="hero">
      <h1>Trouver le moment précis d'un cours</h1>
      <p>Cherche un sujet : tu arrives directement à la bonne minute de la vidéo.</p>
    </section>
    <form class="searchbar" id="search-form" role="search">
      <input type="search" id="q" placeholder="Par exemple : tikoun aklali" aria-label="Sujet à chercher" value="${esc(query)}" autocomplete="off">
      <button class="primary" type="submit">Chercher</button>
    </form>
    <div id="search-chips" class="chips">${SUGGESTIONS.map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join("")}</div>
    <div id="results"></div>`;
  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = $("#q").value.trim();
    if (value) location.hash = `#recherche/${encodeURIComponent(value)}`;
  });
  $$(".chip", view).forEach((chip) => chip.addEventListener("click", () => { location.hash = `#recherche/${encodeURIComponent(chip.textContent)}`; }));
  if (query) runSearch(query);
}

async function runSearch(query) {
  const box = $("#results");
  box.innerHTML = `<p class="empty">Recherche…</p>`;
  const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
  if (!data.results.length) {
    box.innerHTML = `<p class="empty">Aucun passage trouvé pour « ${esc(query)} ».</p>`;
    return;
  }
  const concepts = data.concepts.map((c) => `<span class="tag">${esc(c.label)} ${esc(c.hebrew)}</span>`).join(" ");
  box.innerHTML = `
    <p class="summary">${data.total_moments} moment${data.total_moments > 1 ? "s" : ""} dans ${data.results.length} vidéo${data.results.length > 1 ? "s" : ""} ${concepts}</p>
    ${data.results.map(resultCard).join("")}`;
}

function resultCard(result) {
  const moments = result.moments.map((m) => `
    <div class="moment">
      <a class="time" href="${esc(m.url)}" target="_blank" rel="noopener" aria-label="Voir à ${esc(m.time)}">${esc(m.time)}</a>
      <p>${m.chapter ? `<strong>${esc(m.chapter)}</strong><br>` : ""}${m.html}${m.by_meaning ? ` <span class="tag">proche par le sens</span>` : ""}</p>
    </div>`).join("");
  const more = result.count > result.moments.length ? `<p class="small muted">et ${result.count - result.moments.length} autre${result.count - result.moments.length > 1 ? "s" : ""} moment${result.count - result.moments.length > 1 ? "s" : ""} dans ce cours</p>` : "";
  return `<article class="card">
    <div class="result-head">
      <img src="${esc(result.thumbnail)}" alt="" loading="lazy">
      <div><h2>${esc(result.title)}</h2><span class="small muted">${[dateFr(result.published_at), `${number(result.views)} vues`].filter(Boolean).join(" · ")}</span></div>
    </div>${moments}${more}</article>`;
}

/* Question */

function viewQuestion() {
  view.innerHTML = `
    <h1>Poser une question aux cours</h1>
    <p class="muted">La réponse vient uniquement des cours, avec le lien vers chaque passage. Elle s'appuie sur des sous-titres automatiques : vérifie toujours dans la vidéo.</p>
    <form class="searchbar" id="ask-form">
      <input type="text" id="question" placeholder="Que dit le Rav sur la tristesse ?" aria-label="Ta question">
      <button class="primary" type="submit">Demander</button>
    </form>
    <div id="answer"></div>`;
  $("#ask-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = $("#question").value.trim();
    if (!question) return;
    const box = $("#answer");
    box.innerHTML = `<p class="empty">Lecture des cours…</p>`;
    try {
      const data = await api("/api/ask", { question });
      box.innerHTML = answerHtml(data);
    } catch (error) {
      box.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
    }
  });
}

function answerHtml(data) {
  const byNumber = Object.fromEntries(data.sources.map((s) => [s.n, s]));
  const list = data.sources.slice(0, 8).map((s) => `
    <div class="moment"><a class="time" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.time)}</a>
    <p><strong>${esc(s.title)}</strong><br>${esc(s.text.slice(0, 260))}…</p></div>`).join("");
  if (data.mode === "extraits") {
    const reason = data.limit ? "La limite de questions du jour est atteinte." : "Les réponses rédigées arrivent quand la clé Claude est branchée.";
    return `<p class="summary">${reason} Voici les passages les plus proches.</p><div class="card">${list || "Aucun passage trouvé."}</div>`;
  }
  if (!data.found) return `<p class="empty">Les cours indexés ne répondent pas à cette question.</p>`;
  const paragraphs = data.paragraphs.map((p) => {
    const cites = p.citations.map((n) => byNumber[n] ? `<a class="cite" href="${esc(byNumber[n].url)}" target="_blank" rel="noopener">[${n}]</a>` : "").join("");
    return `<p>${esc(p.text)}${cites}</p>`;
  }).join("");
  return `<div class="card answer">${paragraphs}</div><h2>Les passages utilisés</h2><div class="card">${list}</div>`;
}

/* Sources */

async function viewSources() {
  view.innerHTML = `<h1>Index par source</h1><p class="muted">Toutes les explications d'un texte, classées par livre.</p><div id="sources"></div><h2>Par thème</h2><div id="themes" class="chips"></div>`;
  const [books, concepts] = await Promise.all([api("/api/sources"), api("/api/concepts")]);
  $("#themes").innerHTML = concepts.map((c) => `<a class="chip" href="#recherche/${encodeURIComponent(c.label)}" style="text-decoration:none">${esc(c.label)}${c.videos ? ` · ${c.videos}` : ""}</a>`).join("");
  if (!books.length) {
    $("#sources").innerHTML = `<p class="empty">L'index se remplit à mesure que Claude analyse les cours.</p>`;
    return;
  }
  $("#sources").innerHTML = books.map((book) => `
    <div class="card"><h2>${esc(book.book)}</h2>
    ${book.refs.map((ref) => `<details><summary>${esc(ref.ref)}${ref.label ? ` · ${esc(ref.label)}` : ""} <span class="tag">${ref.moments.length}</span></summary><div>
      ${ref.moments.map((m) => `<div class="source-line"><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.time)}</a> ${esc(m.title)}</div>`).join("")}
    </div></details>`).join("")}</div>`).join("");
}

/* Validation */

function proposalCard(p) {
  const d = p.payload;
  const chapters = d.chapters.map((c) => `${c.time} ${c.title}`).join("\n");
  const translations = Object.entries(d.translations).map(([lang, t]) => `<p dir="${lang === "he" ? "rtl" : "ltr"}"><span class="tag">${esc(lang)}</span> <strong>${esc(t.title)}</strong><br>${esc(t.description)}</p>`).join("");
  const shorts = d.shorts.map((s, i) => `
    <div class="short-line"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.time)}</a> <strong>${esc(s.title)}</strong>
    <span class="muted small">${s.end - s.start} s</span><br><span class="small">${esc(s.hook)}</span>
    ${me.demo ? "" : `<br><button type="button" class="link" data-short="${i}">Fabriquer ce Short</button>`}</div>`).join("");
  const sources = d.sources.map((s) => `<p class="small">${esc(clock(s.start))} · <strong>${esc(s.book)}</strong> ${esc(s.ref)} ${esc(s.label)}</p>`).join("") || `<p class="small muted">Aucune source citée explicitement.</p>`;
  const corrections = d.corrections.map((c) => `<span class="tag">${esc(c.wrong)} → ${esc(c.right)}</span>`).join(" ") || `<span class="small muted">Aucune.</span>`;
  const preview = p.thumbnail_preview || `/api/thumbnail/${encodeURIComponent(p.video_id)}?text=${encodeURIComponent(d.thumbnail_text)}`;
  return `<article class="card" data-proposal="${p.id}">
    <div class="proposal-head">
      <img src="${esc(p.thumbnail)}" alt="">
      <div>
        <span class="tag ${p.kind === "live" ? "ok" : ""}">${p.kind === "live" ? "Nouveau cours" : "Archive"}</span>
        <p class="was">Titre actuel : ${esc(p.current_title)}</p>
        <a class="small" href="${esc(p.url)}" target="_blank" rel="noopener">Voir sur YouTube</a>
        <span class="small muted">· ${esc(clock(p.duration))} · ${number(p.views)} vues</span>
      </div>
    </div>
    <div role="radiogroup" aria-label="Choix du titre">
      ${d.titles.map((title, i) => `<label class="title-option"><input type="radio" name="title-${p.id}" value="${i}" ${i === 0 ? "checked" : ""}><span>${esc(title)}</span></label>`).join("")}
    </div>
    <details><summary>Description, chapitres et tags</summary><div>
      <label class="field"><span>Description</span><textarea data-field="description">${esc(d.description)}</textarea></label>
      <label class="field"><span>Chapitres (une ligne par chapitre : temps puis titre)</span><textarea data-field="chapters" rows="8">${esc(chapters)}</textarea></label>
      <label class="field"><span>Tags, séparés par des virgules</span><input type="text" data-field="tags" value="${esc(d.tags.join(", "))}"></label>
    </div></details>
    <details><summary>Miniature</summary><div>
      <label class="field"><span>Texte de la miniature</span><input type="text" data-field="thumbnail_text" value="${esc(d.thumbnail_text)}"></label>
      <label class="toggle"><input type="checkbox" data-field="thumbnail"><span>Remplacer la miniature actuelle</span></label>
      <img class="thumb-preview" src="${esc(preview)}" alt="Aperçu de la miniature" loading="lazy">
    </div></details>
    <details><summary>Article pour le site</summary><div class="article-body"><h2>${esc(d.article.title)}</h2>${markdown(d.article.body)}</div></details>
    <details><summary>Shorts proposés (${d.shorts.length})</summary><div>${shorts}</div></details>
    <details><summary>Pensée du jour</summary><div><p>${esc(d.pensee.text)}</p><a class="small" href="https://youtu.be/${esc(p.video_id)}?t=${d.pensee.start}" target="_blank" rel="noopener">À ${esc(clock(d.pensee.start))}</a></div></details>
    <details><summary>Traductions</summary><div>${translations}</div></details>
    <details><summary>Sources détectées et corrections</summary><div>${sources}<p>${corrections}</p></div></details>
    <div class="actions">
      <button class="primary" type="button" data-action="approve">Valider et publier</button>
      <button type="button" data-action="ask-note">Autre proposition</button>
      <button class="danger" type="button" data-action="reject">Rejeter</button>
    </div>
    <div class="note-box" hidden>
      <label class="field"><span>Que faut-il changer ? (facultatif)</span><input type="text" data-field="note" placeholder="Par exemple : un titre plus court"></label>
      <button type="button" data-action="regenerate">Demander une nouvelle proposition</button>
    </div>
  </article>`;
}

function markdown(text) {
  return text.trim().split(/\n\s*\n/).map((block) => {
    const safe = esc(block.trim()).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    const heading = safe.match(/^(#{2,3})\s+(.*)/);
    return heading ? `<h${heading[1].length + 1}>${heading[2]}</h${heading[1].length + 1}>` : `<p>${safe}</p>`;
  }).join("");
}

function readChoice(card, p) {
  const field = (name) => $(`[data-field="${name}"]`, card);
  const chapters = field("chapters").value.split("\n").map((line) => line.trim().match(/^([\d:]+)\s+(.+)$/)).filter(Boolean)
    .map((m) => ({ start: toSeconds(m[1]), title: m[2] }));
  return {
    title: p.payload.titles[Number($(`input[name="title-${p.id}"]:checked`, card).value)],
    description: field("description").value,
    tags: field("tags").value.split(",").map((t) => t.trim()).filter(Boolean),
    chapters,
    thumbnail: field("thumbnail").checked,
    thumbnail_text: field("thumbnail_text").value,
  };
}

function bindProposals(list, reload) {
  $$("[data-proposal]", view).forEach((card) => {
    const p = list.find((x) => String(x.id) === card.dataset.proposal);
    card.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const action = button.dataset.action;
      if (button.dataset.short !== undefined) return makeShort(button, p, p.payload.shorts[Number(button.dataset.short)]);
      if (!action) return;
      if (action === "ask-note") {
        $(".note-box", card).hidden = false;
        $('[data-field="note"]', card).focus();
        return;
      }
      button.disabled = true;
      try {
        if (action === "approve") {
          await api(`/api/proposals/${p.id}/approve`, { chosen: readChoice(card, p) });
          toast("Validé. La publication part au prochain passage, hors Chabbat.");
        } else if (action === "reject") {
          await api(`/api/proposals/${p.id}/reject`, {});
          toast("Proposition rejetée.");
        } else {
          await api(`/api/proposals/${p.id}/regenerate`, { note: $('[data-field="note"]', card).value });
          toast("Nouvelle proposition demandée.");
        }
        await refreshPending();
        reload();
      } catch (error) {
        button.disabled = false;
        toast(error.message);
      }
    });
  });
}

async function makeShort(button, p, short) {
  button.disabled = true;
  button.textContent = "Fabrication en cours, quelques minutes…";
  try {
    const result = await api("/api/shorts", { video_id: p.video_id, start: short.start, end: short.end, title: short.title, hook: short.hook });
    button.outerHTML = `<a href="${esc(result.file)}" target="_blank" rel="noopener">Voir le Short</a>`;
  } catch (error) {
    button.disabled = false;
    button.textContent = "Fabriquer ce Short";
    toast(error.message);
  }
}

async function viewValidate() {
  const list = await api("/api/proposals?status=pending&kind=live");
  const approved = await api("/api/proposals?status=approved");
  view.innerHTML = `
    <h1>À valider</h1>
    ${approved.length ? `<p class="summary">${approved.length} validation${approved.length > 1 ? "s" : ""} en attente de publication.</p>` : ""}
    ${list.length ? list.map(proposalCard).join("") : `<p class="empty">Rien à valider pour l'instant. Les nouveaux cours arrivent ici dès que leurs sous-titres sont disponibles.</p>`}`;
  bindProposals(list, viewValidate);
}

async function viewArchives() {
  const [data, list, options] = await Promise.all([api("/api/archives"), api("/api/proposals?status=pending&kind=archive"), api("/api/settings")]);
  const percent = data.total ? (data.done / data.total) * 100 : 0;
  const groups = data.experiment.groups;
  const experiment = data.experiment.started_at ? `
    <div class="table-wrap"><table><thead><tr><th>Groupe</th><th class="num">Vidéos</th><th class="num">Vues gagnées</th></tr></thead><tbody>
    ${Object.entries(groups).map(([name, g]) => `<tr><td>${esc(name)}</td><td class="num">${g.videos}</td><td class="num">${number(g.gain)}</td></tr>`).join("")}
    </tbody></table></div>` : `<p class="small muted">La comparaison démarre avec le premier lot : les 200 vidéos les plus vues sont réparties entre un groupe optimisé et un groupe témoin, laissé intact pendant 4 semaines.</p>`;
  view.innerHTML = `
    <h1>Réveil des archives</h1>
    <div class="card">
      <strong>${number(data.done)} vidéos traitées sur ${number(data.total)}</strong>
      <div class="progress" role="img" aria-label="${Math.round(percent)} % traité"><div style="width:${percent}%"></div></div>
      <span class="small muted">${data.per_day} vidéos par jour au maximum avec le quota gratuit de YouTube.</span>
      <label class="toggle"><input type="checkbox" id="archive-enabled" ${options.archive_enabled ? "checked" : ""}><span>Préparer un lot chaque jour</span></label>
      <label class="toggle"><input type="checkbox" id="auto-low-risk" ${options.auto_low_risk ? "checked" : ""}><span>Publier les sous-titres corrigés sans attendre ma validation
        <br><span class="small muted">${options.validated} validation${options.validated > 1 ? "s" : ""} faite${options.validated > 1 ? "s" : ""}. À activer quand tu fais confiance aux corrections.</span></span></label>
    </div>
    <h2>Lot à valider (${list.length})</h2>
    ${list.length ? `<div class="actions" style="margin:0 0 12px"><button type="button" id="approve-all">Tout valider avec le premier titre</button></div>${list.map(proposalCard).join("")}` : `<p class="empty">Aucun lot en attente.</p>`}
    <h2>Mesure de l'effet</h2><div class="card">${experiment}</div>
    <h2>Prochaines vidéos</h2>
    <div class="card table-wrap"><table><thead><tr><th>Vidéo</th><th class="num">Vues</th></tr></thead><tbody>
      ${data.next.map((v) => `<tr><td>${esc(v.title)}</td><td class="num">${number(v.views)}</td></tr>`).join("")}
    </tbody></table></div>`;
  bindProposals(list, viewArchives);
  const save = (key) => async (event) => {
    await api("/api/settings", { values: { [key]: event.target.checked } });
    toast("Réglage enregistré.");
  };
  $("#archive-enabled").addEventListener("change", save("archive_enabled"));
  $("#auto-low-risk").addEventListener("change", save("auto_low_risk"));
  $("#approve-all")?.addEventListener("click", async (event) => {
    const button = event.target;
    if (!button.dataset.armed) {
      button.dataset.armed = "1";
      button.textContent = `Confirmer : valider les ${list.length} vidéos avec le premier titre`;
      return;
    }
    await api("/api/proposals/approve-all", { kind: "archive" });
    toast("Lot validé.");
    await refreshPending();
    viewArchives();
  });
}

async function viewDiffusion() {
  const data = await api("/api/diffusion");
  view.innerHTML = `
    <h1>Diffusion</h1>
    <h2>Pensées du jour</h2>
    <p class="muted">Une par jour sur ton canal Telegram, jamais pendant Chabbat. Tu peux aussi les copier pour WhatsApp.</p>
    ${data.posts.length ? data.posts.map((post, i) => `
      <div class="card"><p>${esc(post.text)}</p>
      <span class="tag ${post.status === "sent" ? "ok" : ""}">${post.status === "sent" ? "Envoyée" : "En file"}</span>
      <button class="link" type="button" data-copy="${i}">Copier</button></div>`).join("") : `<p class="empty">Les pensées arrivent après chaque publication validée.</p>`}
    <h2>Newsletter de la semaine</h2>
    <div class="card"><label class="field"><span>Brouillon</span><textarea id="newsletter" rows="8" readonly>${esc(data.newsletter)}</textarea></label>
    <button type="button" id="copy-newsletter">Copier</button></div>`;
  const copy = (text) => navigator.clipboard.writeText(text).then(() => toast("Copié."), () => toast("Copie impossible ici : sélectionne le texte à la main."));
  $$("[data-copy]", view).forEach((button) => button.addEventListener("click", () => {
    const post = data.posts[Number(button.dataset.copy)];
    copy(`${post.text}\n\n${post.url}`);
  }));
  $("#copy-newsletter").addEventListener("click", () => copy(data.newsletter));
}

async function viewInsights() {
  const data = await api("/api/insights");
  const rows = (items) => items.map((x) => `<tr><td>${esc(x.query)}</td><td class="num">${x.times}</td></tr>`).join("");
  view.innerHTML = `
    <h1>Analyse</h1>
    <h2>Recherché sans résultat</h2>
    <p class="muted">Des idées de sujets pour tes prochains cours.</p>
    <div class="card table-wrap">${data.missing.length ? `<table><thead><tr><th>Recherche</th><th class="num">Fois</th></tr></thead><tbody>${rows(data.missing)}</tbody></table>` : `<p class="empty">Pas encore de données.</p>`}</div>
    <h2>Les plus recherchés</h2>
    <div class="card table-wrap">${data.top.length ? `<table><thead><tr><th>Recherche</th><th class="num">Fois</th></tr></thead><tbody>${rows(data.top)}</tbody></table>` : `<p class="empty">Pas encore de données.</p>`}</div>
    <h2>Vidéos les plus vues sur 90 jours</h2>
    <div class="card table-wrap">${data.top_videos.length ? `<table><thead><tr><th>Vidéo</th><th class="num">Vues</th><th class="num">Regardé en moyenne</th></tr></thead><tbody>
      ${data.top_videos.map((v) => `<tr><td><a href="https://youtu.be/${esc(v.video)}" target="_blank" rel="noopener">${esc(v.video)}</a></td><td class="num">${number(v.views)}</td><td class="num">${Math.round(v.averageViewPercentage)} %</td></tr>`).join("")}
    </tbody></table>` : `<p class="empty">Disponible une fois la chaîne connectée.</p>`}</div>`;
}

async function viewStatus() {
  const s = await api("/api/status");
  const key = (ok, label, missing) => `<div class="toggle"><span class="tag ${ok ? "ok" : "warn"}">${ok ? "Branché" : "À brancher"}</span><span>${esc(label)}${ok ? "" : `<br><span class="small muted">${esc(missing)}</span>`}</span></div>`;
  const videos = (s.videos.long || 0) + (s.videos.live || 0);
  view.innerHTML = `
    <h1>État</h1>
    <div class="grid">
      <div class="stat"><strong>${number(videos)}</strong><span>cours inventoriés</span></div>
      <div class="stat"><strong>${number(s.videos.short)}</strong><span>Shorts</span></div>
      <div class="stat"><strong>${number(s.transcripts)}</strong><span>transcripts stockés</span></div>
      <div class="stat"><strong>${number(s.passages)}</strong><span>passages cherchables</span></div>
      <div class="stat"><strong>${number(s.quota.spent)}</strong><span>unités de quota sur ${number(s.quota.limit)}</span></div>
    </div>
    ${s.shabbat ? `<div class="card"><strong>Chabbat ou Yom Tov en cours.</strong> Les publications reprennent après la sortie.</div>` : ""}
    <div class="card"><h2>Connexions</h2>
      ${key(s.keys.claude, "Claude", "Sans cette clé, pas de propositions ni de réponses rédigées.")}
      ${key(s.keys.youtube, "Chaîne YouTube", "Sans elle, rien ne peut être publié. La détection marche déjà.")}
      ${key(s.keys.telegram, "Telegram", "Sans lui, les validations se font uniquement ici.")}
    </div>
    <div class="card"><h2>En attente de sous-titres</h2>
      ${s.waiting_captions.length ? s.waiting_captions.map((v) => `<p>${esc(v.title)}<br><span class="small muted">Dernière vérification : ${v.caption_checked_at ? new Date(v.caption_checked_at).toLocaleString("fr-FR") : "pas encore"}</span></p>`).join("") : `<p class="muted">Aucune vidéo en attente.</p>`}
      ${me.demo ? "" : `<button type="button" id="tick">Vérifier maintenant</button>`}
    </div>
    <div class="card"><h2>Journal</h2>
      ${s.events.length ? s.events.map((e) => `<p class="small"><span class="tag ${e.level === "error" ? "bad" : e.level === "warning" ? "warn" : ""}">${new Date(e.at).toLocaleString("fr-FR")}</span> ${esc(e.message)}</p>`).join("") : `<p class="muted">Rien pour l'instant.</p>`}
    </div>`;
  $("#tick")?.addEventListener("click", async () => {
    await api("/api/tick", {});
    toast("Vérification lancée.");
  });
}

/* Navigation */

async function refreshPending() {
  if (!me.admin) return;
  const list = await api("/api/proposals?status=pending&kind=live");
  pending = list.length;
}

async function route() {
  const [tab = "recherche", ...rest] = location.hash.slice(1).split("/");
  const name = tab || "recherche";
  renderNav(name);
  try {
    if (name === "recherche") viewSearch(decodeURIComponent(rest.join("/")));
    else if (name === "question") viewQuestion();
    else if (name === "sources") await viewSources();
    else if (!me.admin) showLogin();
    else if (name === "valider") await viewValidate();
    else if (name === "archives") await viewArchives();
    else if (name === "diffusion") await viewDiffusion();
    else if (name === "analyse") await viewInsights();
    else if (name === "etat") await viewStatus();
    else viewSearch("");
  } catch (error) {
    if (error.status === 401) showLogin();
    else view.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
  }
  window.scrollTo(0, 0);
}

function showLogin() {
  view.innerHTML = `
    <h1>Connexion</h1>
    <form class="card" id="login-form">
      <label class="field"><span>Mot de passe</span><input type="password" id="password" autocomplete="current-password" required></label>
      <button class="primary" type="submit">Se connecter</button>
    </form>`;
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/login", { password: $("#password").value });
      me = await api("/api/me");
      await refreshPending();
      location.hash = "#valider";
      route();
    } catch (error) {
      toast(error.message);
    }
  });
}

$("#login-button").addEventListener("click", async () => {
  if (me.admin) {
    await api("/api/logout", {});
    me = await api("/api/me");
    pending = 0;
    location.hash = "#recherche";
    route();
  } else {
    showLogin();
  }
});

window.addEventListener("hashchange", route);

(async function start() {
  me = await api("/api/me");
  if (me.demo) {
    const banner = $("#demo-banner");
    banner.hidden = false;
    banner.textContent = me.demo;
  }
  await refreshPending();
  route();
})();
