"""Lance l'application depuis n'importe quel dossier : python run.py serve"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio.__main__ import main  # noqa: E402

main()
