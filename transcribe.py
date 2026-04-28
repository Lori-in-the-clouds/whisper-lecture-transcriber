import mlx_whisper
from pathlib import Path
import subprocess
from tqdm import tqdm

def get_audio_duration(input_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(input_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


def get_filter_chain(mode="light"):
    if mode == "light":
        return "highpass=f=80,afftdn=nf=-20,loudnorm"
    elif mode == "aggressive":
        return "highpass=f=80,lowpass=f=7000,afftdn=nf=-30,dynaudnorm,loudnorm"
    else:
        raise ValueError("mode deve essere 'light' o 'aggressive'")


def preprocess_audio(input_path, output_dir, mode="light"):
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_{mode}{input_path.suffix}"

    duration = get_audio_duration(input_path)
    filter_chain = get_filter_chain(mode)

    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-af", filter_chain,
        "-progress", "pipe:1",
        "-nostats",
        "-y",
        str(output_path)
    ]

    print(f"🔊 Preprocessing ({mode}) → {output_path.name}")

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    pbar = tqdm(total=duration, unit="sec", desc=f"Processing ({mode})", dynamic_ncols=True)

    for line in process.stdout:
        if "out_time=" in line:
            time_str = line.strip().split("=")[1]
            h, m, s = time_str.split(":")
            seconds = int(h)*3600 + int(m)*60 + float(s)
            pbar.n = min(seconds, duration)
            pbar.refresh()

    process.wait()
    pbar.n = duration
    pbar.close()

    return output_path


def transcribe_mlx(
    file_path_1,
    output_dir="transcriptions",
    model="Large",
    use_preprocessing=True,
    preprocessing_mode="light",   # 👈 NUOVO PARAMETRO
    keep_processed_audio=True,
    processed_dir="processed_audio",
    language="en"
):
    file_path = Path(file_path_1)

    if model == "Large":
        model_name = "mlx-community/whisper-large-v3-mlx"
    else:
        model_name = "mlx-community/whisper-large-v3-turbo"

    print(f"🎧 Transcribing {file_path.name} using model: {model_name}")

    if use_preprocessing:
        processed_path = preprocess_audio(file_path, processed_dir, preprocessing_mode)
        audio_to_use = processed_path
    else:
        audio_to_use = file_path

    output_folder = Path(output_dir)
    output_folder.mkdir(parents=True, exist_ok=True)

    result = mlx_whisper.transcribe(
        str(audio_to_use),
        path_or_hf_repo=model_name,
        verbose=False,
        language=language,
    )

    text = result["text"]

    # 👇 nome file include modalità
    suffix = f"_{preprocessing_mode}" if use_preprocessing else ""
    output_file = output_folder / f"{file_path.stem}{suffix}.txt"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"✅ Transcription complete! Saved in: {output_file}")

    if use_preprocessing and not keep_processed_audio:
        try:
            processed_path.unlink()
            print(f"🗑️ Deleted processed audio: {processed_path.name}")
        except Exception as e:
            print(f"⚠️ Error deleting file: {e}")


if __name__ == "__main__":
    transcribe_mlx(
        '/Users/lorenzodimaio/Downloads/ciao/scalable_20_04_parte2.m4a',
        model="Large",
        use_preprocessing=True,
        preprocessing_mode="aggressive", 
        keep_processed_audio=True,
        language="it"
    )