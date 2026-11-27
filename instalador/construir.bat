@echo off
REM Construye PREVE_Setup.exe (requiere: pip install pyinstaller, e Inno Setup 6)
cd /d "%~dp0\.."
python -m PyInstaller --noconfirm --clean --windowed --name PREVE ^
  --collect-all statsforecast --collect-all catboost --collect-all lightgbm ^
  --collect-submodules plugins --collect-submodules statsmodels --collect-submodules scipy ^
  --hidden-import preve_extractor --hidden-import generar_resultados_articulo ^
  --add-data "dashboard_template.html;." --add-data "formulacion_compras.md;." ^
  --exclude-module playwright --exclude-module pytest ^
  preve_app.py || exit /b 1
set ISCC="%LOCALAPPDATA%ProgramsInno Setup 6ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles(x86)%Inno Setup 6ISCC.exe"
%ISCC% instalador\preve.iss
