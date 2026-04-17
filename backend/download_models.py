import os
from transformers import pipeline

# We set the HF_HOME directory where the models will be downloaded
MODEL_CACHE_DIR = os.environ.get("HF_HOME", "/app/models")
os.makedirs(MODEL_CACHE_DIR, exist_ok=True)

print(f"Downloading models to {MODEL_CACHE_DIR}...")

print("Downloading Whisper STT model (openai/whisper-base)...")
pipeline(
    "automatic-speech-recognition",
    model="openai/whisper-base",
    device="cpu",
    model_kwargs={"cache_dir": MODEL_CACHE_DIR}
)

print("Downloading wav2vec2 emotion classification model (superb/wav2vec2-base-superb-er)...")
pipeline(
    "audio-classification",
    model="superb/wav2vec2-base-superb-er",
    device="cpu",
    model_kwargs={"cache_dir": MODEL_CACHE_DIR}
)

print("Pre-download complete!")