#!/bin/bash
echo "========================================="
echo "  PREVE - Instalando/actualizando librerias"
echo "  (solo tarda en la PRIMERA vez que lo usas)"
echo "========================================="
if ! command -v python3 &> /dev/null; then
    echo "No se encontro Python 3 instalado en esta Mac/Linux."
    echo "Instala Python desde https://www.python.org/downloads/ y vuelve a correr este script."
    exit 1
fi
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo ""
echo "Abriendo PREVE..."
python3 preve_app.py
