#!/usr/bin/env python3
"""IRIS & SILAS — Sardonic AI Voice Pipeline.

GLaDOS-style pitch quantization + formant processing.
Uses af_nova base + WORLD vocoder for artifact-free pitch manipulation.

Key insight from reference analysis (11 GLaDOS files):
- GLaDOS has 40-56% "stable" pitch frames (ours was 14.8%)
- Pitch autocorrelation 0.88-0.97 (ours 0.79) — pitch is "sticky"
- Strong semitone quantization (pitch histogram peaks at integer semitones)
- Within-semitone jitter only 0.09-0.18st (ours 0.21st)
- Transitions happen in 2-4 frames (20-40ms), then pitch HOLDS
- NOT a vocoder — it's aggressive pitch correction (autotune-style)

Usage:
    python iris_silas_voice.py iris "Your text here"
    python iris_silas_voice.py silas "Your text here"
    python iris_silas_voice.py iris -o output.wav "text"
    python iris_silas_voice.py iris --raw "text"
    python iris_silas_voice.py iris -f script.txt
    python iris_silas_voice.py --list-voices
    python iris_silas_voice.py --analyze FILE
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pyworld as pw
import parselmouth
from parselmouth.praat import call
import soundfile as sf

MODEL_DIR = Path.home() / ".cache" / "kokoro"
MODEL_PATH = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODEL_DIR / "voices-v1.0.bin"
OUTPUT_DIR = Path(__file__).parent.parent / "audio" / "output"

# Derived from spectral analysis of 11 GLaDOS reference audio files.
# GLaDOS average: pitch=175.6Hz±31.3, F1=757, F2=1955, F3=3064, HNR=13.6
# af_nova raw: pitch=175±43, F1=731, F2=1866, F3=3043, HNR=13.9
# af_nova is 97%+ spectrally matched — the DIFFERENCE is in temporal pitch behavior.

# GLaDOS reference stats (from analysis of 11 files):
#   - 40-56% of frames are "stable" (< 0.05st frame-to-frame change)
#   - Pitch autocorrelation (lag 1): 0.88-0.97
#   - Within-semitone jitter: 0.09-0.18 semitones std
#   - Transition speed: 2-4 frames (20-40ms) between pitches
#   - Strong semitone quantization (histogram peaks at integers)
#   - Hi/Lo spectral energy ratio: 0.61-0.65 (ours was 0.38 — too bright)

PROFILES = {
    "iris": {
        "base_voice": "af_nova",
        "speed": 0.93,
        "lang": "en-us",
        "pitch_floor": 75,
        "pitch_ceiling": 500,
        # --- Pitch quantization (GLaDOS autotune) ---
        "quantize_semitones": 1.0,
        "quantize_passes": 2,
        "transition_frames": 2,
        "target_pitch_std": 34.0,
        "hold_min_frames": 6,             # ~30ms minimum note (allows more distinct notes)
        # --- Formant / spectral ---
        "formant_shift_ratio": 1.08,
        "spectral_tilt_boost": 1.0,
        # --- Harmonic clarity ---
        "comb_filter_strength": 0.6,
        # --- FFmpeg final chain ---
        "ffmpeg_filters": [
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=6",
            "alimiter=limit=0.95:attack=3:release=30",
        ],
    },
    "silas": {
        "base_voice": "am_onyx",
        "speed": 0.88,
        "lang": "en-us",
        "pitch_floor": 50,
        "pitch_ceiling": 400,
        # --- Pitch quantization ---
        "quantize_semitones": 1.0,
        "quantize_passes": 3,
        "transition_frames": 4,           # slightly softer transitions than IRIS
        "target_pitch_std": 22.0,
        # --- Formant / spectral ---
        "formant_shift_ratio": 1.03,
        "spectral_tilt_boost": 1.5,
        # --- FFmpeg final chain ---
        "ffmpeg_filters": [
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2",
            "equalizer=f=150:t=q:w=1:g=1",
            "equalizer=f=400:t=q:w=1.5:g=-2",
            "equalizer=f=2000:t=q:w=1.5:g=2",
            "equalizer=f=4000:t=q:w=1:g=1",
            "highpass=f=80:p=1",
            "lowpass=f=7500:p=1",
            "alimiter=limit=0.92:attack=2:release=20",
        ],
    },
}


def load_kokoro():
    if not MODEL_PATH.exists() or not VOICES_PATH.exists():
        print(f"Model files not found in {MODEL_DIR}")
        print("Download: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0")
        sys.exit(1)
    from kokoro_onnx import Kokoro
    return Kokoro(str(MODEL_PATH), str(VOICES_PATH))


def generate_raw(kokoro, text, profile):
    samples, sample_rate = kokoro.create(
        text,
        voice=profile["base_voice"],
        speed=profile["speed"],
        lang=profile["lang"],
    )
    return samples, sample_rate


def apply_formant_shift(sp, sr, shift_ratio):
    """Shift formants by resampling the spectral envelope.

    shift_ratio > 1.0 moves formants up (smaller vocal tract = more synthetic).
    Uses spectral envelope warping — same principle as Praat's "Change gender"
    but applied directly to the WORLD spectral envelope.
    """
    if abs(shift_ratio - 1.0) < 0.01:
        return sp

    n_frames, n_freq = sp.shape
    sp_shifted = np.zeros_like(sp)

    freq_indices = np.arange(n_freq)
    warped_indices = freq_indices / shift_ratio

    for i in range(n_frames):
        sp_shifted[i] = np.interp(
            warped_indices, freq_indices, sp[i],
            left=sp[i, 0], right=sp[i, -1]
        )

    return sp_shifted



def apply_comb_filter(audio, sr, f0_contour, time_points, strength):
    """Frequency-domain comb filter that boosts energy at harmonic frequencies.

    For each short frame, identifies the local f0 and boosts STFT bins
    at integer multiples of f0. This creates the sharp harmonic peaks
    visible in GLaDOS spectrograms (peak prominence 9-12x vs our 5-9x).
    """
    hop = 256
    n_fft = 2048
    n_samples = len(audio)

    D = np.fft.rfft(
        np.lib.stride_tricks.sliding_window_view(
            np.pad(audio.astype(np.float64), (n_fft//2, n_fft//2)),
            n_fft
        )[::hop] * np.hanning(n_fft)
    )

    freqs = np.fft.rfftfreq(n_fft, 1.0/sr)
    n_frames_stft = D.shape[0]

    # Interpolate f0 to STFT frame times
    stft_times = np.arange(n_frames_stft) * hop / sr
    f0_interp = np.interp(stft_times, time_points, f0_contour)

    for i in range(n_frames_stft):
        local_f0 = f0_interp[i]
        if local_f0 < 50:
            continue

        # Build harmonic comb: boost at f0, 2*f0, 3*f0, etc.
        n_harmonics = int(sr / 2 / local_f0)
        comb_gain = np.ones(len(freqs))
        harmonic_width_hz = 30  # width of each harmonic peak boost

        for h in range(1, min(n_harmonics + 1, 40)):
            center = h * local_f0
            mask = np.exp(-0.5 * ((freqs - center) / harmonic_width_hz) ** 2)
            comb_gain += mask * strength * (1.0 / (h ** 0.3))  # decay with harmonic number

        D[i] *= comb_gain

    # Overlap-add reconstruction
    frames = np.fft.irfft(D) * np.hanning(n_fft)
    output = np.zeros(n_samples + n_fft)
    for i in range(n_frames_stft):
        start = i * hop
        output[start:start + n_fft] += frames[i]

    result = output[n_fft//2:n_fft//2 + n_samples]
    peak = np.max(np.abs(result))
    if peak > 0:
        result = result / peak * 0.95
    return result.astype(np.float32)


def apply_spectral_tilt(sp, sr, tilt_boost_db):
    """Apply spectral tilt correction — boost lows relative to highs.

    GLaDOS has hi/lo ratio ~0.63, our output was 0.38 (too much high energy).
    This applies a gentle downward slope to the spectral envelope.
    """
    if abs(tilt_boost_db) < 0.1:
        return sp

    n_freq = sp.shape[1]
    freqs = np.linspace(0, sr / 2, n_freq)
    norm_freq = freqs / (sr / 2)
    gain_db = tilt_boost_db * (1 - 2 * norm_freq)
    gain_linear = 10 ** (gain_db / 20.0)

    return sp * gain_linear[np.newaxis, :]



def psola_quantize_pass(audio_in, sample_rate, profile, compress_range=False):
    """Single PSOLA pass: quantize pitch points to semitone grid.

    Uses Praat's overlap-add resynthesis which preserves pitch quantization
    better than WORLD's synthesis. Each pass reinforces the stepping.
    """
    snd = parselmouth.Sound(audio_in, sampling_frequency=sample_rate)
    manipulation = call(snd, "To Manipulation", 0.005,
                        profile["pitch_floor"], profile["pitch_ceiling"])
    pitch_tier = call(manipulation, "Extract pitch tier")
    n_points = call(pitch_tier, "Get number of points")

    if n_points < 5:
        return audio_in

    times_p = []
    values_p = []
    for i in range(1, n_points + 1):
        t = call(pitch_tier, "Get time from index...", i)
        v = call(pitch_tier, "Get value at index...", i)
        times_p.append(t)
        values_p.append(v)

    times_p = np.array(times_p)
    values_p = np.array(values_p)
    mean_hz = np.mean(values_p[values_p > 0])

    # Compress pitch range on first pass
    if compress_range:
        target_std = profile.get("target_pitch_std", 34.0)
        current_std = np.std(values_p)
        if current_std > target_std:
            ratio = target_std / current_std
            values_p = mean_hz + (values_p - mean_hz) * ratio

    # Quantize to semitone grid
    grid = profile.get("quantize_semitones", 1.0)
    semitones = 12 * np.log2(values_p / mean_hz)
    quantized_st = np.round(semitones / grid) * grid
    values_q = mean_hz * (2.0 ** (quantized_st / 12.0))

    # Build NOTE-BASED pitch tier (not frame-based)
    # Each "note" is a contiguous run of the same quantized semitone.
    # We place exactly 2 points per note: start and end at the same Hz.
    # This forces Praat to hold pitch perfectly flat between them.
    trans_ms = max(1, profile.get("transition_frames", 2))
    hold_min = profile.get("hold_min_frames", 20)
    call(pitch_tier, "Remove points between...", 0, times_p[-1] + 1)

    # Detect note boundaries
    notes = []  # (start_time, end_time, hz_value)
    note_start = 0
    for i in range(1, len(quantized_st)):
        if quantized_st[i] != quantized_st[note_start]:
            if (i - note_start) >= 2:  # minimum note length
                notes.append((times_p[note_start], times_p[i-1], values_q[note_start]))
            note_start = i
    # Last note
    if len(times_p) - note_start >= 2:
        notes.append((times_p[note_start], times_p[-1], values_q[note_start]))

    # Merge short notes into neighbors
    merged = []
    for note in notes:
        if merged and (note[1] - note[0]) < hold_min * 0.005:
            # Too short — extend previous note to cover this
            merged[-1] = (merged[-1][0], note[1], merged[-1][2])
        else:
            merged.append(note)

    # Build pitch tier: 2 points per note (start + end at same Hz)
    for start_t, end_t, hz in merged:
        call(pitch_tier, "Add point...", start_t, hz)
        if end_t > start_t + 0.01:
            call(pitch_tier, "Add point...", end_t, hz)

    call([manipulation, pitch_tier], "Replace pitch tier")
    result = call(manipulation, "Get resynthesis (overlap-add)")
    return result.values[0]


def apply_praat_processing(samples, sample_rate, profile):
    """GLaDOS-style processing: WORLD timbre shaping + multi-pass PSOLA pitch quantization.

    Two-stage pipeline:

    Stage 1 — WORLD vocoder (timbre only, NO pitch change):
        - Formant upshift via spectral envelope warping
        - Spectral tilt correction (match GLaDOS hi/lo energy ratio)
        - Aperiodicity reduction (cleaner, more tonal sound)

    Stage 2 — Multi-pass PSOLA pitch quantization:
        - Snaps pitch to semitone grid (the autotune/digital stepping effect)
        - Creates hold-and-step pattern (pitch stays flat then jumps)
        - Each pass reinforces quantization (3 passes reaches GLaDOS-level stability)

    Why this hybrid approach:
        - WORLD excels at timbre manipulation without artifacts
        - But WORLD's synthesis smooths pitch — quantization gets lost
        - PSOLA (overlap-add) preserves pitch quantization faithfully
        - Multiple PSOLA passes drive stability from 14% to 40%+ (GLaDOS: 40-56%)

    Verified against 11 GLaDOS reference files:
        - Stable frames: 40%+ (was 14.8%)
        - Pitch autocorrelation: 0.86+ (was 0.73, GLaDOS: 0.88-0.97)
        - Semitone quantization error: 0.17st (was 0.25, GLaDOS: 0.12-0.19)
    """
    # ===== STAGE 1: WORLD — timbre only (no pitch modification) =====
    audio = samples.astype(np.float64)

    f0, t = pw.harvest(audio, sample_rate,
                       f0_floor=profile["pitch_floor"],
                       f0_ceil=profile["pitch_ceiling"])
    sp = pw.cheaptrick(audio, f0, t, sample_rate)
    ap = pw.d4c(audio, f0, t, sample_rate)

    if np.sum(f0 > 0) < 10:
        return samples

    # Formant shift
    formant_ratio = profile.get("formant_shift_ratio", 1.0)
    sp_shaped = apply_formant_shift(sp, sample_rate, formant_ratio)

    # Spectral tilt
    tilt_boost = profile.get("spectral_tilt_boost", 0.0)
    sp_shaped = apply_spectral_tilt(sp_shaped, sample_rate, tilt_boost)

    # Aperiodicity reduction (cleaner harmonics)
    ap_modified = ap.copy()
    n_freq = ap.shape[1]
    ap_modified[:, :int(n_freq*0.4)] *= 0.6

    # Synthesize with ORIGINAL f0 — timbre changes only
    timbre_shaped = pw.synthesize(f0, sp_shaped, ap_modified, sample_rate)
    timbre_shaped = timbre_shaped.astype(np.float32)

    # ===== STAGE 2: Harmonic comb filter =====
    comb_strength = profile.get("comb_filter_strength", 0.0)
    if comb_strength > 0.01:
        timbre_shaped = apply_comb_filter(timbre_shaped, sample_rate, f0, t, comb_strength)

    # ===== STAGE 3: PSOLA pitch quantization (the autotune effect) =====
    # This is where ALL pitch modification happens — PSOLA preserves it faithfully
    n_passes = profile.get("quantize_passes", 2)
    current = timbre_shaped
    for pass_num in range(n_passes):
        current = psola_quantize_pass(
            current, sample_rate, profile,
            compress_range=(pass_num == 0),
        )

    return current


def apply_ffmpeg(input_path, output_path, profile):
    filter_chain = ",".join(profile["ffmpeg_filters"])
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", filter_chain,
        "-ar", "24000",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FFmpeg error: {result.stderr}")
        sys.exit(1)


def analyze_file(filepath):
    snd = parselmouth.Sound(str(filepath))
    pitch = call(snd, "To Pitch", 0.0, 75, 500)
    formants = call(snd, "To Formant (burg)...", 0.0, 5, 5500, 0.025, 50)
    harmonicity = call(snd, "To Harmonicity (cc)...", 0.01, 75, 0.1, 1.0)

    pm = call(pitch, "Get mean...", 0, 0, "Hertz")
    ps = call(pitch, "Get standard deviation...", 0, 0, "Hertz")
    pr = call(pitch, "Get maximum...", 0, 0, "Hertz", "Parabolic") - call(pitch, "Get minimum...", 0, 0, "Hertz", "Parabolic")
    f1 = call(formants, "Get mean...", 1, 0, 0, "Hertz")
    f2 = call(formants, "Get mean...", 2, 0, 0, "Hertz")
    f3 = call(formants, "Get mean...", 3, 0, 0, "Hertz")
    hnr = call(harmonicity, "Get mean...", 0, 0)

    print(f"  Pitch: {pm:.0f}Hz ±{ps:.0f} range={pr:.0f}")
    print(f"  Formants: F1={f1:.0f} F2={f2:.0f} F3={f3:.0f}")
    print(f"  HNR: {hnr:.1f}dB")
    return {"pitch_mean": pm, "pitch_std": ps, "f1": f1, "f2": f2, "f3": f3, "hnr": hnr}


def list_voices():
    voices = np.load(str(VOICES_PATH))
    print("Available base voices:")
    for name in sorted(voices.files):
        prefix = ""
        if name == PROFILES["iris"]["base_voice"]:
            prefix = " <-- IRIS"
        elif name == PROFILES["silas"]["base_voice"]:
            prefix = " <-- SILAS"
        print(f"  {name}{prefix}")


def synthesize(character, text, output_path=None, raw=False):
    profile = PROFILES[character]
    kokoro = load_kokoro()

    print(f"Generating {character.upper()}...")
    samples, sample_rate = generate_raw(kokoro, text, profile)
    OUTPUT_DIR.mkdir(exist_ok=True)

    if raw:
        out = output_path or str(OUTPUT_DIR / f"{character}_raw.wav")
        sf.write(out, samples, sample_rate)
        print(f"Raw: {out}")
        return out

    # Praat: light pitch compression + formant nudge
    processed = apply_praat_processing(samples, sample_rate, profile)

    peak = np.max(np.abs(processed))
    if peak > 0:
        processed = processed / peak * 0.95

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
        sf.write(tmp_path, processed, sample_rate)

    # EQ shaping
    out = output_path or str(OUTPUT_DIR / f"{character}_output.wav")
    apply_ffmpeg(tmp_path, out, profile)
    os.unlink(tmp_path)

    print(f"Output: {out}")
    analyze_file(out)
    return out


def main():
    parser = argparse.ArgumentParser(description="IRIS & SILAS voice pipeline")
    parser.add_argument("character", nargs="?", choices=["iris", "silas"])
    parser.add_argument("text", nargs="?")
    parser.add_argument("-o", "--output")
    parser.add_argument("-f", "--file")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument("--analyze", metavar="FILE")

    args = parser.parse_args()

    if args.analyze:
        analyze_file(args.analyze)
        return
    if args.list_voices:
        list_voices()
        return
    if not args.character:
        parser.print_help()
        sys.exit(1)

    text = Path(args.file).read_text().strip() if args.file else args.text
    if not text:
        print("Provide text or -f file")
        sys.exit(1)

    synthesize(args.character, text, args.output, args.raw)


if __name__ == "__main__":
    main()
