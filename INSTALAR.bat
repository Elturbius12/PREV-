@echo off
chcp 65001 >nul
title PREVE - Instalacion
cd /d "%~dp0"
echo =========================================
echo   PREVE - Instalando / actualizando
echo   (la primera vez tarda unos minutos)
echo =========================================

REM 1. Python 3.11 o superior
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo No se encontro Python en esta PC.
    echo Instalalo desde https://www.python.org/downloads/ ^(version 3.11 o superior^)
    echo IMPORTANTE: marca la casilla "Add python.exe to PATH" al instalar.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo.
    echo PREVE necesita Python 3.11 o superior. Tienes:
    python --version
    echo Descarga la version nueva desde https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 2. Entorno virtual propio (no toca el Python del sistema)
if not exist ".venv\Scripts\python.exe" (
    echo Creando el entorno virtual .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

REM 3. Dependencias
echo Instalando librerias...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo No se pudieron instalar las librerias. Revisa tu conexion a internet.
    pause
    exit /b 1
)

REM 4. Base de datos: crea preve.db e importa la configuracion e historial anteriores (no duplica)
echo Preparando la base de datos...
".venv\Scripts\python.exe" scripts\migrar_a_sqlite.py
if errorlevel 1 echo Aviso: la migracion reporto diferencias. Revisa el detalle de arriba; PREVE igual puede abrirse.

REM 5. Iniciar
echo.
echo Abriendo PREVE...
".venv\Scripts\python.exe" preve_app.py
if errorlevel 1 pause
