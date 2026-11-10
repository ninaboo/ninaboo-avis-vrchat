**JP**
## 使い方!!!

1. Releaseからファイルをダウンロードします。
2. `start.bat` を実行します。
3. Aivis Speech Engineが起動するまで待ちます。
   （`Uvicorn running on http://localhost:10101 (Press CTRL+C to quit)` と表示されれば起動完了です）
4. アプリ内の「Reload」を押します。
5. マイクや音声出力などの設定を行います。
6. VRChat内で **Yキー** を押すと、テキスト入力ダイアログが開きます！

追加の音声はAivis Hubからダウンロードできます！
https://hub.aivis-project.com/

使用する前に、必ず各音声のライセンスを確認してください。

## EXEのビルド方法（Windows）

1. Python 3.10以降をインストールします（インストール時に「Add Python to PATH」にチェックを入れてください）。
2. `vrc_tts_chat.py`、`requirements.txt`、`build.bat`、`get_engine.ps1` を同じフォルダに配置します。
3. `build.bat` をダブルクリックして実行します。

ビルドが完了すると、`dist\VRChatTTSChat.exe` が生成されます。

## その他の設定・注意事項

- オプションでSST機能を追加しました。Qwen ASRを使用します。
- VRChat内で「アクションメニュー」→「オプション」→「OSC」→ **「Enabled」** を有効にしてください。
- 他のユーザーにもTTS音声を聞かせるには、VB-Cableなどの仮想オーディオケーブルに音声出力をルーティングし、VRChatのマイク入力をそのケーブルの出力に設定してください。
- VRChatがアクティブな状態では、VRChat標準のYキーによるチャットボックスは無効化されます。他のアプリでは通常どおりYキーを使用できます。
- VRChatはボーダーレスウィンドウモード、またはウィンドウモードで使用してください。フルスクリーン（排他モード）ではポップアップが表示されない場合があります。
- 設定は `%APPDATA%\VRChatTTSChat\config.json` に保存されます。

## 追加の注意事項!!!

- VRChatのチャットボックスには144文字の文字数制限があります。ただし、TTSは入力された全文を読み上げます。


**ENG**
## HOW TO USE!!!
1. Download the release
2. Hit start.bat
3. Wait for aivi speech engine to start running (It should say Uvicorn running on http://localhost:10101 (Press CTRL+C to quit))
4. Hit reload in the app
5. Configure settings mic output ect.
6. In vrchat press Y. It will open a text dialog!

You can download extra voices from aivis hub! https://hub.aivis-project.com/
Make sure to check their licences before use.

## Build the EXE (Windows)
1. Install Python 3.10+ (tick "Add to PATH").
2. Put `vrc_tts_chat.py`, `requirements.txt`, `build.bat` and `get_engine.ps1` in one folder.
3. Double-click `build.bat`. Result: `dist\VRChatTTSChat.exe`.

## Other notes
- Optional SST option added. Uses Qwen ASR
- In VRChat: Action Menu > Options > OSC > **Enabled**.
- To have others hear the TTS, route output to a virtual cable (e.g. VB-Cable) and set VRChat's mic to that cable's output.
- VRChat's own Y chatbox is suppressed while VRChat is focused. Y works normally in other apps.
- Use VRChat in borderless/windowed mode; a fullscreen-exclusive game can hide the pop-up.
- Settings are saved to %APPDATA%\VRChatTTSChat\config.json.

## More Notes!!!
- Chatbox is limited to 144 characters (TTS speaks the full text).
