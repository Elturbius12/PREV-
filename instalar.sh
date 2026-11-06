#!/usr/bin/env bash
# PREVÉ — instalación / actualización e inicio (Linux y macOS).
set -euo pipefail
cd "$(dirname "$0")"

echo "========================================="
echo "  PREVÉ - Instalando / actualizando"
echo "========================================="

# 1. Python 3.11 o superior
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "No se encontró Python. Instálalo (3.11 o superior) desde https://www.python.org/downloads/"
    exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "PREVÉ necesita Python 3.11 o superior. Tienes: $("$PY" --version)"
    exit 1
fi
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    echo "Falta tkinter (la interfaz gráfica). En Debian/Ubuntu: sudo apt install python3-tk"
    exit 1
fi

# 2. Entorno virtual propio
if [ ! -x ".venv/bin/python" ]; then
    echo "Creando el entorno virtual .venv ..."
    "$PY" -m venv .venv
fi
VPY=".venv/bin/python"

# 3. Dependencias
echo "Instalando librerías..."
"$VPY" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VPY" -m pip install --quiet --disable-pip-version-check -r requirements.txt

# 4. Base de datos (idempotente: no duplica lo ya importado)
echo "Preparando la base de datos..."
"$VPY" scripts/migrar_a_sqlite.py || echo "Aviso: la migración reportó diferencias; PREVÉ igual puede abrirse."

# 4b. Navegador de Playwright (solo hace falta si usas "Descargar desde Negosy").
#     pip instala la librería pero NO el navegador: hay que descargarlo aparte, una sola vez.
if [ ! -d "$HOME/.cache/ms-playwright" ]; then
    echo "Descargando el navegador para la integración con Negosy (una sola vez, puede tardar unos minutos)..."
    "$VPY" -m playwright install chromium || echo "Aviso: no se pudo descargar el navegador de Playwright. \"Descargar desde Negosy\" no funcionará hasta correr: $VPY -m playwright install chromium"
fi

# 5. Iniciar
echo "Abriendo PREVÉ..."
exec "$VPY" preve_app.py
