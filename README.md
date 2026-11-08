## HOW TO USE!!!
1. Download the release
2. Hit start .bat
3. Wait for aivi speech engine to start running (It should say Uvicorn running on http://localhost:10101 (Press CTRL+C to quit))
4. Hit reload in the app
5. Configure settings mic output ect.
6. In vrchat press Y. It will open a text dialog!

You can download extra voices from aivis hub! https://hub.aivis-project.com/
Make sure to check their licences before use.

## Build the EXE (Windows)
1. Install Python 3.10+ (tick "Add to PATH").
2. Put `vrc_tts_chat.py`, `requirements.txt` and `build.bat` in one folder.
3. Double-click `build.bat`. Result: `dist\VRChatTTSChat.exe`.

## OTHER NOTES
- In VRChat: Action Menu > Options > OSC > **Enabled**.
- To have others hear the TTS, route output to a virtual cable (e.g. VB-Cable) and set VRChat's mic to that cable's output.
- VRChat's own Y chatbox is suppressed while VRChat is focused. Y works normally in other apps.
- Use VRChat in borderless/windowed mode; a fullscreen-exclusive game can hide the pop-up.
- Settings are saved to %APPDATA%\VRChatTTSChat\config.json.

## Notes
- Chatbox is limited to 144 characters (TTS speaks the full text).
