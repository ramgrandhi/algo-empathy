import torchaudio
w, sr = torchaudio.load("test.webm")
print(w.shape, sr)
