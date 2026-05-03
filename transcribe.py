import mlx_whisper
from pathlib import Path
import subprocess
from tqdm import tqdm
import shutil
import sys
import re


# =========================
# 📊 HELPER: WHISPER PROGRESS BAR
# =========================
class WhisperProgressBar:
    """Intercepts Whisper output to update a tqdm bar based on timestamps."""
    def __init__(self, total_duration, desc="Transcribing"):
        self.pbar = tqdm(total=total_duration, unit="sec", desc=desc, dynamic_ncols=True)
        self.total_duration = total_duration
        self._old_stdout = sys.stdout

    def __enter__(self):
        sys.stdout = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._old_stdout
        self.pbar.n = self.total_duration
        self.pbar.refresh()
        self.pbar.close()

    def write(self, data):
        # Search for timestamp in format [MM:SS.mmm --> MM:SS.mmm]
        match = re.search(r"-->\s*(\d{2,3}):(\d{2})\.(\d{3})", data)
        if match:
            m, s, ms = match.groups()
            current_time = int(m) * 60 + int(s) + int(ms) / 1000.0
            self.pbar.n = min(current_time, self.total_duration)
            self.pbar.refresh()
        self._old_stdout.write(data)

    def flush(self):
        self._old_stdout.flush()


# =========================
# ⏱️ AUDIO DURATION (ROBUST)
# =========================
def get_audio_duration(input_path: Path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    try:
        return float(result.stdout.strip())
    except:
        raise RuntimeError("❌ Cannot read audio duration (ffprobe failed)")


# =========================
# 🎛️ AUDIO FILTERS
# =========================
def get_filter_chain(mode="balanced"):
    if mode == "light":
        return "highpass=f=80,adeclick,adeclip,afftdn=nf=-20,treble=g=1,loudnorm"

    elif mode == "balanced":
        # Balanced: removed digital imperfections, moderate denoise, compression and high-frequency air
        return "highpass=f=100,adeclick,adeclip,afftdn=nf=-25,compand=attacks=0.3 0.3:decays=0.8 0.8:points=-70/-60|-20/-14:soft-knee=6,equalizer=f=3000:width_type=h:width=200:g=3,treble=g=2,loudnorm"

    elif mode == "aggressive":
        # Deep cleaning: removed imperfections, strong denoise but with more air to avoid sounding muffled
        return "highpass=f=100,adeclick,adeclip,afftdn=nf=-35,compand=attacks=0.3 0.3:decays=0.8 0.8:points=-70/-60|-20/-14:soft-knee=6,treble=g=3,loudnorm"

    else:
        raise ValueError("mode must be 'light', 'balanced' or 'aggressive'")


# =========================
# 🔊 PREPROCESSING → MP4 (ROBUST)
# =========================
def preprocess_audio(input_path, output_dir, mode="light"):
    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_{mode}.mp4"

    duration = get_audio_duration(input_path)
    filter_chain = get_filter_chain(mode)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),

        # audio only
        "-vn",
        "-ac", "1",
        "-ar", "16000",

        # filters
        "-af", filter_chain,

        # 🔥 MP4 AUDIO SAFE
        "-c:a", "aac",
        "-b:a", "128k",

        str(output_path)
    ]

    print(f"\n🔊 Preprocessing ({mode}) → {output_path.name}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    pbar = tqdm(total=duration, unit="sec", desc=f"🔊 Preprocessing ({mode})", dynamic_ncols=True)

    # Progressive reading of ffmpeg stderr to update the bar
    while True:
        line = process.stderr.readline()
        if not line:
            break
        if "time=" in line:
            try:
                # Time extraction: time=00:00:10.00
                time_str = line.split("time=")[1].split(" ")[0]
                h, m, s = time_str.split(":")
                seconds = int(h)*3600 + int(m)*60 + float(s)
                pbar.n = min(seconds, duration)
                pbar.refresh()
            except:
                pass

    process.wait()
    pbar.close()

    # =========================
    # 🔥 ERROR CHECK
    # =========================
    if process.returncode != 0:
        raise RuntimeError("❌ FFmpeg failed during preprocessing")

    if not output_path.exists():
        raise RuntimeError("❌ Output audio not created")

    print(f"✅ Audio saved: {output_path}")

    return output_path


# =========================
# 🧠 MLX TRANSCRIBER
# =========================
def transcribe_mlx(
    file_path,
    output_dir=None,
    model="Large",
    use_preprocessing=True,
    preprocessing_mode="balanced",
    language="en",
    keep_processed_audio=True,
    processed_dir=None
):
    # Path to the script's directory
    base_dir = Path(__file__).parent.resolve()

    # If not specified, use default folders in the project root
    output_dir = Path(output_dir).resolve() if output_dir else base_dir / "transcriptions"
    processed_dir = Path(processed_dir).resolve() if processed_dir else base_dir / "processed_audio"
    file_path = Path(file_path).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    model_name = (
        "mlx-community/whisper-large-v3-mlx"
        if model == "Large"
        else "mlx-community/whisper-large-v3-turbo"
    )

    print(f"\n🎧 Transcribing: {file_path.name}")
    print(f"🧠 Model: {model_name}")

    # =========================
    # 🔧 PREPROCESS
    # =========================
    if use_preprocessing:
        audio_to_use = preprocess_audio(
            file_path,
            processed_dir,
            preprocessing_mode
        )
    else:
        audio_to_use = file_path

    if not audio_to_use.exists():
        raise FileNotFoundError(f"❌ Audio not found: {audio_to_use}")

    duration = get_audio_duration(file_path)

    # =========================
    # 🧠 WHISPER WITH PROGRESS BAR
    # =========================
    with WhisperProgressBar(duration, desc=f"🧠 Transcribing ({model})") as wpbar:
        result = mlx_whisper.transcribe(
            str(audio_to_use),
            path_or_hf_repo=model_name,
            verbose=False,
            language=language,
            temperature=0.0,
            condition_on_previous_text=False,
        )

    text = result["text"]

    output_file = output_dir / f"{file_path.stem}_{preprocessing_mode}.txt"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"\n✅ Transcription saved → {output_file}")

    # =========================
    # 🧹 OPTIONAL CLEANUP
    # =========================
    if use_preprocessing and not keep_processed_audio:
        try:
            audio_to_use.unlink()
            print(f"🗑️ Deleted: {audio_to_use}")
        except:
            pass


# =========================
# ▶️ RUN
# =========================
if __name__ == "__main__":
    transcribe_mlx(
        "/Users/lorenzodimaio/Downloads/Scalable 23-04 parte 3.m4a",
        model="Large", #Large, Turbo
        use_preprocessing=True,
        preprocessing_mode="aggressive", #light, balanced, aggressive
        language="it",
        keep_processed_audio=True
    )