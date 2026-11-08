# VRChat TTS Chat

Press **Y** while VRChat is focused -> chat box pops up -> **Enter** sends it to the VRChat chatbox (OSC) and speaks it with AivisSpeech. **Esc** cancels.

## Build the EXE (Windows)
1. Install Python 3.10+ (tick "Add to PATH").
2. Put `vrc_tts_chat.py`, `requirements.txt` and `build.bat` in one folder.
3. Double-click `build.bat`. Result: `dist\VRChatTTSChat.exe`.

## Use
- In VRChat: Action Menu > Options > OSC > **Enabled**.
- Start AivisSpeech (engine default: http://127.0.0.1:10101).
- Launch the EXE, pick a voice and an audio output device.
  To have others hear the TTS, route output to a virtual cable (e.g. VB-Cable) and set VRChat's mic to that cable's output.
- VRChat's own Y chatbox is suppressed while VRChat is focused. Y works normally in other apps.
- Use VRChat in borderless/windowed mode; a fullscreen-exclusive game can hide the pop-up.
- Settings are saved to %APPDATA%\VRChatTTSChat\config.json.

## Notes
- Chatbox is limited to 144 characters (TTS speaks the full text).
- English text read by a Japanese voice model will sound odd; that's an AivisSpeech limitation.
