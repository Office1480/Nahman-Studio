"""Saisie masquée de clés : elles vont directement dans .env, sans s'afficher ni passer par le chat.

Usage : python tools/set_secret.py RAILWAY_API_TOKEN ANTHROPIC_API_KEY
"""
import getpass
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"

for name in sys.argv[1:]:
    value = getpass.getpass(f"Colle {name} puis Entree (rien ne s'affiche, c'est normal ; Entree seul pour passer) : ").strip()
    if not value:
        print(f"{name} : passe.")
        continue
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [line for line in lines if not line.startswith(f"{name}=")] + [f"{name}={value}"]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{name} : enregistre dans .env.")
print("C'est fini, tu peux dire a Claude que c'est fait.")
