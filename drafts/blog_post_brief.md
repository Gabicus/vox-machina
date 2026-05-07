# Blog Post Brief: Building Vox Machina — Engineering a Sardonic AI Voice from Scratch

## Article Summary

How we reverse-engineered the iconic GLaDOS voice characteristics (without cloning or IP infringement), built a complete open-source voice processing pipeline, created two original sardonic AI personas (IRIS & SILAS), and shipped it all as a public GitHub tool with an interactive GUI tuner. Built entirely with Claude Code + open-source audio tools on a CPU-only Linux machine.

## Target Audience

- AI/ML hobbyists and builders
- Audio engineers curious about voice synthesis
- Claude Code / AI coding tool users
- Open-source contributors
- Content creators wanting custom AI voices

## Article Structure

### 1. The Goal
- Wanted a sardonic AI narrator voice for content creation (Instagram Reels, voiceovers, narration)
- Inspired by GLaDOS archetype but must be legally distinct — no voice cloning, no franchise IP
- Must run locally, CPU-only, fully open source
- Built entirely with Claude Code (Opus) as AI pair programmer on Fedora Linux

#### Full Tech Stack
- **TTS:** Kokoro-82M via kokoro-onnx 0.4.7 (ONNX runtime, CPU inference)
- **Vocoder:** pyworld (WORLD vocoder — harvest, cheaptrick, d4c, synthesize)
- **Pitch manipulation:** praat-parselmouth (Praat PSOLA via Python bindings)
- **Audio analysis:** librosa, parselmouth, numpy
- **Audio I/O:** soundfile (libsndfile)
- **Post-processing:** FFmpeg 7.1.2
- **GUI:** tkinter (Python stdlib)
- **AI coding assistant:** Claude Code (Opus 4) — designed the pipeline, wrote all code, iterated on spectral analysis
- **OS:** Fedora 43 Linux, Python 3.14, no GPU

### 2. Creating the Personas (IRIS & SILAS)
- Designing two original characters with distinct personalities
- The Seven Rhetorical Weapons (with examples)
- Writing the voice skill/system prompts for LLM content generation
- **Files to feature:** `SKILL.md`, `prompts/iris_system_prompt.md`, `prompts/silas_system_prompt.md`

### 3. Choosing the TTS Engine
- Evaluated: Piper, Coqui, Edge-TTS, Kokoro
- Edge-TTS had pitch control removed in 2025 — ruled out
- Coqui/XTTS requires GPU and voice cloning (IP risk) — ruled out
- Piper is fast but limited voice quality — ruled out
- Why Kokoro won: ONNX runtime (CPU-only, no GPU needed), multiple high-quality voices, clean 24kHz output, pip-installable

#### Kokoro Setup (HuggingFace / GitHub)
- Install: `pip install kokoro-onnx>=0.4.7` (NOT `kokoro` base package — that pulls blis which fails on AVX512)
- Model files (~87MB total) from GitHub releases (originally hosted on HuggingFace):
  ```
  mkdir -p ~/.cache/kokoro
  wget -O ~/.cache/kokoro/kokoro-v1.0.onnx \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
  wget -O ~/.cache/kokoro/voices-v1.0.bin \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
  ```
- Source: https://github.com/thewh1teagle/kokoro-onnx (ONNX wrapper), original model by https://huggingface.co/hexgrad/Kokoro-82M
- Model: Kokoro-82M — 82 million parameter TTS, StyleTTS2 architecture, trained on LibriTTS
- Voices file contains 50+ pre-baked voice embeddings (af_nova, am_onyx, af_bella, etc.)
- Usage in code:
  ```python
  from kokoro_onnx import Kokoro
  kokoro = Kokoro("~/.cache/kokoro/kokoro-v1.0.onnx", "~/.cache/kokoro/voices-v1.0.bin")
  samples, sr = kokoro.create("Your text", voice="af_nova", speed=0.93, lang="en-us")
  # Returns numpy array at 24kHz
  ```
- Key gotcha: `kokoro` (base) vs `kokoro-onnx` (what we use) — base package requires blis which fails with gcc AVX512 errors on many Linux systems

#### Base Voice Selection
- Tested 10+ Kokoro voices against reference spectral profile
- af_nova won: F1=731Hz (ref 757), F2=1866Hz (ref 1955), F3=3043Hz (ref 3064) — 97%+ match
- The difference between raw af_nova and the target is almost entirely in temporal pitch behavior, not spectral shape
- This meant we could focus processing on pitch quantization rather than fighting the base timbre
- **Audio to feature:** `test_af_*.wav` files (voice comparison samples, 10 voices)

### 4. The Spectral Analysis Phase
- Analyzed 11 reference audio files with Praat + librosa
- Measured: pitch (mean, std, range), formants (F1/F2/F3), HNR, spectral centroid/bandwidth/rolloff, spectral contrast per band, MFCC distance
- Key discovery: the "effect" isn't a vocoder — it's aggressive pitch correction with hold-and-step behavior
- GLaDOS target stats: 175.6Hz ±31.3, HNR 13.6dB, 40-56% stable pitch frames
- **Files to feature:** `audio/analysis/spectral_analysis.json`
- **Images to feature:**
  - `audio/analysis/glados_detailed_spectrograms.png` — all 11 reference spectrograms
  - `audio/analysis/glados_spectrograms_all.png` — reference overview

### 5. Building the Pipeline (Iteration by Iteration)

#### Iteration 1: Raw TTS + FFmpeg EQ
- Just Kokoro + FFmpeg filters (compression, EQ, bandpass)
- Result: sounded like a filtered podcast, no character
- **Audio:** `iris_raw.wav` (raw), `iris_eq_only.wav` (EQ only)

#### Iteration 2: Formant Shifting with WORLD Vocoder
- Added pyworld for formant manipulation
- Spectral envelope warping (shift_ratio=1.08) + spectral tilt correction
- Result: more synthetic timbre, but pitch was still natural/wavy
- **Audio:** `iris_formant_only.wav`, `iris_formant_noEQ.wav`

#### Iteration 3: Frame-Based PSOLA Pitch Quantization
- Added Praat PSOLA for pitch snapping to semitone grid
- Problem: Praat interpolates between individual frame points → wobble
- Result: slightly stepped but still too smooth
- **Audio:** `iris_v2_stronger.wav`
- **Image:** `audio/analysis/pitch_contour_comparison.png` (early vs reference)

#### Iteration 4: Failed Approaches (The Graveyard)
- Spectral envelope morphing → added static/noise (terrible)
- Spectral gating (noisereduce) → changed timbre, MFCC regressed
- WORLD f0 quantization → smoothed out by synthesis
- 48kHz upsampling → broke pitch detection entirely
- AP profile forcing → MFCC regression to 186
- **This section is the most valuable for other builders — what NOT to do**

#### Iteration 5: The Breakthrough — Note-Based PSOLA
- Key insight: instead of one pitch point per frame, detect "notes" (contiguous same-semitone regions)
- Place exactly 2 Praat pitch tier points per note at identical Hz → forces flat holds
- Merge short notes below minimum duration → prevents chattering
- Multiple passes reinforce quantization
- Result: 81% pitch holds (GLaDOS: 79%), clear staircase contour
- **Audio:** `iris_v3_tuned.wav`, then `iris_output.wav` (final)
- **Image:** `audio/analysis/pitch_contour_v2.png`, `audio/analysis/pitch_contour_v3.png` (showing progression)

#### Iteration 6: Harmonic Comb Filter
- STFT-domain filter boosting energy at f0 multiples
- Brought spectral flatness from 3.4x worse to 1.12x of reference
- Sharp harmonic peaks visible in spectrograms
- **Audio:** `iris_final.wav`

#### Iteration 7: Final Tuning + Compression
- FFmpeg compressor with makeup gain=6 (matched RMS loudness)
- Limiter at 0.95
- Final measured: 175Hz ±31, HNR 13.6dB, 81% holds
- **Audio:** `iris_output.wav` (THE final product)
- **Image:** `audio/analysis/spectrogram_comparison.png` (reference vs final, side by side)
- **Image:** `audio/analysis/glados_vs_iris_comparison.png` (detailed comparison)

### 6. Building the Interactive Tuner
- tkinter GUI with real-time sliders for all parameters
- Save/load presets as JSON via file dialog
- Generate → listen → tweak → repeat workflow
- **Screenshot needed:** tuner GUI (must capture live)
- **File:** `scripts/iris_tuner.py`

### 7. Extracting to Open Source
- Moved from private social pipeline to standalone public repo
- Cleaned dead code (6 abandoned functions removed)
- Added comprehensive README, prompts, presets
- MIT license
- **Link:** https://github.com/Gabicus/vox-machina

### 8. The Full System — Persona + Pipeline
- Six operating modes (persona, ingest, script gen, synthesis, re-voice, tuner)
- LLM generates the words, Vox Machina gives them a voice
- Custom character creation workflow
- **Files:** full SKILL.md, prompt templates

### 9. What's Next
- SILAS voice tuning (IRIS is nearly perfect, SILAS needs the same treatment)
- Claude Code plugin packaging
- Community voice profiles
- Integration with video pipelines (Instagram Reels, YouTube)

---

## Complete Asset List

### Images (Already Have)

| File | Description | Article Section |
|---|---|---|
| `audio/analysis/glados_detailed_spectrograms.png` | All 11 reference spectrograms | Spectral Analysis |
| `audio/analysis/glados_spectrograms_all.png` | Reference overview grid | Spectral Analysis |
| `audio/analysis/glados_vs_iris_comparison.png` | Side-by-side reference vs IRIS | Final Result |
| `audio/analysis/pitch_contour_comparison.png` | Early pitch contour comparison | Iteration 3 |
| `audio/analysis/pitch_contour_v2.png` | Improved pitch contour | Iteration 5 |
| `audio/analysis/pitch_contour_v3.png` | Final pitch contour (staircase visible) | Iteration 5 / Final |
| `audio/analysis/spectrogram_comparison.png` | Reference vs IRIS spectrograms + pitch | Final Result |

### Images (Need to Capture)

| Description | How to Get | Article Section |
|---|---|---|
| Tuner GUI screenshot | Launch tuner, screenshot | Building the Tuner |
| Pipeline architecture diagram | Generate from README text or create | Pipeline section |
| IRIS vs SILAS parameter comparison | Table graphic or screenshot | Persona section |
| Before/after waveform comparison | Generate with matplotlib | Iteration showcase |

### Audio Files (Key Demos — Embed or Link)

| File | Description | Article Section |
|---|---|---|
| `iris_raw.wav` | Raw Kokoro TTS, no processing | Iteration 1 (before) |
| `iris_eq_only.wav` | FFmpeg EQ only | Iteration 1 |
| `iris_formant_only.wav` | Formant shift only | Iteration 2 |
| `iris_v2_stronger.wav` | Frame-based PSOLA attempt | Iteration 3 |
| `iris_v3_tuned.wav` | Note-based PSOLA breakthrough | Iteration 5 |
| `iris_output.wav` | **FINAL product** | Iteration 7 / Hero audio |
| `iris_demo_intro.wav` | Original IRIS demo — Weaponized Helpfulness | Demos section |
| `iris_demo_warmth.wav` | Original IRIS demo — Emotional Feint | Demos section |
| `silas_output.wav` | SILAS demo | SILAS section |
| `silas_demo_intro.wav` | Original SILAS demo — Precision Strike | Demos section |
| `silas_demo_verdict.wav` | Original SILAS demo — Validate-Betray | Demos section |
| `test_af_nova.wav` | Base voice comparison (af_nova raw) | TTS Engine selection |

### Code Snippets to Feature

| What | File | Lines | Description |
|---|---|---|---|
| IRIS profile | `scripts/iris_silas_voice.py` | PROFILES dict | The tuned parameters |
| Note-based PSOLA | `scripts/iris_silas_voice.py` | `psola_quantize_pass()` | The breakthrough algorithm |
| Comb filter | `scripts/iris_silas_voice.py` | `apply_comb_filter()` | Harmonic enhancement |
| Full pipeline | `scripts/iris_silas_voice.py` | `apply_praat_processing()` | The 3-stage pipeline |
| Tuner slider setup | `scripts/iris_tuner.py` | `_build_ui()` | GUI construction |

### Data to Feature

| What | File | Use |
|---|---|---|
| Reference spectral stats | `audio/analysis/spectral_analysis.json` | Before/after comparison tables |
| IRIS preset | `presets/iris_default.json` | Show the final tuned parameters |
| Measured results | From generation output | Pitch, HNR, holds comparison table |

### Comparison Table for Article

| Metric | GLaDOS Reference | IRIS Final | Match % |
|---|---|---|---|
| Pitch mean | 175.6 Hz | 175 Hz | 99.7% |
| Pitch std | 31.3 Hz | 31 Hz | 99.0% |
| F1 | 757 Hz | 752 Hz | 99.3% |
| F2 | 1955 Hz | 1917 Hz | 98.1% |
| F3 | 3064 Hz | 3037 Hz | 99.1% |
| HNR | 13.6 dB | 13.6 dB | 100% |
| Pitch holds | 79% | 81% | 102.5% |

---

## Missing Assets to Create Before Writing

1. **Tuner GUI screenshot** — launch `python3 ~/Desktop/Projects/vox-machina/scripts/iris_tuner.py` and capture
2. **Pipeline architecture diagram** — clean version of the ASCII art from README
3. **Before/after waveform** — matplotlib plot showing raw vs processed waveform
4. **Iteration audio player embed** — if WordPress supports audio embeds, include key WAVs at each stage

## WordPress Post Notes

- Use Victor's voice (via wordpress-post skill + brand-voice)
- Heavy on visuals — spectrograms tell the story
- Audio embeds at each iteration stage (raw → formant → PSOLA → final)
- Code snippets for the breakthrough moments
- Link to GitHub repo throughout
- "What we tried and abandoned" section is the most unique/valuable content
- End with open-source call to action (try it, contribute profiles, etc.)
