# 📚 Lecture Whisper Transcriber

A simple (but powerful) tool to turn **hours of lecture recordings** into clean, readable text.

Or, more honestly:

> 😁 So you don’t have to re-listen to the same lecture at 1.5x speed ever again.

---

## 🚀 1. What it does

- 🎧 Takes audio files (`.m4a`, `.mp3`, etc.)

- 🔧 Preprocesses audio using FFmpeg filters (noise reduction + normalization)

- 🤖 Transcribes speech using `mlx-whisper` (optimized for Apple Silicon)

- 📝 Saves clean `.txt` transcripts
---

## ⚙️ 2. Installation

- Install Python dependencies:

    ```bash
    pip install -r requirements.txt
    ```
- Install FFmpeg (required):
    ```bash
    brew install ffmpeg
    ```
---
## ▶️ 3. Usage
- Basic example:
  
    ```bash
    python main.py audio.m4a
    ```
- With options:
  
    ```bash
    python main.py audio.m4a --model Large --mode aggressive --lang it
    ```
---
## 🧠 4. Parameters
The tool supports the following parameters:
| Parameter         | Default  | Description                                   |
|------------------|----------|-----------------------------------------------|
| `input`          | required | Path to audio file                            |
| `--model`        | Large    | Whisper model: Large or Turbo                 |
| `--mode`         | light    | Audio preprocessing: light or aggressive      |
| `--lang`         | en       | Language (en, it, etc.)                       |
| `--no-preprocess`| off      | Disable audio preprocessing                   |
| `--keep-audio`   | off      | Keep processed audio file                     |


### 🔊 4.1. Preprocessing modes
 - **light** → preserves natural sound with minimal filtering (recommended for clean audio)  
 - **aggressive** → applies stronger noise reduction, ideal for noisy environments (e.g., classrooms, distant microphones)

### 📁 4.2. Output
* **Transcripts** are saved in: `transcriptions/`
* **Processed audio** (optional): `processed_audio/`
---