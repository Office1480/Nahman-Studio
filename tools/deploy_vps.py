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
    print(f"Déployé : https://{host}")


if __name__ == "__main__":
    main()
