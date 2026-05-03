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
Currently, the tool is configured via the `if __name__ == "__main__":` block in `transcribe.py`. You can run it with:
  
```bash
python transcribe.py
```

---
## 🧠 4. Features & Parameters

### 🚀 4.1. Key Features
- **Real-time Progress Bars**: Both audio preprocessing and Whisper transcription show live progress using `tqdm`.
- **Advanced Audio Cleanup**: Automatically removes digital clicks, fixes clipping, and enhances voice clarity using professional FFmpeg filters.
- **Apple Silicon Optimized**: Uses `mlx-whisper` for lightning-fast performance on Mac.

### 🎛️ 4.2. Parameters (transcribe_mlx)
| Parameter | Default | Description |
|-----------|---------|-------------|
| `file_path` | required | Path to audio file |
| `model` | `Large` | Whisper model: `Large` or `Turbo` |
| `mode` | `balanced` | Preprocessing: `light`, `balanced`, `aggressive` |
| `language` | `it` | Transcription language |
| `use_preprocessing` | `True` | Enable/Disable audio filtering |
| `keep_audio` | `True` | Keep the processed `.mp4` file in `processed_audio/` |

### 🔊 4.3. Preprocessing modes
 - **light** → Minimal filtering, preserves natural sound.
 - **balanced** → (Default) Digital cleanup + voice clarity boost. Recommended for most lectures.
 - **aggressive** → Strong noise reduction for very noisy environments.

### 📁 4.4. Output
* **Transcripts** (`.txt`) are saved in: `transcriptions/`
* **Processed audio** (`.mp4`): saved in: `processed_audio/`
* **Paths**: All folders are created automatically relative to the script directory.
---