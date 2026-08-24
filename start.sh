#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_CREATED=false

if [[ ! -d .venv ]]; then
    echo "Création de l'environnement virtuel Python..."
    python3 -m venv .venv
    VENV_CREATED=true
fi

source .venv/bin/activate

if [[ "$VENV_CREATED" == true ]]; then
    echo "Installation des dépendances..."
    pip install -r requirements.txt
fi

if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        echo "Fichier .env créé depuis .env.example"
    else
        echo "Erreur : aucun fichier .env ou .env.example trouvé." >&2
        exit 1
    fi
fi

set -a
source .env
set +a

python manage.py check
python manage.py migrate

echo "----------------------------------"
echo "DJINA Backend"
echo "http://127.0.0.1:8000"
echo "API docs: http://127.0.0.1:8000/api/docs/"
echo "----------------------------------"

python manage.py runserver 127.0.0.1:8000
