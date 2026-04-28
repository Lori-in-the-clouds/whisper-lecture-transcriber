# 📚 Lecture Whisper Transcriber

A simple (but powerful) tool to turn **hours of lecture recordings** into clean, readable text.

Or, more honestly:
> “So you don’t have to re-listen to the same lecture at 1.5x speed ever again.”

---

## 🚀 What it does

- 🎧 Takes audio files (`.m4a`, `.mp3`, `.wav`)
- 🔧 Preprocesses audio using FFmpeg filters
- 🤖 Transcribes speech using `mlx-whisper` (Apple Silicon optimized)
- 📝 Saves clean `.txt` transcripts
- 📊 Shows progress during preprocessing

---

## ⚙️ Requirements

### System
- macOS (Apple Silicon recommended: M1/M2/M3/M4)
- Python 3.10+

### FFmpeg (required)
```bash
brew install ffmpeg