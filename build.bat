```bat
@echo off
setlocal
cd /d "%~dp0"

REM One-time build
python -m pip install -r requirements.txt || goto :fail
python -m PyInstaller --onefile --noconsole --clean --name VRChatTTSChat vrc_tts_chat.py || goto :fail

copy /y dist\VRChatTTSChat.exe VRChatTTSChat.exe >nul
rmdir /s /q build dist
del /q VRChatTTSChat.spec

REM Generate start.bat
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo start "" "%%~dp0VRChatTTSChat.exe"
    echo start "" "%%~dp0AivisSpeech-Engine\run.exe"
    echo exit
) > start.bat

echo.
echo === VRChatTTSChat.exe is built. Now fetching the AivisSpeech Engine ===

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get_engine.ps1"
if errorlevel 1 (
    echo.
    echo The EXE is fine, but the engine download failed.
    echo Run build.bat again to retry.
    pause
    exit /b 1
)

echo.
echo Done!
echo Generated start.bat.
echo Keep both EXEs and the AivisSpeech-Engine folder together.
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
```