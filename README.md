# Nahman Studio

Automatise l'optimisation de la chaîne YouTube Nahman meOuman. L'application détecte les nouvelles vidéos, récupère leurs
sous-titres dès qu'ils existent, prépare tout avec Claude et n'attend qu'une validation. Elle offre aussi un moteur de
recherche à la minute près dans tous les cours.

## Ce que fait l'application

| Étape | Fonctionnement |
|---|---|
| Détection | Flux public de la chaîne, toutes les 15 min, sans clé ni quota |
| Sous-titres | Vérifiés toutes les 3 h, transcript intégral stocké avec ses horodatages. Abandon et alerte après 72 h |
| Nettoyage | Glossaire Breslev automatique, puis corrections proposées par Claude |
| Proposition | 3 titres, description, chapitres, tags, texte de miniature, 3 Shorts, article, pensée du jour, sources citées, traductions |
| Validation | Telegram ou tableau de bord. Rien n'est publié sans validation |
| Publication | Titre, description, tags, traductions, sous-titres corrigés. Jamais pendant Chabbat et Yom Tov |
| Archives | Lots quotidiens à moitié prix (Batch API), les plus vues d'abord, avec groupe témoin pendant 4 semaines |
| Recherche | Mots exacts, orthographes approchées, concepts du glossaire et sens (vecteurs locaux, sans clé) |
| Questions | Réponse de Claude à partir des extraits uniquement, avec le lien vers chaque passage |
| Extraits | Toutes les histoires, les Shorts et les moments clés de chaque cours, sur une seule page : tu regardes, tu ajustes, tu valides, l'appli extrait et publie |
| Diffusion | Page publique par cours, pensée quotidienne sur Telegram, brouillon de newsletter |
| Analyse | Recherches sans résultat, sujets les plus demandés, vidéos les plus vues |

Protections : le titre, la description et les tags d'origine sont sauvegardés et restaurables. La description d'origine
(livres, dons, site officiel) reste toujours en bas de la nouvelle. La miniature n'est remplacée que si la case est cochée.

## Installation locale

Il faut Python 3.12 et ffmpeg.

```bash
pip install -r requirements.txt
cp .env.example .env
python run.py import-channel
python run.py import-subs
python run.py reindex
python run.py serve
```

L'application tourne alors sur http://localhost:8000. Renseigne au moins `ADMIN_PASSWORD` dans `.env`.

`import-subs` récupère les sous-titres des archives sans consommer le quota YouTube. Lance-le depuis une connexion
domestique : YouTube bloque souvent ces accès depuis un serveur.

## Clés

Chaque clé active une partie de l'application, et l'état de chacune est visible dans l'onglet État.

| Clé | Sert à | Où l'obtenir |
|---|---|---|
| `ANTHROPIC_API_KEY` | Propositions, articles, réponses | console.anthropic.com |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Publication, sous-titres par l'API, statistiques | console.cloud.google.com |
| `GOOGLE_REFRESH_TOKEN` | Rempli par `python run.py auth-youtube` | Connecte-toi avec le compte de la chaîne |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Validation depuis le téléphone | @BotFather, puis @userinfobot |
| `TELEGRAM_CHANNEL_ID` | Pensée du jour publique (facultatif) | Ton canal, avec le bot administrateur |

### Google

1. Crée un projet sur console.cloud.google.com.
2. Active YouTube Data API v3 et YouTube Analytics API.
3. Configure l'écran de consentement et passe-le en production. En mode test, l'autorisation expire au bout de 7 jours.
4. Crée un identifiant OAuth de type Application de bureau.
5. Lance `python run.py auth-youtube`.

## Quota YouTube

10 000 unités par jour, remises à zéro à minuit heure du Pacifique. L'application en réserve 1 500 pour les nouveaux cours.

| Action | Unités |
|---|---|
| Lister les sous-titres d'une vidéo | 50 |
| Télécharger des sous-titres | 200 |
| Envoyer des sous-titres corrigés | 400 |
| Mettre à jour titre, description et tags | 50 |
| Remplacer la miniature | 50 |
| Publier un extrait | 1 600 |

Une archive déjà transcrite coûte environ 450 unités, soit 17 vidéos par jour (`ARCHIVE_PER_DAY`). Si une publication
s'arrête faute de quota, elle reprend le lendemain sans refaire ce qui est déjà fait.

## Déploiement

`Dockerfile` et `docker-compose.yml` lancent le site et le travail automatique dans un seul conteneur. La base SQLite et
les modèles sont dans le volume `/data`.

```bash
docker compose up -d --build
```

Sur un serveur, les sous-titres doivent passer par l'API YouTube : renseigne les clés Google.

### Railway

`railway.json` et le `Dockerfile` suffisent. Au premier démarrage, l'application part des données embarquées
(`seed/studio-seed.db.gz` : inventaire, transcripts, index et propositions).

1. New Project, Deploy from GitHub repo, `Office1480/Nahman-Studio`.
2. Variables : `ADMIN_PASSWORD`, `PUBLIC_URL` (l'adresse Railway), et `ANTHROPIC_API_KEY` pour les réponses rédigées.
3. Ajoute un volume monté sur `/data`, sinon les validations sont perdues à chaque redéploiement.
4. Settings, Networking, Generate Domain.

Pour rafraîchir les données embarquées : `python tools/export_seed.py`.

## Limites connues

- Les sous-titres automatiques déforment les mots hébreux. Le glossaire (`studio/glossaire.json`) corrige les erreurs
  connues, et il suffit de l'enrichir puis de lancer `python run.py reindex`.
- Les écrans de fin et les fiches n'ont pas d'API : ils restent manuels.
- L'API Analytics ne donne pas le taux de clic des miniatures. Les archives sont donc classées par nombre de vues.
- Extraire un passage passe par yt-dlp, un outil non officiel : YouTube lui oppose par moments une vérification
  anti-robots, y compris depuis une connexion domestique, et bloque souvent les serveurs. L'application ne la contourne
  pas : l'extrait passe en échec avec un bouton pour réessayer. La voie sûre est de déposer le fichier du cours,
  téléchargé depuis YouTube Studio, dans `data/sources/ID_VIDEO.mp4` : il est alors utilisé en priorité.
- Avec le quota gratuit, au plus 5 extraits publiés par jour, en concurrence avec la réoptimisation des archives.
  Le reste attend le lendemain, sans rien perdre.

## Tests et outils

```bash
python -m pytest tests
python tools/try_search.py "tikoun aklali"
python tools/try_detection.py
python tools/build_demo.py
```
