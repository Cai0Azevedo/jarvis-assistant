@echo off
setlocal EnableExtensions
title JARVIS - Desinstalador de Inicializacao Automatica

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_DIR%\JARVIS Assistant.lnk"

if exist "%SHORTCUT_PATH%" (
    del /f /q "%SHORTCUT_PATH%"
    if exist "%SHORTCUT_PATH%" (
        echo [ERRO] Nao foi possivel remover o atalho: "%SHORTCUT_PATH%"
        exit /b 1
    ) else (
        echo [OK] Inicializacao automatica desativada. Atalho removido:
        echo      "%SHORTCUT_PATH%"
    )
) else (
    echo [INFO] Nenhum atalho de inicializacao do JARVIS encontrado em:
    echo        "%SHORTCUT_PATH%"
)

endlocal
pause
