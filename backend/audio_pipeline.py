import os
import time
import subprocess
import io
import soundfile as sf
from transformers import pipeline

# Set Hugging Face cache directory to persist models
MODEL_CACHE_DIR = os.environ.get("HF_HOME", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models")))
os.environ["HF_HOME"] = MODEL_CACHE_DIR

# Global singletons for pipelines
stt_pipeline = None
emotion_pipeline = None

def load_models():
    global stt_pipeline, emotion_pipeline
    
    print("Loading Whisper STT model...")
    # STT: openai/whisper-base
    stt_pipeline = pipeline(
        "automatic-speech-recognition",
        model="openai/whisper-base",
        device="cpu", # Force CPU as per PRD
        model_kwargs={"cache_dir": MODEL_CACHE_DIR}
    )
    
    print("Loading wav2vec2 emotion classification model...")
    # Emotion: ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition
    emotion_pipeline = pipeline(
        "audio-classification",
        model="ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition",
        device="cpu",
        model_kwargs={"cache_dir": MODEL_CACHE_DIR}
    )
    print("Models loaded successfully.")

def process_audio_file(file_path: str):
    """
    Processes a given audio file path.
    Returns transcript and emotion data.
    """
    start_time = time.time()
    
    # 1. Run STT directly on the file path (transformers handles ffmpeg internally)
    stt_result = stt_pipeline(file_path)
    transcript = stt_result.get("text", "").strip()
    
    # 2. Run Emotion Classification directly on the file path
    emotion_result = emotion_pipeline(file_path, top_k=None)
    
    # Process emotion scores
    # ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition returns 8 classes: 
    # 'angry', 'calm', 'disgust', 'fearful', 'happy', 'neutral', 'sad', 'surprised'
    raw_scores = {item['label']: float(item['score']) for item in emotion_result}
    
    emotion_scores = {
        "neutral": raw_scores.get("neutral", 0.0) + raw_scores.get("calm", 0.0), # Merge calm into neutral
        "happy": raw_scores.get("happy", 0.0),
        "angry": raw_scores.get("angry", 0.0),
        "sad": raw_scores.get("sad", 0.0),
        "fearful": raw_scores.get("fearful", 0.0),
        "disgusted": raw_scores.get("disgust", 0.0),
        "surprised": raw_scores.get("surprised", 0.0)
    }
    
    # Sort by score descending to find dominant
    dominant_emotion = max(emotion_scores, key=emotion_scores.get)
    
    end_time = time.time()
    backend_latency_ms = int((end_time - start_time) * 1000)
    word_count = len(transcript.split())
    
    return {
        "transcript": transcript,
        "emotion": dominant_emotion,
        "emotion_scores": emotion_scores,
        "word_count": word_count,
        "backend_latency_ms": backend_latency_ms
    }
