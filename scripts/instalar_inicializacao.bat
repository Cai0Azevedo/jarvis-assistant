@echo off
setlocal EnableExtensions
title JARVIS - Instalador de Inicializacao Automatica

set "PROJECT_DIR=C:\Jarvis\jarvis-assistant"
if exist "%~dp0..\main.py" (
    for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
)

set "TARGET_BAT=%PROJECT_DIR%\scripts\iniciar_jarvis.bat"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_DIR%\JARVIS Assistant.lnk"

if not exist "%TARGET_BAT%" (
    echo [ERRO] Script alvo nao encontrado: "%TARGET_BAT%"
    pause
    exit /b 1
)

if not exist "%STARTUP_DIR%" (
    mkdir "%STARTUP_DIR%"
)

echo [INFO] Registrando atalho de inicializacao para o usuario atual (sem Admin)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%PROJECT_DIR%'; $s.WindowStyle = 1; $s.Description = 'Inicializacao automatica do Projeto JARVIS'; $s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [OK] Atalho criado com sucesso em:
    echo      "%SHORTCUT_PATH%"
    echo [OK] Apontando para: "%TARGET_BAT%"
    echo [OK] Diretorio de trabalho: "%PROJECT_DIR%"
) else (
    echo [ERRO] Nao foi possivel criar o atalho na pasta Startup.
    exit /b 1
)

endlocal
pause
