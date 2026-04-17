# PRD: "Do I Need a Coat?" — Voice Weather Assistant + t-NPS Training Data Collector

## Purpose

Build a mobile-compatible voice weather web app that collects **t-NPS training data** to later train a model that predicts t-NPS from behavioral and emotional signals — without requiring explicit user feedback interruptions mid-journey.

The app has one visible action: **"Do I need a coat?"** It conducts a short voice conversation to gather context, delivers a nuanced coat recommendation, then captures a t-NPS score via a tap widget.

---

## Architecture Decisions

### Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router) |
| Backend | FastAPI (Python) |
| Local dev | `docker-compose` (full stack) |
| Deployment | Kubernetes namespace-as-a-service (Heroku-like) |
| Repo structure | Monorepo: `frontend/`, `backend/`, `data/`, `models/` |

### Voice Pipeline

| Role | Technology | Size |
|---|---|---|
| Voice Activity Detection | `@ricky0123/vad-web` (silero-vad ONNX, browser-side) | ~1 MB |
| Audio capture | `MediaRecorder` API (`.webm` per turn) | — |
| Speech-to-Text | `openai/whisper-base` (HuggingFace, CPU) | 74 MB |
| Emotion classification | `ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition` (HuggingFace, CPU) | ~1.2 GB |
| Text-to-Speech | Browser `SpeechSynthesis` API (free, zero latency) | — |
| Conversation reasoning | `claude-haiku-4-5` via `LLMProvider` abstraction | API |

### LLM Abstraction

```python
class LLMProvider(ABC):
    async def chat(self, messages: list[dict]) -> str: ...

class ClaudeProvider(LLMProvider): ...
class AzureOpenAIProvider(LLMProvider): ...
```

Switched via `LLM_PROVIDER=claude|azure` env var — zero code change to swap.

### Transport

- Audio upload: `POST /api/session/{id}/turn` — multipart audio blob → `{transcript, emotion, emotion_scores, word_count, backend_latency_ms}`
- LLM response: SSE stream from `GET /api/session/{id}/respond`
- Weather: `GET /api/weather?lat=&lon=`
- Session save: `POST /api/session/{id}/complete`

No WebSockets. Stateless, K8s-friendly.

### Location

- Browser GPS (`navigator.geolocation`) auto-detected on load — silent fallback
- Claude extracts destination from conversation transcript
- Backend geocodes destination via OpenWeatherMap `/geo/1.0/direct`
- Falls back to GPS coords if geocoding fails

### Conversation Flow

- **Strategy:** Free-form reasoning. The LLM maintains full session context across all voice commands and responses.
- **Autonomous Exit:** The LLM is fully in control of the conversation length. It autonomously decides when to provide the final recommendation and terminate the session, triggering the NPS screen as soon as it has sufficient context (regardless of the turn count).
- **Max turns:** 5 (Hard limit safety net, recommendation forced).
- **Slots to collect:** `when`, `where` (destination), `how_long`, `transport`
- **Recommendation:** Nuanced (e.g. "light jacket is enough", "definitely bring a coat")
- Unanswered slots recorded as `null` in `context_extracted`

### t-NPS Capture

- Presented after coat recommendation
- **0–10 tap widget** (no voice — keeps target variable clean for model training)
- Optional typed verbatim field
- Distinct UI break signals formal feedback moment

---

## Training Data Schema

One `.json` file per session under `data/sessions/`. Raw `.webm` audio stored alongside at `data/sessions/{session_id}/turn_{n}.webm`.

```json
{
  "session_id": "uuid",
  "timestamp": "ISO8601",

  "conversation": [
    {
      "turn": 1,
      "question": "Where are you heading?",
      "transcript": "I'm going to the park",
      "audio_file": "session_uuid/turn_1.webm",
      "emotion": "neutral",
      "emotion_scores": { "happy": 0.1, "neutral": 0.7, "sad": 0.1, "angry": 0.1 },
      "word_count": 6,
      "turn_duration_ms": 3200,
      "backend_latency_ms": 1800
    }
  ],

  "context_extracted": {
    "when": "tonight at 8pm",
    "where": "Central Park",
    "duration": "2 hours",
    "transport": "walking"
  },

  "weather_fetched": { "temp_c": 12, "condition": "light rain", "wind_kph": 18 },
  "recommendation": "The forecast shows light rain and 12°C — I'd bring a waterproof jacket.",

  "tnps_score": 8,
  "tnps_verbatim": "It was quick and helpful",

  "metrics": {
    "total_duration_ms": 42000,
    "turn_count": 5,
    "completion_success": true,
    "total_interruptions": 0,
    "retry_count": 0,
    "sentiment_trajectory": [0.7, 0.65, 0.8, 0.85, 0.9],
    "dominant_emotion_overall": "neutral",
    "avg_backend_latency_ms": 1950
  }
}
```

### Feature → t-NPS Correlation Rationale

| Feature | Signal |
|---|---|
| `emotion_scores` per turn | Tone predicts satisfaction |
| `sentiment_trajectory` | Improving vs worsening arc |
| `completion_success` | Strongest single NPS predictor |
| `turn_count` | Proxy for friction |
| `retry_count` | Repetition = frustration |
| `backend_latency_ms` | Perceived slowness affects NPS |
| `word_count` per turn | Verbosity signals engagement |
| `total_interruptions` | Friction proxy |

---

## Environment Variables

```env
# Backend
OPENWEATHER_API_KEY=
LLM_PROVIDER=claude           # claude | azure
ANTHROPIC_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_DEPLOYMENT=

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Issues

### Issue 1 — Project Scaffold
**Type:** AFK  
**Blocked by:** None

Set up the monorepo skeleton: Next.js App Router frontend, FastAPI backend, `docker-compose.yml` wiring both services, `.env.example`, and a `/health` endpoint on each service verifiable with `curl`.

**Acceptance criteria:**
- [ ] `docker-compose up --build` starts both services without errors (mapping frontend to `3001` and backend to `8000`)
- [ ] `GET http://localhost:3001` returns Next.js default page (using `node:20-alpine` due to Next.js 16 requirements)
- [ ] `GET http://localhost:8000/health` returns `{"status": "ok"}`
- [ ] `.env.example` documents all required environment variables
- [ ] `models/` and `data/` directories are gitignored with `.gitkeep`

---

### Issue 2 — VAD + Audio Capture
**Type:** AFK  
**Blocked by:** Issue 1

Wire `@ricky0123/vad-web` (silero-vad) into the Next.js frontend. Single "Do I need a coat?" button starts the session. VAD auto-detects speech end, `MediaRecorder` captures `.webm`, POSTs to a backend stub at `POST /api/session/{id}/turn` that returns `200 {"received": true}`.

**Acceptance criteria:**
- [ ] Button press opens mic and shows "Listening..." indicator
- [ ] VAD required static assets (`.onnx`, `.wasm`, `.mjs` from `onnxruntime-web`) are served directly from `public/`
- [ ] Silence for ~1s auto-stops recording and shows "Thinking..." indicator
- [ ] `.webm` audio blob POSTed to backend without manual button press
- [ ] Empty (0-byte) `.webm` uploads are prevented on the frontend and rejected cleanly (400 Bad Request) on the backend
- [ ] VAD pauses during LLM generation/TTS and unpauses immediately after TTS completion
- [ ] Mic re-opens after stub response received
- [ ] Works on Chrome/Safari mobile (iOS + Android tested)

---

### Issue 3 — STT + Emotion Pipeline
**Type:** AFK  
**Blocked by:** Issue 1

Backend endpoint `POST /api/session/{id}/turn` receives audio blob, runs `openai/whisper-base` for transcription and `ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition` for emotion classification. Models downloaded to `./models` on first run and cached. Returns `{transcript, emotion, emotion_scores, word_count, backend_latency_ms}`.

**Acceptance criteria:**
- [ ] Endpoint accepts multipart `audio` field (`.webm`)
- [ ] Returns transcript string from Whisper
- [ ] Returns `emotion` (dominant class) + `emotion_scores` dict from wav2vec2
- [ ] Returns `word_count` (len of transcript tokens) and `backend_latency_ms`
- [ ] Models loaded once at startup, not per request
- [ ] Model weights written to `./models/` volume, not re-downloaded on restart

---

### Issue 4 — LLM Conversation Engine
**Type:** AFK  
**Blocked by:** Issue 1

Implement `LLMProvider` abstract base class with `ClaudeProvider` (`claude-haiku-4-5`) and `AzureOpenAIProvider` stub. Conversation state manager tracks turn history and extracted slots (`when`, `where`, `how_long`, `transport`). SSE endpoint streams Claude's response tokens.

**Acceptance criteria:**
- [ ] `LLM_PROVIDER=claude` routes to Anthropic API
- [ ] `LLM_PROVIDER=azure` routes to Azure OpenAI endpoint (stub returning fixed response is acceptable)
- [ ] `GET /api/session/{id}/respond` returns SSE stream of tokens
- [ ] System prompt instructs free-form reasoning, max 5 turns, nuanced coat advice
- [ ] Conversation history maintained per `session_id` in memory for session duration
- [ ] Switching provider requires only env var change, no code change

---

### Issue 5 — Browser TTS Playback
**Type:** AFK  
**Blocked by:** Issue 4

Frontend consumes SSE token stream from the LLM engine, feeds tokens to `window.speechSynthesis` in chunks, shows "Speaking..." visual state, and re-opens mic after TTS finishes.

**Acceptance criteria:**
- [ ] SSE tokens fed to `SpeechSynthesis` with minimal buffering delay
- [ ] Visual state cycles: idle → listening → thinking → speaking → listening
- [ ] Mic does not re-open until TTS utterance fully completes
- [ ] Works on Chrome/Safari mobile (iOS requires user gesture — handled by initial button press)

---

### Issue 6 — Weather + Location
**Type:** AFK  
**Blocked by:** Issues 3, 4

Frontend requests `navigator.geolocation` on session start. Claude extracts destination from transcript each turn and passes to backend. Backend geocodes destination via OpenWeatherMap `/geo/1.0/direct`, fetches current weather, injects into Claude's context. Falls back to GPS coordinates if geocoding fails or destination is absent.

**Acceptance criteria:**
- [ ] GPS coordinates captured on session start (graceful denial fallback)
- [ ] Destination extracted from transcript injected into weather fetch
- [ ] `GET /api/weather?lat=&lon=` returns `{temp_c, condition, wind_kph}`
- [ ] Weather data present in Claude's context when generating recommendation
- [ ] `weather_fetched` populated in session record

---

### Issue 7 — End-to-End Conversation Loop
**Type:** AFK  
**Blocked by:** Issues 2, 3, 5, 6

Wire all pipeline stages into a complete turn cycle. Enforce max 5 turns. Final turn always delivers nuanced coat recommendation. Session transitions to t-NPS screen after recommendation.

**Acceptance criteria:**
- [ ] Full cycle works: VAD → POST audio → STT/emotion → Claude + weather → SSE → TTS → mic re-open
- [ ] Turn counter enforced — recommendation forced at turn 5 regardless of slot fill state
- [ ] `context_extracted` slots populated as conversation progresses
- [ ] Transition to NPS screen triggered automatically after recommendation turn
- [ ] Retry counter increments if VAD triggers within 1s of previous upload (noise false-positive)

---

### Issue 8 — t-NPS Capture + Session Persistence
**Type:** AFK  
**Blocked by:** Issue 7

After recommendation, show 0–10 tap widget and optional typed verbatim field. On submission, write complete session JSON to `data/sessions/{session_id}.json` and move per-turn `.webm` files to `data/sessions/{session_id}/`.

**Acceptance criteria:**
- [ ] 0–10 tap widget rendered, one tap selects score (no double-tap confirm needed)
- [ ] Optional verbatim `<textarea>` appears after score tap
- [ ] "Submit" writes session JSON matching schema defined in PRD
- [ ] Per-turn `.webm` files co-located with session JSON
- [ ] `metrics.completion_success` = `true` only when NPS submitted (not on browser close)
- [ ] Thank-you screen shown after submission

---

### Issue 9 — Mobile UI Polish
**Type:** AFK  
**Blocked by:** Issue 8

Responsive single-button layout optimised for mobile. Clear visual states for each pipeline phase. NPS widget with accessible tap targets (min 44px). Tested on iOS Safari and Android Chrome.

**Acceptance criteria:**
- [ ] "Do I need a coat?" button is the only visible element on idle screen
- [ ] Visual state indicators legible at arm's length (listening pulse, thinking spinner, speaking wave)
- [ ] NPS tap targets ≥ 44px, full-width on mobile
- [ ] No horizontal scroll on 375px viewport
- [ ] Passes Lighthouse mobile performance score ≥ 80

---

### Issue 10 — K8s Deployment Manifests
**Type:** HITL  
**Blocked by:** Issue 8

`Dockerfile` for each service (multi-stage for frontend, model-preloading layer for backend). K8s `Deployment`, `Service`, and `PersistentVolumeClaim` manifests for `data/` and `models/` volumes.

**Acceptance criteria:**
- [ ] `docker build` succeeds for both services
- [ ] Backend image pre-downloads model weights during build (not at runtime)
- [ ] K8s manifests include resource requests/limits appropriate for CPU-based model inference
- [ ] PVC for `data/` persists across pod restarts
- [ ] PVC for `models/` shared or pre-populated to avoid redundant downloads
- [ ] `.env.example` values map 1:1 to K8s `Secret` / `ConfigMap` keys
- [ ] **HITL:** Manifests reviewed against platform-specific registry URL, namespace, and ingress class before apply
