"""Turn-based voice I/O: Gemini TTS out, microphone + Gemini transcription in.

Listening is hands-free: recording starts when speech is detected and stops
after a short silence. Speech output runs on a background thread so the
browser keeps working while the agent talks.
"""
from __future__ import annotations

import audioop
import io
import logging
import os
import queue
import subprocess
import threading
import time
import wave

from google.genai import types

from . import llm

log = logging.getLogger(__name__)

RATE = 16000
CHUNK = 1600          # 100 ms of 16-bit mono
# Free-tier TTS allows ~3 requests/minute *per model*, so rotate through several
# and fall back to the local speech-dispatcher (spd-say) if all are exhausted.
TTS_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_TTS_MODELS",
    "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts",
).split(",") if m.strip()]
TTS_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")
_tts_cooldown: dict[str, float] = {}
# Transcription is on the critical path of every turn, so it uses the fastest model first.
STT_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_STT_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]


# ---------------------------------------------------------------- speaking

def synthesize(text: str, models: list[str] | None = None) -> tuple[bytes, str]:
    last = None
    for model in models or TTS_MODELS:
        if time.time() < _tts_cooldown.get(model, 0):
            continue
        try:
            resp = llm.client().models.generate_content(
                model=model,
                contents=f"Say in a clear, friendly Indian English voice, at a brisk pace: {text}",
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)))))
            part = resp.candidates[0].content.parts[0].inline_data
            return part.data, part.mime_type or ""
        except Exception as e:
            last = e
            # 429 quota: rest this model for a minute; 503/504: briefly.
            _tts_cooldown[model] = time.time() + (60 if "429" in str(e) else 20)
            log.info("TTS %s failed: %s", model, str(e)[:100])
    raise RuntimeError(f"All TTS models unavailable: {last}")


def speak_locally(text: str):
    """Offline fallback via speech-dispatcher, so the mechanic still hears the question."""
    subprocess.run(["spd-say", "-w", "-r", "10", text], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play(data: bytes, mime: str):
    if "wav" in mime:
        cmd = ["aplay", "-q", "-"]
    else:  # raw PCM, e.g. audio/L16;codec=pcm;rate=24000
        rate = "24000"
        for bit in mime.split(";"):
            if bit.strip().startswith("rate="):
                rate = bit.split("=", 1)[1]
        cmd = ["aplay", "-q", "-f", "S16_LE", "-r", rate, "-c", "1", "-t", "raw", "-"]
    subprocess.run(cmd, input=data, check=False)


class Speaker:
    """Queue of things to say, played in order on a worker thread."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.idle = threading.Event()
        self.idle.set()
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text: str):
        self.idle.clear()
        self.q.put(text)

    def wait(self):
        self.idle.wait()

    def _run(self):
        while True:
            text = self.q.get()
            try:
                data, mime = synthesize(text)
                play(data, mime)
            except Exception as e:
                log.warning("Gemini TTS unavailable (%s); using spd-say", str(e)[:80])
                speak_locally(text)
            finally:
                if self.q.empty():
                    self.idle.set()


# ---------------------------------------------------------------- listening

def record_utterance(max_seconds: float = 15, silence_after: float = 1.2,
                     start_timeout: float = 10) -> bytes | None:
    """Record from the default mic until the speaker pauses. Returns WAV bytes,
    or None if nobody spoke within `start_timeout` seconds."""
    proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frames: list[bytes] = []
    try:
        # Calibrate the noise floor on the first 300 ms.
        noise = [audioop.rms(proc.stdout.read(CHUNK * 2), 2) for _ in range(3)]
        floor = max(sum(noise) / len(noise), 120)
        threshold = floor * 3
        started, silent_for, t0 = False, 0.0, time.time()
        pre: list[bytes] = []
        while time.time() - t0 < max_seconds:
            chunk = proc.stdout.read(CHUNK * 2)
            if not chunk:
                break
            loud = audioop.rms(chunk, 2) > threshold
            if not started:
                pre = (pre + [chunk])[-3:]          # keep 300 ms before speech onset
                if loud:
                    started = True
                    frames.extend(pre)
                elif time.time() - t0 > start_timeout:
                    return None
                continue
            frames.append(chunk)
            silent_for = 0.0 if loud else silent_for + 0.1
            if silent_for >= silence_after:
                break
    finally:
        proc.terminate()
    if not frames:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def transcribe(wav: bytes, context: str = "") -> str:
    """Verbatim transcript. Models sometimes *reply* to the audio instead of
    transcribing it, so the instruction is a system prompt and the result is
    checked against the audio length; a too-long transcript is retried on the
    next model."""
    system = ("You are a speech-to-text engine. Output only the exact words spoken in the audio, "
              "nothing else. Never answer, explain, or add words. The speaker is an Indian mechanic "
              "talking about car parts and may mix Hindi and English; write Hindi words in Latin script. "
              "Car makes, models and trim names (Harrier, Nexon, XZA plus, VXI, BS6) are likely. "
              "If there is no speech, output nothing.")
    hint = f"(Context only, do not repeat: the speaker is answering \"{context}\")\n" if context else ""
    seconds = max(_wav_seconds(wav), 0.5)
    chain = STT_MODELS + [m for m in llm.model_chain() if m not in STT_MODELS]
    text = ""
    for i, model in enumerate(chain):
        try:
            resp = llm.generate([hint + "Transcribe:", types.Part.from_bytes(data=wav, mime_type="audio/wav")],
                                types.GenerateContentConfig(system_instruction=system, temperature=0),
                                models=[model])
        except Exception:
            continue
        text = (resp.text or "").strip()
        # People rarely say more than ~4 words a second; much more means the model replied.
        if len(text.split()) <= 4.5 * seconds + 3:
            return text
        log.info("transcript from %s looks like a reply (%d words for %.1fs), retrying",
                 model, len(text.split()), seconds)
    return text


def _wav_seconds(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def listen(context: str = "") -> str:
    wav = record_utterance()
    if not wav:
        return ""
    return transcribe(wav, context)
