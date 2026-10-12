# Montage Studio

[Website and downloads](https://mysticalg.github.io/Montage-Studio/) · [Releases](https://github.com/mysticalg/Montage-Studio/releases) · [X extension](https://github.com/mysticalg/Montage-Studio-X-Extension)

Turn a folder of clips and a music track into a finished montage, locally on your computer.

![Montage Studio](docs/assets/montage-hero.webp)

## What it does

- Randomly selects 1–5000 clips, tracks content hashes and avoids reusing clips until you reset the cycle.
- Uses a single audio file or a random track from a folder. Match the full audio length and automatically add more unused clips.
- Aligns edits with detected beats, or accepts a manual BPM.
- Adds crossfades, optical-flow frame interpolation, fixed-length random excerpts and ping-pong loops with eased reversals.
- Optionally inserts evenly spaced stills with configurable duration and smooth zoom.
- Exports 16:9, 9:16 or square video at 720p, 1080p, 1440p or 2160p; optionally selects only clips matching the output ratio.
- Upscales small clips with local Real-ESRGAN before looping and interpolation. GPU/Vulkan support is required; speed and quality depend on the GPU, source and model.
- Generates optional captions and hashtags through a local Ollama model.
- Saves MP4s automatically. Library thumbnails, Download MP4 and Show in folder make exports easy to retrieve.
- Optionally uploads, posts or saves native X schedules through the paired extension in your signed-in Chrome or Edge profile. Rendering never requires an X account.

## Downloads and requirements

Download a native package from [Releases](https://github.com/mysticalg/Montage-Studio/releases/latest). Python and the desktop UI runtime are bundled. **FFmpeg and FFprobe must be installed separately and available on PATH.** Packages contain no clips, accounts, saved schedules, pairing codes or databases.

| Platform | Package | Requirements |
| --- | --- | --- |
| Windows | x64 portable ZIP | Windows 10/11, FFmpeg, Microsoft Edge WebView2 Runtime |
| macOS | Apple Silicon or Intel app ZIP | macOS 13+; matching architecture; FFmpeg |
| Linux | x64 tar.gz | Ubuntu 22.04+ or compatible desktop distribution; FFmpeg and Qt system libraries |

Windows: extract the ZIP and run **Launch Montage Studio.cmd**, or `Montage Studio/Montage Studio.exe`. Install FFmpeg with `winget install Gyan.FFmpeg`; restart the app after installation. WebView2 is normally present on Windows 11; install [Microsoft's Evergreen Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) if missing.

macOS: extract the ZIP and drag **Montage Studio.app** into Applications. Install FFmpeg with `brew install ffmpeg`. Builds are unsigned and not notarized: macOS may require the explicit **Open Anyway** control in Privacy & Security for this downloaded app. Do not disable Gatekeeper globally. AI remains optional and requires compatible GPU support.

Linux: extract the tar.gz and run `./Launch Montage Studio.sh`. On Ubuntu/Debian, `sh install-linux-dependencies.sh` installs FFmpeg, native file chooser and required system libraries. Other distributions need their equivalent packages. A graphical session is required; GPU AI also requires a working Vulkan driver. Linux targets x86_64 and glibc 2.35+.

SHA-256 sidecars accompany downloads. These identify the published files; they do not constitute code signing.

## First montage

1. In **Create**, add one or more video folders, and choose a music file or folder.
2. Set the clip count, format, size and optional effects. Use Scan to see unused clips.
3. Click Build. Rendering continues if you close the desktop window; reopen to see progress.
4. In **Library**, play the output, download a copy or reveal the saved file. Files are already saved before you open them.

The local backend runs on `http://127.0.0.1:8765`. It binds only to loopback. A second app launch attaches to the existing service rather than interrupting it. Do not share pairing codes or expose the service to the network.

## Where your files live

- Windows: `%LOCALAPPDATA%\Montage Studio`
- macOS: `~/Library/Application Support/Montage Studio`
- Linux: `$XDG_DATA_HOME/montage-studio` (defaults to `~/.local/share/montage-studio`)

Rendered videos are in `exports/` inside that folder. The library shows the exact path. Existing portable installs with `data/studio.sqlite3` keep their adjacent data folder. Set `MONTAGE_DATA` to use another location and `MONTAGE_PORT` to use another local port; the extension currently pairs with port 8765 only. Do not move or replace data while renders or browser actions are active.

## Optional Ollama and X

Install [Ollama](https://ollama.com/), pull a model such as `ollama pull llama3.1:8b`, and choose it in Settings. Captions use the filenames and your notes unless you explicitly enable a vision model. Text generation stays on the configured local Ollama endpoint.

For X, load the included `x-extension` folder as an unpacked extension in Chrome or Edge. In Settings choose **Extension — your logged-in browser**, enter your expected account, generate a pairing code and pair through the popup. Verify the account, enable queue processing, then queue the desired upload, post or schedule. [Full instructions](x-extension/README.md).

Posting clicks X's final Post button; scheduling saves to X's native Scheduled list. The extension stops on login challenges, security/rate warnings and uncertain results. English X controls are required and may change. No API credits are used by extension posting; platform rules and upload limits still apply. Do not automate spam or publish media you have no right to share. The app does not determine whether media is eligible for publication.

The optional X API mode requires your own developer access and may incur fees. OAuth secrets use Windows DPAPI or the operating system keychain; Linux needs an unlocked compatible Secret Service. Managed-browser mode uses a separate browser profile and may not support third-party Google login. The extension uses your ordinary logged-in browser instead.

## Run and build from source

Python 3.12 recommended; Node.js 22 for the UI. Install FFmpeg/FFprobe and the platform prerequisites above.

```sh
python -m venv .venv
# Activate .venv using the command appropriate to your shell.
python -m pip install -r requirements.txt -r desktop-requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
python scripts/fetch-ai-tools.py
python desktop.py
```

`python server.py` runs the local-browser version. On Linux the desktop uses PySide6/Qt; macOS uses Cocoa/WebKit; Windows uses WebView2. The optional AI helper is fetched only during setup/build, not during rendering.

```sh
python -m unittest discover -p 'test_*.py'
npm ci --prefix x-extension --ignore-scripts
npm test --prefix x-extension
python build-desktop.py
python scripts/smoke-frozen.py
python scripts/package-release.py
```

Build on the target OS. GitHub Actions builds Windows x64, Linux x64, macOS Intel and macOS Apple Silicon independently, checks a neutral packaged render and the actual desktop window, then publishes all packages together for a `v*` tag. GPU inference tests run locally on supported hardware; cloud runners skip GPU inference. They still test dimensions, tool discovery, cancellation and rendering. No private media is used in CI.

## License and privacy

Application source: [MIT](LICENSE). Bundled third-party components and AI model weights retain their own licenses; see [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt), `tools/realesrgan/LICENSE*`, and package runtime-license notices. FFmpeg is a separate dependency with its own license. There is no analytics or hosted media service. GitHub hosts the marketing site and source; it does not receive your local montage files.
