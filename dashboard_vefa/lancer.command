#!/bin/bash
# Double-clic sur ce fichier : installe Flask (une seule fois), démarre le comparateur et ouvre le navigateur.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 est introuvable."
  echo "Installez-le depuis https://www.python.org/downloads/ (ou lancez : xcode-select --install), puis relancez ce fichier."
  read -n 1 -s -r -p "Appuyez sur une touche pour fermer."; exit 1
fi
if [ ! -d .venv ]; then
  echo "Première installation (quelques secondes)..."
  python3 -m venv .venv || { echo "Création de l'environnement impossible."; read -n 1 -s -r; exit 1; }
fi
source .venv/bin/activate
pip install -q -r requirements.txt || { echo "Installation de Flask impossible (connexion Internet ?)."; read -n 1 -s -r; exit 1; }
(sleep 2; open "http://127.0.0.1:5050") &
echo "Comparateur lancé sur http://127.0.0.1:5050 — fermez cette fenêtre ou faites Ctrl+C pour arrêter."
python app.py
