"""Déploie le dernier commit sur le VPS : python tools/deploy_vps.py root@ADRESSE studio.exemple.com

Le code part par SSH et l'image se construit sur le serveur. Seul le dossier /docker/nahman-studio est touché.
Le mot de passe du tableau de bord est créé sur le serveur au premier déploiement, puis jamais modifié.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = Path.home() / ".ssh" / "nahman_deploy"
FOLDER = "/docker/nahman-studio"
SSH = ["ssh", "-i", str(KEY), "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20"]


def main() -> None:
    target, host = sys.argv[1], sys.argv[2]
    archive = ROOT / "dist" / "deploy.tar.gz"
    archive.parent.mkdir(exist_ok=True)
    subprocess.run(["git", "archive", "--format=tar.gz", "-o", str(archive), "HEAD"], cwd=ROOT, check=True)
    subprocess.run([*SSH, target, f"mkdir -p {FOLDER}"], check=True)
    subprocess.run(["scp", "-i", str(KEY), "-o", "StrictHostKeyChecking=accept-new", str(archive),
                    f"{target}:{FOLDER}/deploy.tar.gz"], check=True)
    script = f"""set -e
cd {FOLDER}
tar xzf deploy.tar.gz && rm deploy.tar.gz
touch .env
grep -q '^ADMIN_PASSWORD=' .env || echo "ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')" >> .env
grep -q '^AGENT_TOKEN=' .env || echo "AGENT_TOKEN=$(openssl rand -hex 24)" >> .env
sed -i '/^STUDIO_HOST=/d;/^PUBLIC_URL=/d' .env
echo "STUDIO_HOST={host}" >> .env
echo "PUBLIC_URL=https://{host}" >> .env
chmod 600 .env
cp .env deploy/.env
cd deploy
docker compose -p nahman-studio -f docker-compose.vps.yml up -d --build
docker compose -p nahman-studio -f docker-compose.vps.yml ps
"""
    subprocess.run([*SSH, target, script], check=True)
    archive.unlink()
    link_agent(target, host)
    print(f"Déployé : https://{host}")


def link_agent(target: str, host: str) -> None:
    """Recopie le jeton du programme du PC dans le .env local, sans l'afficher."""
    token = subprocess.run([*SSH, target, f"grep '^AGENT_TOKEN=' {FOLDER}/.env | cut -d= -f2"], check=True,
                           capture_output=True, text=True).stdout.strip()
    path = ROOT / ".env"
    lines = [x for x in path.read_text(encoding="utf-8").splitlines() if not x.startswith(("AGENT_TOKEN=", "AGENT_URL="))]
    path.write_text("\n".join([*lines, f"AGENT_URL=https://{host}", f"AGENT_TOKEN={token}"]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
