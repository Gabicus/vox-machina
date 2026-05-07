#!/usr/bin/env python3
"""IRIS Voice Tuner — Interactive GUI for dialing in the voice.

Sliders for all parameters. Hit Generate, listen, tweak, repeat.
Save settings when you like them.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

import numpy as np
import pyworld as pw
import parselmouth
from parselmouth.praat import call
import soundfile as sf

# Reuse core functions from the main pipeline
sys.path.insert(0, str(Path(__file__).parent))
from iris_silas_voice import (
    load_kokoro, generate_raw, apply_formant_shift,
    apply_spectral_tilt, psola_quantize_pass, apply_ffmpeg,
    MODEL_DIR, OUTPUT_DIR, PROFILES
)

SETTINGS_PATH = Path(__file__).parent.parent / "presets" / "iris_default.json"
DEFAULT_TEXT = "Well done. Here come the test results. You are a horrible person."


class IrisTuner:
    def __init__(self, root):
        self.root = root
        self.root.title("IRIS Voice Tuner")
        self.root.geometry("750x780")
        self.root.configure(bg="#1a1a2e")

        self.kokoro = None
        self.generating = False

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background="#1a1a2e", foreground="#e0e0e0", font=("Mono", 10))
        style.configure("Header.TLabel", background="#1a1a2e", foreground="#00d4ff", font=("Mono", 12, "bold"))
        style.configure("TButton", font=("Mono", 10))
        style.configure("TScale", background="#1a1a2e")

        self.params = {}
        self._build_ui()
        if SETTINGS_PATH.exists():
            self._load_settings(str(SETTINGS_PATH))

    def _add_slider(self, parent, label, key, from_, to, default, resolution=0.01, row=0):
        ttk.Label(parent, text=label, style="TLabel").grid(row=row, column=0, sticky="w", padx=5, pady=2)

        var = tk.DoubleVar(value=default)
        self.params[key] = var

        slider = ttk.Scale(parent, from_=from_, to=to, variable=var, orient="horizontal", length=350)
        slider.grid(row=row, column=1, padx=5, pady=2)

        val_label = ttk.Label(parent, text=f"{default:.2f}", style="TLabel", width=8)
        val_label.grid(row=row, column=2, padx=5, pady=2)

        def update_label(*_):
            val_label.config(text=f"{var.get():.2f}")
        var.trace_add("write", update_label)

        return var

    def _build_ui(self):
        # Title
        ttk.Label(self.root, text="IRIS Voice Tuner", style="Header.TLabel").pack(pady=10)

        # Text input
        text_frame = ttk.Frame(self.root)
        text_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(text_frame, text="Text:", style="TLabel").pack(side="left")
        self.text_var = tk.StringVar(value=DEFAULT_TEXT)
        text_entry = ttk.Entry(text_frame, textvariable=self.text_var, width=70)
        text_entry.pack(side="left", padx=5, fill="x", expand=True)

        # Base voice selector
        voice_frame = ttk.Frame(self.root)
        voice_frame.pack(fill="x", padx=15, pady=5)
        ttk.Label(voice_frame, text="Base voice:", style="TLabel").pack(side="left")
        self.voice_var = tk.StringVar(value="af_nova")
        voices = ["af_nova", "af_bella", "af_sky", "af_alloy", "af_kore",
                   "af_nicole", "af_sarah", "af_heart", "af_jessica", "af_river"]
        voice_combo = ttk.Combobox(voice_frame, textvariable=self.voice_var, values=voices, width=15)
        voice_combo.pack(side="left", padx=5)

        # Sliders frame
        slider_frame = ttk.Frame(self.root)
        slider_frame.pack(fill="x", padx=15, pady=10)

        row = 0
        ttk.Label(slider_frame, text="── PITCH QUANTIZATION ──", style="Header.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(5, 2)); row += 1

        self._add_slider(slider_frame, "Speed", "speed", 0.70, 1.10, 0.93, row=row); row += 1
        self._add_slider(slider_frame, "Pitch Std (Hz)", "target_pitch_std", 10, 50, 34, row=row); row += 1
        self._add_slider(slider_frame, "Quantize Passes", "quantize_passes", 0, 6, 2, row=row); row += 1
        self._add_slider(slider_frame, "Transition (ms)", "transition_frames", 1, 8, 2, row=row); row += 1
        self._add_slider(slider_frame, "Hold Min Frames", "hold_min_frames", 1, 30, 6, row=row); row += 1

        ttk.Label(slider_frame, text="── FORMANT / TIMBRE ──", style="Header.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 2)); row += 1

        self._add_slider(slider_frame, "Formant Shift", "formant_shift_ratio", 0.90, 1.25, 1.08, row=row); row += 1
        self._add_slider(slider_frame, "Spectral Tilt (dB)", "spectral_tilt_boost", 0, 6, 1.0, row=row); row += 1
        self._add_slider(slider_frame, "Comb Filter", "comb_filter_strength", 0.0, 1.0, 0.6, row=row); row += 1

        ttk.Label(slider_frame, text="── ROBOT OCTAVE MIX ──", style="Header.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 2)); row += 1

        self._add_slider(slider_frame, "Octave Up Mix", "octave_up_mix", 0.0, 0.4, 0.0, row=row); row += 1
        self._add_slider(slider_frame, "Octave Down Mix", "octave_down_mix", 0.0, 0.4, 0.0, row=row); row += 1
        self._add_slider(slider_frame, "Octave Detune (st)", "octave_detune", 0.0, 2.0, 0.0, row=row); row += 1

        # Buttons
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=15)

        self.gen_btn = ttk.Button(btn_frame, text="Generate", command=self._generate)
        self.gen_btn.pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Play", command=self._play).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Play Reference", command=self._play_ref).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Save Settings", command=self._save_settings).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Load Settings", command=self._load_settings).pack(side="left", padx=5)

        # Status
        self.status_var = tk.StringVar(value="Ready. Hit Generate.")
        ttk.Label(self.root, textvariable=self.status_var, style="TLabel").pack(pady=5)

        # Analysis display
        self.analysis_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.analysis_var, style="TLabel",
                  justify="left", wraplength=700).pack(pady=5)

    def _get_profile(self):
        return {
            "base_voice": self.voice_var.get(),
            "speed": self.params["speed"].get(),
            "lang": "en-us",
            "pitch_floor": 75,
            "pitch_ceiling": 500,
            "target_pitch_std": self.params["target_pitch_std"].get(),
            "quantize_passes": int(self.params["quantize_passes"].get()),
            "transition_frames": int(self.params["transition_frames"].get()),
            "hold_min_frames": int(self.params["hold_min_frames"].get()),
            "quantize_semitones": 1.0,
            "formant_shift_ratio": self.params["formant_shift_ratio"].get(),
            "spectral_tilt_boost": self.params["spectral_tilt_boost"].get(),
            "comb_filter_strength": self.params["comb_filter_strength"].get(),
            "ffmpeg_filters": [
                "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=6",
                "alimiter=limit=0.95:attack=3:release=30",
            ],
        }

    def _apply_octave_mix(self, samples, sr):
        """Mix in pitch-shifted copies for robot harmonics (Wall-E style)."""
        import librosa

        up_mix = self.params["octave_up_mix"].get()
        down_mix = self.params["octave_down_mix"].get()
        detune = self.params["octave_detune"].get()

        if up_mix < 0.01 and down_mix < 0.01:
            return samples

        result = samples.copy().astype(np.float64)

        if up_mix > 0.01:
            shifted_up = librosa.effects.pitch_shift(
                samples.astype(np.float64), sr=sr, n_steps=12 + detune
            )
            result += shifted_up * up_mix

        if down_mix > 0.01:
            shifted_down = librosa.effects.pitch_shift(
                samples.astype(np.float64), sr=sr, n_steps=-(12 + detune)
            )
            result += shifted_down * down_mix

        # Normalize
        peak = np.max(np.abs(result))
        if peak > 0:
            result = result / peak * 0.95

        return result.astype(np.float32)

    def _generate_thread(self):
        try:
            self.status_var.set("Loading TTS model...")
            if self.kokoro is None:
                self.kokoro = load_kokoro()

            profile = self._get_profile()
            text = self.text_var.get().strip()
            if not text:
                self.status_var.set("Enter some text first.")
                return

            self.status_var.set("Generating TTS...")
            samples, sr = generate_raw(self.kokoro, text, profile)

            self.status_var.set("Processing (WORLD + PSOLA)...")
            from iris_silas_voice import apply_praat_processing
            current = apply_praat_processing(samples, sr, profile)

            self.status_var.set("Applying octave mix...")
            current = self._apply_octave_mix(current, sr)

            peak = np.max(np.abs(current))
            if peak > 0:
                current = current / peak * 0.95

            OUTPUT_DIR.mkdir(exist_ok=True)
            out_path = str(OUTPUT_DIR / "iris_tuner_output.wav")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                sf.write(tmp.name, current, sr)
                apply_ffmpeg(tmp.name, out_path, profile)
                os.unlink(tmp.name)

            # Quick analysis
            snd = parselmouth.Sound(out_path)
            pitch = call(snd, "To Pitch", 0.0, 75, 500)
            harmonicity = call(snd, "To Harmonicity (cc)...", 0.01, 75, 0.1, 1.0)
            pm = call(pitch, "Get mean...", 0, 0, "Hertz")
            ps = call(pitch, "Get standard deviation...", 0, 0, "Hertz")
            hnr = call(harmonicity, "Get mean...", 0, 0)

            self.analysis_var.set(
                f"Pitch: {pm:.0f}Hz ±{ps:.0f}  |  HNR: {hnr:.1f}dB  |  "
                f"GLaDOS target: 175Hz ±31, HNR 13.6dB"
            )
            self.status_var.set(f"Done: {out_path}")

        except Exception as e:
            self.status_var.set(f"Error: {e}")
        finally:
            self.generating = False
            self.gen_btn.config(state="normal")

    def _generate(self):
        if self.generating:
            return
        self.generating = True
        self.gen_btn.config(state="disabled")
        threading.Thread(target=self._generate_thread, daemon=True).start()

    def _play(self):
        out_path = str(OUTPUT_DIR / "iris_tuner_output.wav")
        if Path(out_path).exists():
            subprocess.Popen(["xdg-open", out_path])
        else:
            self.status_var.set("Generate first.")

    def _play_ref(self):
        ref_dir = Path(__file__).parent.parent / "audio" / "reference"
        refs = sorted(ref_dir.glob("GLaDOS_*.wav"))
        if refs:
            subprocess.Popen(["xdg-open", str(refs[3])])  # success-1 is a good example
        else:
            self.status_var.set("No reference files found.")

    def _save_settings(self):
        path = filedialog.asksaveasfilename(
            initialdir=str(SETTINGS_PATH.parent),
            initialfile=SETTINGS_PATH.name,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save Voice Settings",
        )
        if not path:
            return

        settings = {
            "voice": self.voice_var.get(),
            "text": self.text_var.get(),
        }
        for key, var in self.params.items():
            settings[key] = var.get()

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(settings, f, indent=2)
        self.status_var.set(f"Saved: {path}")

    def _load_settings(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                initialdir=str(SETTINGS_PATH.parent),
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                title="Load Voice Settings",
            )
        if not path or not Path(path).exists():
            return
        try:
            with open(path) as f:
                settings = json.load(f)
            if "voice" in settings:
                self.voice_var.set(settings["voice"])
            if "text" in settings:
                self.text_var.set(settings["text"])
            for key, var in self.params.items():
                if key in settings:
                    var.set(settings[key])
            self.status_var.set(f"Loaded: {Path(path).name}")
        except Exception as e:
            self.status_var.set(f"Load failed: {e}")


def main():
    root = tk.Tk()
    app = IrisTuner(root)
    root.mainloop()


if __name__ == "__main__":
    main()
