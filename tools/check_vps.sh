#!/bin/sh
# Contrôle du déploiement, à lancer sur le VPS. N'affiche jamais le mot de passe.
URL="https://$(grep '^STUDIO_HOST=' /docker/nahman-studio/.env | cut -d= -f2)"
JAR=$(mktemp)
PASSWORD=$(grep '^ADMIN_PASSWORD=' /docker/nahman-studio/.env | cut -d= -f2)
printf '{"password":"%s"}' "$PASSWORD" | curl -s -o /dev/null -w 'connexion %{http_code}\n' -c "$JAR" \
  -H 'Content-Type: application/json' --data-binary @- "$URL/api/login"
curl -s -b "$JAR" "$URL/api/proposals?status=pending&kind=live" | python3 -c "import sys,json; print('propositions', len(json.load(sys.stdin)))"
curl -s -b "$JAR" "$URL/api/clips" | python3 -c "import sys,json; d=json.load(sys.stdin); print('extraits', d['states'], d['kinds'], 'cours', d['lessons'])"
curl -s "$URL/api/search?q=comment%20sortir%20de%20la%20tristesse" | python3 -c "import sys,json; d=json.load(sys.stdin); print('recherche', d['total_moments'], 'moments, sens', d['semantic'])"
curl -s -o /dev/null -w 'miniature %{http_code}\n' -b "$JAR" "$URL/api/thumbnail/tEenadgsY9k?text=Essai"
rm -f "$JAR"
echo '--- flux YouTube depuis le serveur'
for i in 1 2 3 4; do
  curl -s -o /dev/null -w '%{http_code} ' --max-time 15 'https://www.youtube.com/feeds/videos.xml?channel_id=UCl6DCmZEozwVnxv3MVYbJnA'
  sleep 3
done
echo
echo '--- yt-dlp depuis le serveur'
docker exec nahman-studio-studio-1 python -m yt_dlp --no-warnings --skip-download --print title \
  'https://www.youtube.com/watch?v=tEenadgsY9k' 2>&1 | cut -c1-170 | tail -2
echo '--- memoire'
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep nahman
