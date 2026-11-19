@echo off
setlocal EnableExtensions
title JARVIS Assistant - Inicializacao Automatica

REM Navega para o diretorio raiz do projeto
if exist "%~dp0..\main.py" (
    cd /d "%~dp0.."
) else (
    cd /d "C:\Jarvis\jarvis-assistant"
)

echo ============================================================================
echo  JARVIS Assistant - Bootstrap Automatico (Subfase 3.3)
echo ============================================================================
echo [INFO] Diretorio de trabalho: %CD%
echo [INFO] Aguardando 5 segundos para estabilizacao da rede (Wi-Fi/Ethernet)...
timeout /t 5 /nobreak > nul 2>&1 || ping -n 6 127.0.0.1 > nul

REM Verifica e ativa o ambiente virtual Python (suporta .venv\ ou venv\)
set "VENV_PYTHON="
if exist ".venv\Scripts\python.exe" (
    echo [INFO] Ativando ambiente virtual em .venv\...
    if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
    set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    echo [INFO] Ativando ambiente virtual em venv\...
    if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
    set "VENV_PYTHON=%CD%\venv\Scripts\python.exe"
) else (
    echo [ERRO] Ambiente virtual nao encontrado em "%CD%\.venv" ou "%CD%\venv".
    echo [DICA] Crie o ambiente virtual com: py -3.12 -m venv venv
    pause
    exit /b 1
)

echo [INFO] Iniciando JARVIS ("%VENV_PYTHON%" main.py)...
"%VENV_PYTHON%" main.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [AVISO] O JARVIS encerrou com codigo de erro %EXIT_CODE%.
    pause
)

endlocal & exit /b %EXIT_CODE%
