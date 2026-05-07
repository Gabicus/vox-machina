# Vox Machina

**Machine voice forge** — an open-source audio processing pipeline that transforms text-to-speech output into stylized, character-driven voices with precise pitch quantization, formant shaping, and harmonic processing.

Built on [Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx) + [WORLD vocoder](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) + [Praat PSOLA](https://github.com/YannickJadoul/Parselmouth). Ships with two sardonic AI overseer presets (IRIS and SILAS) and an interactive GUI tuner.

## What It Does

Takes raw TTS output and applies a multi-stage processing pipeline:

1. **WORLD vocoder** — formant shifting, spectral tilt, aperiodicity reduction (timbre shaping without pitch artifacts)
2. **Harmonic comb filter** — STFT-domain filter that boosts energy at f0 multiples, creating sharp harmonic peaks
3. **Note-based PSOLA** — pitch quantization to semitone grid with hold-and-step cadence (the "autotune" effect)
4. **FFmpeg compression/limiting** — final dynamics processing

The result is a voice with digitally precise pitch stepping, synthetic formant character, and clean harmonic structure.

## Audio Examples

*Coming soon — check the `audio/output/` directory after running the pipeline.*

## Quick Start

### 1. Install System Dependencies

```bash
# Fedora/RHEL
sudo dnf install ffmpeg python3-devel

# Ubuntu/Debian
sudo apt install ffmpeg python3-dev

# macOS
brew install ffmpeg
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- **kokoro-onnx** — lightweight ONNX-based TTS engine (no GPU required)
- **pyworld** — WORLD vocoder for artifact-free timbre manipulation
- **praat-parselmouth** — Python interface to Praat (PSOLA pitch manipulation)
- **soundfile** — WAV I/O
- **librosa** — audio analysis utilities
- **numpy** — numerical processing

### 3. Download Kokoro TTS Model

Kokoro requires two model files (~87MB total). Download from HuggingFace:

```bash
# Create cache directory
mkdir -p ~/.cache/kokoro

# Download model files
wget -O ~/.cache/kokoro/kokoro-v1.0.onnx \
  "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"

wget -O ~/.cache/kokoro/voices-v1.0.bin \
  "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
```

Or manually download from the [kokoro-onnx releases page](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0) and place in `~/.cache/kokoro/`.

### 4. Generate Voice

```bash
# IRIS (female sardonic AI)
python scripts/iris_silas_voice.py iris "Well done. Here come the test results. You are a horrible person."

# SILAS (male sardonic AI)
python scripts/iris_silas_voice.py silas "Noted. Your attempt has been catalogued for future reference."

# Custom output path
python scripts/iris_silas_voice.py iris -o my_output.wav "Your text here"

# From a text file
python scripts/iris_silas_voice.py iris -f script.txt

# Raw TTS (no processing, for comparison)
python scripts/iris_silas_voice.py iris --raw "Text here"

# List available base voices
python scripts/iris_silas_voice.py --list-voices

# Analyze any audio file
python scripts/iris_silas_voice.py --analyze some_file.wav
```

### 5. Interactive Tuner (GUI)

```bash
python scripts/iris_tuner.py
```

Opens a tkinter GUI with real-time sliders for all voice parameters. Generate, listen, tweak, save presets.

**Tuner controls:**
- **Speed** — TTS speaking rate (0.70-1.10)
- **Pitch Std** — target pitch variation in Hz (lower = more monotone)
- **Quantize Passes** — number of PSOLA pitch-snapping passes (more = more robotic)
- **Transition Frames** — frames allowed for pitch transitions
- **Hold Min Frames** — minimum frames before pitch can change (higher = longer notes)
- **Formant Shift** — vocal tract size (>1.0 = smaller/more synthetic)
- **Spectral Tilt** — low/high frequency balance
- **Comb Filter** — harmonic peak sharpness (0.0-1.0)
- **Octave Mix** — blend in pitch-shifted copies for robotic harmonics

Settings save/load as JSON presets via file dialog.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Kokoro TTS                        │
│              (ONNX, CPU inference)                   │
│         Base voice: af_nova / am_onyx / etc          │
└──────────────────────┬──────────────────────────────┘
                       │ raw PCM (24kHz)
                       ▼
┌─────────────────────────────────────────────────────┐
│              WORLD Vocoder (Stage 1)                 │
│                                                      │
│  harvest() → f0 extraction                           │
│  cheaptrick() → spectral envelope                    │
│  d4c() → aperiodicity                               │
│                                                      │
│  Modifications (timbre only, original f0 preserved): │
│    • Formant shift — spectral envelope warping       │
│    • Spectral tilt — hi/lo energy rebalance          │
│    • AP reduction — cleaner harmonics below 5kHz     │
│                                                      │
│  synthesize() → timbre-shaped audio                  │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│           Harmonic Comb Filter (Stage 2)             │
│                                                      │
│  STFT-domain: for each frame, boost energy at        │
│  integer multiples of local f0.                      │
│  Gaussian peaks (30Hz width), 1/h^0.3 decay.         │
│  Creates sharp harmonic structure.                   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│         Note-Based PSOLA (Stage 3)                   │
│                                                      │
│  Praat pitch analysis → semitone quantization        │
│  Detect "notes" (contiguous same-semitone regions)   │
│  Merge notes shorter than hold_min_frames            │
│  Place 2 pitch tier points per note (start + end     │
│  at identical Hz) → forces flat pitch holds          │
│                                                      │
│  Multiple passes reinforce quantization:             │
│    Pass 1: compress range + quantize                 │
│    Pass 2+: re-quantize residual drift               │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              FFmpeg (Stage 4)                         │
│                                                      │
│  • Compressor (threshold=-18dB, ratio=3:1)           │
│  • Limiter (0.95 ceiling)                            │
│  • Optional: EQ, bandpass, effects per profile       │
└──────────────────────┴──────────────────────────────┘
```

## Voice Profiles

### IRIS (Female)

Cold, precise, musical in her cruelty. Pitch-stepped with clear harmonic structure.

| Parameter | Value | Why |
|---|---|---|
| Base voice | af_nova | Closest spectral match (97%+ F1/F2/F3 overlap) |
| Speed | 0.93 | Slightly deliberate pacing |
| Formant shift | 1.08 | Subtly smaller vocal tract = more synthetic |
| Spectral tilt | 1.0 dB | Gentle warmth reduction |
| Comb filter | 0.6 | Sharp harmonic peaks |
| Quantize passes | 2 | Clear pitch stepping without over-roboticizing |
| Hold min frames | 6 | ~30ms minimum note — allows expressive range |
| Target pitch std | 34 Hz | Controlled variation (reference: 31 Hz) |

**Measured output:** Pitch 175Hz +/-31 | HNR 13.6dB | 81% pitch holds | 48+ distinct note changes

### SILAS (Male)

Dry, measured, academic. Tighter pitch range, more compression, deliberate pacing.

| Parameter | Value | Why |
|---|---|---|
| Base voice | am_onyx | Deep, authoritative male voice |
| Speed | 0.88 | Slower, more deliberate |
| Formant shift | 1.03 | Minimal — already has the right timbre |
| Spectral tilt | 1.5 dB | Slightly more bass emphasis |
| Quantize passes | 3 | More robotic stepping |
| Target pitch std | 22 Hz | Tighter range — more monotone |

## How the Pitch Quantization Works

The key insight: the "robotic autotune" effect isn't a vocoder — it's aggressive pitch correction with hold-and-step behavior.

1. **Extract pitch** — Praat analyzes f0 at 5ms resolution
2. **Compress range** — scale pitch variation toward target standard deviation
3. **Snap to grid** — round each pitch point to nearest semitone (or half-semitone, configurable)
4. **Detect notes** — group consecutive frames at the same semitone into "notes"
5. **Merge short notes** — any note shorter than `hold_min_frames` gets absorbed into its neighbor
6. **Build pitch tier** — place exactly 2 Praat pitch points per note (start and end at identical Hz), forcing perfectly flat holds between jumps
7. **PSOLA resynthesis** — Praat's overlap-add reconstructs the audio with the new pitch tier
8. **Repeat** — additional passes reinforce quantization (residual drift from PSOLA gets re-snapped)

This creates the characteristic staircase pitch contour: flat holds interrupted by instant jumps.

## Creating Custom Voice Profiles

1. Start the tuner: `python scripts/iris_tuner.py`
2. Pick a base voice from the dropdown
3. Adjust sliders while generating test phrases
4. Save your preset as a JSON file
5. To use in the CLI pipeline, add your profile to the `PROFILES` dict in `iris_silas_voice.py`

### Preset JSON Format

```json
{
  "voice": "af_nova",
  "text": "Test phrase for preview",
  "speed": 0.93,
  "target_pitch_std": 34.0,
  "quantize_passes": 2.0,
  "transition_frames": 2.0,
  "hold_min_frames": 6.0,
  "formant_shift_ratio": 1.08,
  "spectral_tilt_boost": 1.0,
  "comb_filter_strength": 0.6,
  "octave_up_mix": 0.0,
  "octave_down_mix": 0.0,
  "octave_detune": 0.0
}
```

## Spectral Analysis

The `audio/analysis/` directory contains:

- **spectral_analysis.json** — per-file pitch, formant, centroid, bandwidth, rolloff, and HNR measurements from 11 reference audio files used as the engineering target
- **glados_spectral_template.npz** — averaged WORLD spectral envelope, aperiodicity, MFCC means/stds, and spectral contrast from reference files
- **Spectrogram comparisons** — visual overlays of reference vs output pitch contours and spectrograms

These were used to derive the IRIS profile parameters through iterative spectral matching.

## Available Base Voices (Kokoro)

Kokoro ships with multiple voices. Use `--list-voices` to see all. Key ones:

| Voice | Gender | Character |
|---|---|---|
| af_nova | F | Clear, neutral — best spectral match for IRIS |
| af_bella | F | Warmer, slightly breathier |
| af_sky | F | Brighter, more energetic |
| af_heart | F | Softer, more intimate |
| am_onyx | M | Deep, authoritative — used for SILAS |

All voices work with the processing pipeline. The formant/spectral parameters may need adjustment per voice.

## Full Content Creation Workflow

Vox Machina is two things: a **voice persona** (how the AI thinks and writes) and a **voice engine** (how the AI sounds). The full workflow connects them.

### Step 1: Generate Script with AI Persona

Use the system prompts in `prompts/` to make any LLM write in the IRIS or SILAS voice.

**With Claude:**
```
# Paste the contents of prompts/iris_system_prompt.md as your system prompt, then:
"Write a 20-second voiceover introducing my new app that organizes bookmarks."
```

**With ChatGPT:**
```
# Start a new chat, paste the system prompt from prompts/iris_system_prompt.md, then:
"Write a 30-second narration for a YouTube tech review of wireless earbuds."
```

**With any LLM API:**
```python
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    system=open("prompts/iris_system_prompt.md").read(),
    messages=[{"role": "user", "content": "Write a 15-second product intro for a password manager."}]
)
script = response.content[0].text
```

### Step 2: Synthesize Voice

```bash
# From text directly
python scripts/iris_silas_voice.py iris "Your generated script here"

# From a saved script file
python scripts/iris_silas_voice.py iris -f script.txt -o final_narration.wav

# Use SILAS for the male variant
python scripts/iris_silas_voice.py silas -f script.txt -o silas_narration.wav
```

### Step 3: Fine-tune (Optional)

If the default profile doesn't match your content:

```bash
# Open the GUI tuner
python scripts/iris_tuner.py

# Paste your script text, adjust sliders, generate, listen
# Save your custom preset for reuse
```

### Example: Instagram Reel Pipeline

```bash
# 1. Generate script (save LLM output to file)
echo "You've been doom-scrolling for forty-seven minutes. I've been counting." > reel_script.txt

# 2. Synthesize
python scripts/iris_silas_voice.py iris -f reel_script.txt -o reel_voice.wav

# 3. Layer over video with ffmpeg
ffmpeg -i background_video.mp4 -i reel_voice.wav \
  -filter_complex "[1:a]adelay=1000|1000[voice];[0:a][voice]amix=inputs=2" \
  -c:v copy final_reel.mp4
```

### Creating Your Own Character

1. Write a persona using `prompts/custom_voice_prompt_template.md`
2. Tune a matching voice profile in the GUI tuner
3. Save the preset JSON
4. Add the profile to `PROFILES` in `iris_silas_voice.py`
5. Generate content: LLM writes the words, Vox Machina gives them a voice

### Included Prompts

| File | Character | Description |
|---|---|---|
| `prompts/iris_system_prompt.md` | IRIS | Female sardonic AI — cold, musical, devastating |
| `prompts/silas_system_prompt.md` | SILAS | Male sardonic AI — dry, measured, academic |
| `prompts/custom_voice_prompt_template.md` | (template) | Build your own character voice |

The full persona definition with all seven rhetorical weapons, tone calibration modes, and voice direction is in `SKILL.md`.

## Troubleshooting

### "Model files not found"
Download Kokoro model files (see step 3 above). They go in `~/.cache/kokoro/`.

### Build errors installing pyworld
pyworld requires a C compiler. On Fedora: `sudo dnf install gcc gcc-c++ python3-devel`. On Ubuntu: `sudo apt install build-essential python3-dev`.

### kokoro won't install (blis/AVX512 error)
Use `kokoro-onnx` (the ONNX variant), not the base `kokoro` package. The ONNX version has no blis dependency.

### No sound from tuner Play button
The tuner uses `xdg-open` (Linux) to play WAV files. Ensure you have a default audio player configured. On macOS, change to `open`; on Windows, change to `start`.

### Tuner output sounds different from CLI
Close and relaunch the tuner — Python caches imported modules. If you edited `iris_silas_voice.py` while the tuner was running, it uses the old code until restarted.

## How This Was Built

The processing pipeline was developed through iterative spectral analysis of 11 reference audio files exhibiting the target voice characteristics. Key measurements tracked:

- **Pitch**: mean, std, min/max, range, hold percentage, autocorrelation
- **Formants**: F1, F2, F3 center frequencies
- **Spectral shape**: centroid, bandwidth, rolloff, MFCC distance, spectral contrast per band
- **Harmonic quality**: HNR, spectral flatness, harmonic peak prominence
- **Temporal behavior**: pitch hold percentage, quantization error, note count

The IRIS profile achieves near-identical measured characteristics to the reference target across all tracked metrics (see `audio/analysis/spectral_analysis.json` for raw data).

### Approaches Tried and Abandoned

| Approach | Result | Why Abandoned |
|---|---|---|
| Spectral envelope morphing (blend toward reference template) | Added static/noise artifacts | Log-domain blending destroyed natural envelope dynamics |
| Spectral gating (noisereduce) | Changed overall timbre | Too aggressive even at low strength; MFCC regressed |
| WORLD f0 modification (quantize before synthesis) | Smooth output, stepping lost | WORLD's synthesis interpolates internally |
| Frame-based PSOLA (one pitch point per 5ms frame) | Wobble between frames | Praat interpolates between adjacent points |
| High hold_min_frames (20+) | Too monotone, 2-3 pitches only | Lost all expressiveness |
| 48kHz upsampling | Wrong pitch detection, broken formants | Kokoro outputs 24kHz; WORLD parameters tuned for 24kHz |
| Aperiodicity profile forcing | Timbre regression | Blending AP toward target changed character too much |

The winning approach was the **note-based PSOLA** method: detect contiguous same-semitone regions, merge short notes, place exactly 2 pitch tier points per note at identical Hz values, forcing Praat's overlap-add to hold pitch perfectly flat.

## License

MIT License — see [LICENSE](LICENSE).

## Credits

- [Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx) — the TTS engine
- [WORLD Vocoder](https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder) — timbre manipulation
- [Parselmouth/Praat](https://github.com/YannickJadoul/Parselmouth) — PSOLA pitch processing
- [FFmpeg](https://ffmpeg.org/) — final dynamics processing
