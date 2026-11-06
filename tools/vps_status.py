"""État de l'application, à lancer dans le conteneur : docker exec -i CONTENEUR python - < tools/vps_status.py"""
from studio import clips, config, db, shabbat

settings = config.load()
db.init(settings.db_path)
print("chabbat", shabbat.is_blocked(settings), "| réouverture", shabbat.next_opening(settings))
print("programme du PC", clips.agent_status(settings))
print("clé Claude", settings.has_claude, "| YouTube", settings.has_youtube)
