import mlx_whisper
from pathlib import Path
import subprocess
from tqdm import tqdm
import shutil
import sys
import re

from output_naming import transcript_output_stem


RNNOISE_MODEL_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "rnnoise"
    / "somnolent-hogwash.rnnn"
)


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


def get_audio_sample_rate(input_path: Path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate", "-of", "default=nw=1:nk=1",
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    sample_rate = result.stdout.strip()
    if result.returncode != 0 or not sample_rate.isdigit():
        raise RuntimeError("❌ Cannot read audio sample rate (ffprobe failed)")
    return sample_rate


def get_audio_channel_count(input_path: Path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=channels", "-of", "default=nw=1:nk=1",
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    channels = result.stdout.strip()
    if result.returncode != 0 or not channels.isdigit():
        raise RuntimeError("❌ Cannot read audio channel count (ffprobe failed)")
    return int(channels)


def get_best_channel_filter(input_path: Path, analysis_seconds=120):
    """Select a clearly cleaner stereo channel; otherwise keep the normal downmix."""
    if get_audio_channel_count(input_path) != 2:
        return ""

    import numpy as np

    sample_rate = 16000
    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-t", str(analysis_seconds),
            "-i", str(input_path), "-vn", "-ac", "2", "-ar", str(sample_rate),
            "-f", "f32le", "-",
        ],
        capture_output=True,
    )
    if completed.returncode != 0:
        return ""
    samples = np.frombuffer(completed.stdout, dtype="<f4")
    if samples.size < sample_rate or samples.size % 2:
        return ""
    samples = samples.reshape(-1, 2)
    frame, hop = int(0.2 * sample_rate), int(0.1 * sample_rate)

    def channel_quality(channel):
        count = 1 + (len(channel) - frame) // hop
        windows = np.lib.stride_tricks.as_strided(
            channel,
            shape=(count, frame),
            strides=(channel.strides[0] * hop, channel.strides[0]),
        )
        levels = 20 * np.log10(np.sqrt(np.mean(windows.astype(np.float64) ** 2, axis=1)) + 1e-12)
        noise = float(np.median(levels[levels <= np.percentile(levels, 20)]))
        speech = float(np.median(levels[levels >= np.percentile(levels, 60)]))
        return speech - noise + 0.25 * speech

    qualities = [channel_quality(samples[:, index]) for index in range(2)]
    if abs(qualities[0] - qualities[1]) < 0.5:
        return ""
    return f"pan=mono|c0=c{int(np.argmax(qualities))}"


# =========================
# 🎛️ AUDIO FILTERS
# =========================
def get_filter_chain(mode="balanced"):
    if mode == "light":
        # Preserve the voice and only remove low-frequency rumble.  This is a
        # safe default for recordings that are already reasonably clear.
        return "highpass=f=70,loudnorm=I=-18:LRA=11:TP=-1.5"

    elif mode == "balanced":
        # Conservative adaptive cleanup. Smooth gain changes preserve speech
        # consonants while removing more stationary hiss than the light mode.
        return ("highpass=f=80,lowpass=f=10000,"
                "afftdn=nr=10:nf=-42:tn=1:ad=0.85:gs=12,"
                "loudnorm=I=-18:LRA=9:TP=-1.5")

    elif mode == "aggressive":
        # RNNoise operates at 48 kHz and is trained to separate speech from
        # recording noise. Keep it opt-in because strong neural denoising can
        # alter voices when the source is already clean.
        if not RNNOISE_MODEL_PATH.is_file():
            raise RuntimeError(f"RNNoise model not found: {RNNOISE_MODEL_PATH}")
        model_path = RNNOISE_MODEL_PATH.as_posix().replace("'", r"\'")
        return ("aresample=48000,highpass=f=95,lowpass=f=9000,"
                f"arnndn=m='{model_path}':mix=0.75,"
                "afftdn=nr=16:nf=-43:tn=1:ad=0.95:gs=16,"
                "loudnorm=I=-18:LRA=7:TP=-1.5")

    else:
        raise ValueError("mode must be 'light', 'balanced' or 'aggressive'")


def get_preprocessing_filter_chain(input_path: Path, mode="balanced"):
    filters = []
    if mode in {"balanced", "aggressive"}:
        channel_filter = get_best_channel_filter(input_path)
        if channel_filter:
            filters.append(channel_filter)
    filters.append(get_filter_chain(mode))
    return ",".join(filters)


# =========================
# 🔊 PREPROCESSING → LOSSLESS FLAC
# =========================
def preprocess_audio(input_path, output_dir, mode="light"):
    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_{mode}.flac"

    duration = get_audio_duration(input_path)
    sample_rate = get_audio_sample_rate(input_path)
    filter_chain = (
        f"{get_preprocessing_filter_chain(input_path, mode)},aresample={sample_rate},"
        f"aformat=sample_fmts=s16:sample_rates={sample_rate},asetnsamples=n=4096:p=0"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),

        # audio only
        "-vn",
        "-ac", "1",
        "-ar", sample_rate,

        # filters
        "-af", filter_chain,

        # Keep the preprocessing generation lossless.
        "-c:a", "flac",
        "-compression_level", "5",

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
    processed_dir=None,
    initial_prompt=None,
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
            condition_on_previous_text=True,
            initial_prompt=initial_prompt,
        )

    text = result["text"]

    output_file = output_dir / f"{transcript_output_stem(file_path.stem, language, use_preprocessing, preprocessing_mode)}.txt"

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

    return output_file


# =========================
# ▶️ RUN
# =========================
if __name__ == "__main__":
    transcribe_mlx(
        "/Users/lorenzodimaio/Downloads/Big data 22-09 parte 1.m4a",
        model="Large", #Large, Turbo
        use_preprocessing=True,
        preprocessing_mode="balanced", #light, balanced, aggressive
        language="en",
        keep_processed_audio=True
    )
