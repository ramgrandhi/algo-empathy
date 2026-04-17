# Plan: "Do I Need a Coat?" — Voice Weather Assistant + t-NPS Collector

> Source PRD: `PRD.md` (root of repo)

---

## Architectural Decisions

Durable decisions that apply across all phases:

- **Frontend routes**
  - `/` — single-page app, all states managed client-side (idle → listening → thinking → speaking → nps → thank-you)

- **Backend API routes**
  - `GET  /health` — liveness probe
  - `POST /api/session/{id}/turn` — multipart audio upload, returns STT + emotion JSON
  - `GET  /api/session/{id}/respond` — SSE stream of LLM tokens
  - `GET  /api/weather?lat=&lon=` — returns weather JSON for coordinates
  - `POST /api/session/{id}/complete` — writes final session record to filesystem

- **Key data models**
  - `Session` — top-level record written to `data/sessions/{id}.json`
  - `Turn` — one entry per voice exchange: transcript, emotion, scores, timing
  - `ConversationContext` — extracted slots: `when`, `where`, `how_long`, `transport`
  - `WeatherData` — `temp_c`, `condition`, `wind_kph`
  - `Metrics` — computed at session close: totals, trajectory, latencies

- **LLM boundary**
  - `LLMProvider` ABC with `async chat(messages) -> str`
  - `ClaudeProvider` and `AzureOpenAIProvider` implementations
  - Selected via `LLM_PROVIDER=claude|azure` env var — no code change to swap

- **Model loading**
  - Whisper and wav2vec2 loaded once at backend startup into module-level singletons
  - Weights stored in `./models/` volume — never re-downloaded on restart

- **Storage**
  - Filesystem only: `data/sessions/{id}.json` + `data/sessions/{id}/turn_{n}.webm`
  - No database, no migrations

- **Audio format**
  - Browser captures `.webm` (Opus codec) via `MediaRecorder`
  - Backend accepts as multipart `audio` field, writes raw file before processing

---

## Phase 1: Project Scaffold

**Issues covered:** Issue 1

### What to build

Bootstrap the full monorepo so both services start locally with a single command. This is the foundation every subsequent phase builds on — nothing else starts until this is green.

### Acceptance criteria

- [ ] Monorepo structure: `frontend/`, `backend/`, `data/`, `models/`, `docker-compose.yml`, `.env.example`
- [ ] `docker-compose up` starts both services without errors (mapping frontend to `3001` and backend to `8000`)
- [ ] Frontend `Dockerfile` uses `node:20-alpine` (Next.js 16 requirement)
- [ ] `GET http://localhost:3001` returns Next.js default page
- [ ] `GET http://localhost:8000/health` returns `{"status": "ok"}`
- [ ] `.env.example` documents all env vars: `OPENWEATHER_API_KEY`, `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`, `NEXT_PUBLIC_API_URL`
- [ ] `data/` and `models/` are gitignored with `.gitkeep`
- [ ] `models/` volume-mounted in docker-compose so weights persist across restarts

---

## Phase 2: VAD + Audio Capture

**Issues covered:** Issue 2  
**Can start after:** Phase 1

### What to build

The single "Do I need a coat?" button opens the mic. `@ricky0123/vad-web` (silero-vad, browser-side) detects when the user stops speaking and automatically POSTs the audio blob to the backend — no manual stop button. Backend stub returns `200` to close the loop.

### Acceptance criteria

- [ ] Single button visible on idle screen; pressing it requests mic permission and starts VAD
- [ ] VAD required static assets (`.onnx`, `.wasm`, `.mjs` from `onnxruntime-web`) are served directly from `public/`
- [ ] "Listening..." indicator shown while VAD is active
- [ ] ~1s of silence auto-triggers recording stop and shows "Thinking..."
- [ ] `.webm` blob POSTed to `POST /api/session/{id}/turn` without any manual action
- [ ] Empty (0-byte) `.webm` uploads are prevented on the frontend and rejected cleanly (400 Bad Request) on the backend
- [ ] VAD pauses during LLM generation/TTS and unpauses immediately after TTS completion
- [ ] Mic re-opens automatically after stub `200` response
- [ ] Retry counter increments if VAD re-triggers within 1s of a previous POST (noise false-positive guard)
- [ ] Works on Chrome and Safari mobile (iOS + Android)

---

## Phase 3: STT + Emotion Pipeline

**Issues covered:** Issue 3  
**Can start after:** Phase 1 (parallel with Phase 2 and 4)

### What to build

The backend turn endpoint runs `openai/whisper-base` on the incoming audio for transcription, then `ehcalabres/wav2vec2-lg-xlsr-en-speech-emotion-recognition` for emotion classification. Both models load once at startup. Returns structured JSON used downstream by the conversation engine and persisted in the session record.

### Acceptance criteria

- [ ] `POST /api/session/{id}/turn` accepts multipart `audio` (.webm)
- [ ] Returns `{ transcript, emotion, emotion_scores, word_count, backend_latency_ms }`
- [ ] `emotion_scores` contains all classes (happy, neutral, sad, angry, fearful, disgusted, surprised)
- [ ] `word_count` derived from `len(transcript.split())`
- [ ] `backend_latency_ms` measured from request received to response sent
- [ ] Both models loaded at startup — not per request
- [ ] Weights written to `./models/` and not re-downloaded on container restart

---

## Phase 4: LLM Conversation Engine

**Issues covered:** Issue 4  
**Can start after:** Phase 1 (parallel with Phase 2 and 3)

### What to build

`LLMProvider` abstraction with `ClaudeProvider` (live) and `AzureOpenAIProvider` (stub). Conversation state tracks turn history and extracted context slots per session. SSE endpoint streams Claude's response tokens back to the frontend. System prompt instructs free-form reasoning up to 5 turns with a nuanced coat recommendation.

### Acceptance criteria

- [ ] `LLMProvider` ABC defined; `ClaudeProvider` calls `claude-haiku-4-5`
- [ ] `AzureOpenAIProvider` stub returns a fixed response (sufficient for phase)
- [ ] `GET /api/session/{id}/respond` returns SSE token stream
- [ ] System prompt enforces: free-form slot collection, max 5 turns, 1–2 sentence spoken responses, nuanced coat recommendation
- [ ] Conversation history maintained per `session_id` in memory for session lifetime
- [ ] `context_extracted` slots (`when`, `where`, `how_long`, `transport`) updated each turn; unfilled slots remain `null`
- [ ] `LLM_PROVIDER=azure` routes to Azure provider — no code change required

---

## Phase 5: Browser TTS Playback

**Issues covered:** Issue 5  
**Can start after:** Phase 4

### What to build

Frontend consumes the SSE token stream and feeds it to `window.speechSynthesis` in word-boundary chunks. Visual state transitions through the full cycle. Mic re-opens only after the TTS utterance fully completes, preventing the assistant from hearing itself.

### Acceptance criteria

- [ ] SSE tokens fed to `SpeechSynthesis` with minimal perceived delay
- [ ] Visual states cycle correctly: idle → listening → thinking → speaking → listening
- [ ] Mic does not re-open until `utterance.onend` fires
- [ ] iOS Safari handled: `SpeechSynthesis` requires user gesture — covered by the initial button press
- [ ] No audio feedback loop (mic closed during TTS)

---

## Phase 6: Weather + Location

**Issues covered:** Issue 6  
**Can start after:** Phases 3 and 4

### What to build

GPS coordinates captured silently on session start. Each turn, Claude's extracted destination (from transcript) is geocoded via OpenWeatherMap's `/geo/1.0/direct` and weather is fetched. Weather context injected into Claude's message history so the recommendation is grounded in real data.

### Acceptance criteria

- [ ] `navigator.geolocation` requested on session start; denial handled gracefully (no crash)
- [ ] `GET /api/weather?lat=&lon=` returns `{ temp_c, condition, wind_kph }`
- [ ] Destination string from Claude's `context_extracted.where` passed to geocoding endpoint
- [ ] Falls back to GPS coordinates when geocoding returns no results
- [ ] Weather data injected into Claude's system context before responding
- [ ] `weather_fetched` populated in session record

---

## Phase 7: End-to-End Conversation Loop

**Issues covered:** Issue 7  
**Can start after:** Phases 2, 3, 5, 6

### What to build

Wire all pipeline stages into a single turn cycle and enforce session logic. This is the first fully demoable end-to-end path: user presses button, has a real voice conversation, receives a coat recommendation, and is handed off to the NPS screen.

### Acceptance criteria

- [ ] Full cycle: VAD → POST audio → STT/emotion → Claude + weather → SSE → TTS → mic re-open
- [ ] Turn counter enforced — recommendation forced at turn 5 regardless of slot fill state
- [ ] Autonomous exit — LLM maintains session context and autonomously decides when to provide the recommendation and terminate the session, triggering the NPS screen
- [ ] `context_extracted` slots updated progressively across turns
- [ ] Session transitions to NPS screen automatically after recommendation turn
- [ ] `metrics.turn_count`, `metrics.total_duration_ms`, `metrics.retry_count` accumulated correctly
- [ ] `metrics.sentiment_trajectory` array populated with per-turn dominant emotion score

---

## Phase 8: t-NPS Capture + Session Persistence

**Issues covered:** Issue 8  
**Can start after:** Phase 7

### What to build

After the recommendation, a 0–10 tap widget appears. One tap selects the score (no confirm needed). An optional verbatim text field follows. Submission writes the complete session JSON and all per-turn `.webm` files to `data/sessions/`.

### Acceptance criteria

- [ ] 0–10 tap widget shown immediately after recommendation TTS completes
- [ ] One tap captures score — no double-tap or confirm
- [ ] Optional `<textarea>` for verbatim appears after score tap
- [ ] "Submit" POSTs to `POST /api/session/{id}/complete`
- [ ] Session JSON written to `data/sessions/{id}.json` matching schema in `PRD.md`
- [ ] Per-turn `.webm` files moved to `data/sessions/{id}/turn_{n}.webm`
- [ ] `metrics.completion_success = true` only on NPS submission (browser close = false)
- [ ] Thank-you screen shown after submission

---

## Phase 9: Mobile UI Polish

**Issues covered:** Issue 9  
**Can start after:** Phase 8

### What to build

Refine the UI for mobile-first use: single prominent button on idle, clear animated state indicators, accessible NPS tap targets. Tested on iOS Safari and Android Chrome.

### Acceptance criteria

- [ ] "Do I need a coat?" button is the only visible element on idle screen
- [ ] State indicators legible at arm's length (listening pulse, thinking spinner, speaking waveform)
- [ ] NPS tap targets ≥ 44px, full-width row on mobile viewport
- [ ] No horizontal scroll on 375px viewport
- [ ] Lighthouse mobile performance score ≥ 80
- [ ] Tested on iOS Safari 17+ and Android Chrome

---

## Phase 10: K8s Deployment Manifests

**Issues covered:** Issue 10  
**Can start after:** Phase 8  
**Type: HITL** — manifests require platform-specific values before apply

### What to build

Production-ready `Dockerfile` for each service and K8s manifests. Backend image pre-downloads model weights during build so pods start fast. `PersistentVolumeClaim` for `data/` and `models/` ensures session data and weights survive pod restarts.

### Acceptance criteria

- [ ] Multi-stage `Dockerfile` for frontend (build → slim Node runtime)
- [ ] Backend `Dockerfile` includes model pre-download layer (weights baked into image or pulled to PVC on init container)
- [ ] K8s `Deployment`, `Service`, and `PersistentVolumeClaim` manifests for both services
- [ ] Resource requests/limits set appropriate for CPU inference (wav2vec2 + Whisper)
- [ ] All env vars in `.env.example` map 1:1 to K8s `Secret` / `ConfigMap` keys
- [ ] **HITL checkpoint:** registry URL, namespace, ingress class, and storage class reviewed against platform before `kubectl apply`

---

## Parallel Execution Map

```
Phase 1 (Scaffold)
    ├── Phase 2 (VAD + Audio)      ─┐
    ├── Phase 3 (STT + Emotion)    ─┤
    └── Phase 4 (LLM Engine)       ─┤
              │                     │
         Phase 5 (TTS)    Phase 6 (Weather)
              └──────────────┬──────┘
                        Phase 7 (E2E Loop)
                             │
                        Phase 8 (NPS + Persist)
                             ├── Phase 9 (Mobile Polish)
                             └── Phase 10 (K8s) [HITL]
```
