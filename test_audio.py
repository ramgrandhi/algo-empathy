import requests
import numpy as np
import soundfile as sf
import io

# Create 1 second of silence
samplerate = 16000
t = np.linspace(0, 1, samplerate, endpoint=False)
data = 0.5 * np.sin(2 * np.pi * 440 * t) # 440Hz sine wave

wav_io = io.BytesIO()
sf.write(wav_io, data, samplerate, format='WAV')
wav_io.seek(0)

session_id = "test-session-456"

print("Sending audio to turn endpoint...")
res = requests.post(
    f"http://localhost:8000/api/session/{session_id}/turn",
    files={"audio": ("test.wav", wav_io.read(), "audio/wav")}
)
print("Turn response:", res.status_code, res.text)

print("Fetching SSE response...")
res2 = requests.get(f"http://localhost:8000/api/session/{session_id}/respond", stream=True)
print("Respond response:", res2.status_code)
for line in res2.iter_lines():
    if line:
        print(line.decode('utf-8'))
