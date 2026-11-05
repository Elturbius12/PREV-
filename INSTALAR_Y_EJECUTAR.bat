@echo off
title PREVE - Yuju Store
echo =========================================
echo   PREVE - Instalando/actualizando librerias
echo   (solo tarda en la PRIMERA vez que lo usas)
echo =========================================
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo No se encontro Python instalado en esta PC.
    echo Antes de continuar, instala Python desde https://www.python.org/downloads/
    echo IMPORTANTE: al instalar, marca la casilla "Add python.exe to PATH".
    echo Luego vuelve a hacer doble clic en este archivo.
    pause
    exit /b
)
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo.
echo Abriendo PREVE...
python preve_app.py
pause
