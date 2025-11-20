#!/usr/bin/env bash
set -euo pipefail

echo "==> Creando virtualenv en .venv (python3 -m venv)"
python3 -m venv .venv

echo "==> Activando virtualenv"
# Ejecutar con: source scripts/setup_venv.sh o ./scripts/setup_venv.sh
source .venv/bin/activate

echo "==> Actualizando pip, setuptools y wheel"
python -m pip install --upgrade pip setuptools wheel

echo "==> Instalando dependencias desde requirements.txt (esto puede tardar mucho)"
python -m pip install -r requirements.txt

echo "==> Instalando el paquete en modo editable"
python -m pip install -e .

echo "==> Instalación completada. Para activar el venv usa: source .venv/bin/activate"
