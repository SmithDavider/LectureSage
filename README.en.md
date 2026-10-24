# LectureSage

[简体中文](README.md) | [English](README.en.md)

LectureSage is a personal classroom recording and knowledge organization tool. The Android app captures microphone audio and screen content, while the server handles speech recognition, course-material OCR, live subtitle delivery, transcript correction, course summaries, section notes, and editable mind maps.

## Features

- Microphone recording and MediaProjection screen capture on Android
- Persistent upload queues with retry for audio and screenshots
- Real-time and post-class transcription with SenseVoice
- Page, title, and body-text recognition with PaddleOCR
- WebSocket delivery for subtitles, course timelines, and task status
- OpenAI-compatible LLM integration
- OCR-grounded transcript correction
- Course summaries, section notes, and editable mind maps
- Markdown and OPML export

## Architecture

```text
Android App (Kotlin)
  |  HTTP REST / WebSocket
  v
FastAPI server
  |-- SQLite + file storage
  |-- SenseVoice ONNX
  |-- PaddleOCR / OpenVINO
  `-- OpenAI-compatible LLM API
```

The Android app does not run ASR, OCR, or LLM inference. Users enter the server address on first login, and the app stores it locally. Neither the source code nor the APK includes a private server address.

## Repository Layout

```text
app/                Android client
server/             FastAPI server
docs/               API and design documentation
scripts/            Build scripts
gradle/             Gradle Wrapper
```

## Build the Android App

Requirements:

- JDK 21
- Android SDK Platform 35
- Android SDK Build-Tools 36.0.0

Linux / macOS:

```bash
./gradlew :app:assembleDebug
```

Windows:

```powershell
.\gradlew.bat :app:assembleDebug
```

Output:

```text
app/build/outputs/apk/debug/app-debug.apk
```

On first launch, enter your own server URL, username, and password. The server address is not hard-coded in the source or APK defaults.

## Run the Server

Python 3.11 or newer is recommended:

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Create a server account:

```bash
python scripts/create_user.py <username> <password>
```

FFmpeg, SenseVoice, the punctuation model, and the OCR models must be installed and configured separately using the variables in `server/.env.example`. Model files are not included in this repository.

## Admin Console

The server includes a web-based admin console at:

```text
http://<server-host>:8000/admin
```

Admin access is currently determined by the username `admin`. Create the administrator account before the first use:

```bash
python scripts/create_user.py admin <password>
```

The admin console supports:

- Listing, creating, and deleting users
- Listing courses for a selected user
- Viewing course details, transcripts, audio segments, screenshots, and background jobs
- Viewing total and per-user upload/download traffic
- Viewing the 50 most recent traffic records

The admin console can access user course data and transcripts. Deploy it only on a trusted network and serve it over HTTPS.

## Model Setup

This repository contains model integration code and configuration instructions only. It does not include model weights, vocabularies, quantization files, or inference caches. Models can be large and may use different open-source licenses. Download them from their official publishers and review the applicable terms.

### SenseVoice

1. Obtain `SenseVoiceSmall` from the official SenseVoice or FunASR model repository.
2. Place the model outside this repository, for example:

```text
/opt/keji/models/SenseVoiceSmall
```

3. Install the server dependencies and configure:

```text
KEJI_SENSEVOICE_ENABLED=true
KEJI_SENSEVOICE_MODEL=/opt/keji/models/SenseVoiceSmall
KEJI_SENSEVOICE_PUNC_MODEL=iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch
```

FunASR or ModelScope can download the punctuation model into a local cache on first use. Ensure the server process can read and write that cache directory.

### PaddleOCR

This project loads OCR models through PaddleOCR/PaddleX. `PP-OCRv6_medium` is the recommended model, but other compatible models can be selected according to available hardware:

```text
KEJI_OCR_MODEL=PP-OCRv6_medium
KEJI_OCR_ENABLE_HPI=true
KEJI_OCR_CPU_THREADS=2
```

PaddleX HPI mode prepares models in its cache directory. Pre-downloading models is recommended for production deployments. Keep the model cache outside the Git repository.

### Local Transcript Correction Model

The optional Qwen correction service uses a quantized GGUF model. Download the GGUF file from its publisher and start the inference service from a separate deployment directory:

```text
KEJI_CORRECTION_LLM_ENABLED=true
KEJI_CORRECTION_LLM_BASE_URL=http://127.0.0.1:8081/v1
KEJI_CORRECTION_LLM_MODEL=<model-name>
```

Deployers are responsible for model paths, service startup parameters, and license compliance. Do not add model files to this repository.

## API Documentation

See [docs/API_CONTRACT.md](docs/API_CONTRACT.md).

## Data and Privacy

Recognition and generation depend on a server deployed by the user. Users are responsible for:

- Obtaining permission to record classroom audio and capture screen content.
- Choosing trusted speech recognition and LLM services.
- Protecting server data directories and access credentials.
- Removing classroom audio, screenshots, and transcripts that are no longer needed.
