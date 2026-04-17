"use client";

import { useState, useRef, useEffect } from "react";
import { v4 as uuidv4 } from "uuid";
import { MicVAD } from "@ricky0123/vad-web";

type UIState = "idle" | "listening" | "thinking" | "speaking" | "nps" | "thank-you";

// Helper to encode Float32Array to WAV Blob
function encodeWAV(samples: Float32Array, sampleRate = 16000): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (view: DataView, offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([view], { type: 'audio/wav' });
}

export default function Home() {
  const [state, setState] = useState<UIState>("idle");
  const [sessionId, setSessionId] = useState<string>("");
  const [retryCount, setRetryCount] = useState<number>(0);
  const [location, setLocation] = useState<{ lat: number; lon: number } | null>(null);
  
  // NPS states
  const [npsScore, setNpsScore] = useState<number | null>(null);
  const [npsVerbatim, setNpsVerbatim] = useState<string>("");
  const [isSubmittingNps, setIsSubmittingNps] = useState<boolean>(false);

  // Audio capture refs
  const vadRef = useRef<MicVAD | null>(null);
  const lastPostTimeRef = useRef<number>(0);
  const streamRef = useRef<MediaStream | null>(null);

  const handleSSEAndTTS = async (currentSessionId: string) => {
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiUrl}/api/session/${currentSessionId}/respond`);
      
      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      
      let sentenceBuffer = "";
      let isDone = false;
      let playingCount = 0;
      let hasStartedSpeaking = false;
      let willEndSession = false;

      const checkDone = async () => {
        if (isDone && playingCount === 0) {
          // All TTS finished
          if (willEndSession) {
            setState("nps");
          } else {
            setState("listening");
            // Ensure VAD is restarted
            if (vadRef.current) {
              await vadRef.current.start();
            }
          }
        }
      };

      const speakChunk = (text: string) => {
        const cleanText = text.trim();
        if (!cleanText) return;
        
        if (!hasStartedSpeaking) {
          hasStartedSpeaking = true;
          setState("speaking");
        }

        playingCount++;
        const utterance = new SpeechSynthesisUtterance(cleanText);
        
        // Select a better, more human-like voice if available
        if (typeof window !== 'undefined' && window.speechSynthesis) {
          const voices = window.speechSynthesis.getVoices();
          // Prefer premium/natural English voices (e.g., Samantha, Google US English, Daniel)
          const preferredVoice = voices.find(v => 
            v.lang.includes('en') && 
            (v.name.includes('Premium') || v.name.includes('Google') || v.name.includes('Samantha') || v.name.includes('Daniel') || v.name.includes('Natural'))
          );
          
          if (preferredVoice) {
            utterance.voice = preferredVoice;
          } else {
            // Fallback to any english voice
            const englishVoice = voices.find(v => v.lang.includes('en'));
            if (englishVoice) utterance.voice = englishVoice;
          }
          
          // Slightly tweak rate/pitch to sound less robotic
          utterance.rate = 0.95;
          utterance.pitch = 1.05;
        }

        utterance.onend = () => {
          playingCount--;
          checkDone();
        };
        
        utterance.onerror = (e) => {
          console.error("TTS error:", e);
          playingCount--;
          checkDone();
        };

        window.speechSynthesis.speak(utterance);
      };

      let doneReading = false;
      let lineBuffer = "";
      while (!doneReading) {
        const { value, done } = await reader.read();
        if (done) {
          doneReading = true;
          isDone = true;
          if (sentenceBuffer.trim()) {
            speakChunk(sentenceBuffer);
            sentenceBuffer = "";
          }
          checkDone();
          break;
        }

        const chunk = decoder.decode(value, { stream: true });
        lineBuffer += chunk;
        const lines = lineBuffer.split("\n");
        // Keep the last incomplete line in the buffer
        lineBuffer = lines.pop() || "";
        
        for (const line of lines) {
          const trimmedLine = line.trim();
          if (trimmedLine.startsWith("data: ")) {
            const dataStr = trimmedLine.slice(6).trim();
            if (dataStr === "[DONE]") {
              isDone = true;
              continue;
            }
            
            if (!dataStr) continue;

            try {
              const data = JSON.parse(dataStr);
              if (data.type === "control" && data.is_recommendation) {
                willEndSession = true;
              } else if (data.token) {
                sentenceBuffer += data.token;
                
                // Chunk by punctuation: . ? !
                // or if it's getting long, by comma
                const match = sentenceBuffer.match(/.*[.!?]\s/);
                if (match) {
                  const toSpeak = match[0];
                  sentenceBuffer = sentenceBuffer.slice(toSpeak.length);
                  speakChunk(toSpeak);
                } else if (sentenceBuffer.length > 40) {
                  const commaMatch = sentenceBuffer.match(/.*[,]\s/);
                  if (commaMatch) {
                    const toSpeak = commaMatch[0];
                    sentenceBuffer = sentenceBuffer.slice(toSpeak.length);
                    speakChunk(toSpeak);
                  }
                }
              }
            } catch (e) {
              console.error("Error parsing SSE JSON:", e, dataStr);
            }
          }
        }
      }
    } catch (err) {
      console.error("SSE/TTS error:", err);
      setState("listening");
      if (vadRef.current) await vadRef.current.start();
    }
  };

  const startSession = async () => {
    try {
      // Capture GPS coordinates silently on session start
      if ("geolocation" in navigator) {
        navigator.geolocation.getCurrentPosition(
          (position) => {
            setLocation({
              lat: position.coords.latitude,
              lon: position.coords.longitude,
            });
          },
          (error) => {
            console.warn("Geolocation access denied or failed:", error);
            // Graceful denial handled (no crash, location remains null)
          }
        );
      }

      // Initialize speech synthesis with empty utterance to unlock it on iOS
      if (typeof window !== 'undefined' && window.speechSynthesis) {
        const unlock = new SpeechSynthesisUtterance("");
        window.speechSynthesis.speak(unlock);
      }

      const newSessionId = uuidv4();
      setSessionId(newSessionId);
      
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Initialize VAD
      const myvad = await MicVAD.new({
        baseAssetPath: "/",
        onnxWASMBasePath: "/",
        redemptionMs: 1000,
        getStream: () => Promise.resolve(stream),
        onSpeechStart: () => {
          // No need to do anything here anymore, micVAD buffers the speech automatically
        },
        onSpeechEnd: async (audio) => {
          const audioBlob = encodeWAV(audio);

          if (audioBlob.size === 0 || audio.length === 0) {
            console.warn("Audio blob is empty, skipping turn.");
            setState("listening");
            if (vadRef.current) await vadRef.current.start();
            return;
          }

          // Debounce logic: noise guard within 1s
          const now = Date.now();
          if (now - lastPostTimeRef.current < 1000) {
            console.log("Noise guard: VAD re-triggered too quickly, ignoring.");
            setRetryCount(c => c + 1);
            setState("listening");
            if (vadRef.current) await vadRef.current.start();
            return;
          }
          lastPostTimeRef.current = now;

          setState("thinking");
          // Pause VAD entirely while we send audio and wait for the LLM + TTS
          if (vadRef.current) {
            vadRef.current.pause();
          }

          const formData = new FormData();
          formData.append("audio", audioBlob, "turn.wav");
          formData.append("retry_count", retryCount.toString());
          if (location) {
            formData.append("lat", location.lat.toString());
            formData.append("lon", location.lon.toString());
          }

          try {
            const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
            const res = await fetch(`${apiUrl}/api/session/${newSessionId}/turn`, {
              method: "POST",
              body: formData,
            });
            
            if (res.ok) {
              // Call the SSE endpoint to get the LLM response
              handleSSEAndTTS(newSessionId);
            } else {
              console.error("Backend error on /turn");
              setState("listening");
              if (vadRef.current) await vadRef.current.start();
            }
          } catch (err) {
            console.error("Upload error", err);
            setState("listening");
            if (vadRef.current) await vadRef.current.start();
          }
        }
      });
      
      vadRef.current = myvad;
      await myvad.start();
      
      setState("listening");

    } catch (err) {
      console.error("Failed to start session:", err);
    }
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      vadRef.current?.destroy();
      streamRef.current?.getTracks().forEach(track => track.stop());
    };
  }, []);

  const handleNpsSubmit = async () => {
    if (npsScore === null || isSubmittingNps) return;
    
    setIsSubmittingNps(true);
    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${apiUrl}/api/session/${sessionId}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          score: npsScore,
          verbatim: npsVerbatim.trim() || undefined
        })
      });
      
      if (res.ok) {
        setState("thank-you");
      } else {
        console.error("Failed to submit NPS");
      }
    } catch (err) {
      console.error("NPS submit error:", err);
    } finally {
      setIsSubmittingNps(false);
    }
  };

  const handleScoreTap = (score: number) => {
    setNpsScore(score);
    // Optional: we don't auto-submit immediately because they might want to type verbatim
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-4 bg-gray-50">
      {state === "idle" && (
        <button
          onClick={startSession}
          className="px-8 py-4 text-2xl font-bold text-white bg-blue-600 rounded-full shadow-lg hover:bg-blue-700 transition-transform active:scale-95"
        >
          Do I need a coat?
        </button>
      )}

      {state === "listening" && (
        <div className="flex flex-col items-center space-y-4">
          <div className="w-16 h-16 bg-red-500 rounded-full animate-pulse shadow-lg shadow-red-500/50"></div>
          <p className="text-xl font-medium text-gray-700">Listening...</p>
        </div>
      )}

      {state === "thinking" && (
        <div className="flex flex-col items-center space-y-4">
          <div className="w-16 h-16 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
          <p className="text-xl font-medium text-gray-700">Thinking...</p>
        </div>
      )}
      
      {state === "speaking" && (
        <div className="flex flex-col items-center space-y-4">
          <div className="flex space-x-2 h-16 items-center">
            <div className="w-3 h-8 bg-green-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
            <div className="w-3 h-12 bg-green-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
            <div className="w-3 h-16 bg-green-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
            <div className="w-3 h-12 bg-green-500 rounded-full animate-bounce" style={{ animationDelay: '450ms' }}></div>
            <div className="w-3 h-8 bg-green-500 rounded-full animate-bounce" style={{ animationDelay: '600ms' }}></div>
          </div>
          <p className="text-xl font-medium text-gray-700">Speaking...</p>
        </div>
      )}
      
      {state === "nps" && (
        <div className="flex flex-col items-center space-y-6 w-full max-w-md animate-fade-in">
          <h2 className="text-2xl font-bold text-gray-800 text-center">How was your experience?</h2>
          <p className="text-gray-600 text-center">Tap a score to let us know.</p>
          
          <div className="w-full bg-white rounded-xl shadow-sm border border-gray-100 p-4 w-full">
            <div className="flex justify-between w-full mb-2 px-1">
              <span className="text-xs text-gray-400 font-medium uppercase tracking-wider">0 - Not helpful</span>
              <span className="text-xs text-gray-400 font-medium uppercase tracking-wider">10 - Very helpful</span>
            </div>
            <div className="grid grid-cols-11 gap-1 sm:gap-2">
              {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((score) => (
                <button
                  key={score}
                  onClick={() => handleScoreTap(score)}
                  className={`
                    h-12 min-w-[28px] sm:min-w-[44px] flex items-center justify-center rounded-md font-medium text-sm transition-all
                    ${npsScore === score 
                      ? 'bg-blue-600 text-white scale-105 shadow-md' 
                      : 'bg-gray-50 text-gray-700 hover:bg-gray-100 active:bg-gray-200'}
                  `}
                >
                  {score}
                </button>
              ))}
            </div>
          </div>

          {npsScore !== null && (
            <div className="w-full space-y-4 animate-fade-in">
              <textarea
                value={npsVerbatim}
                onChange={(e) => setNpsVerbatim(e.target.value)}
                placeholder="Optional: Tell us more about your experience..."
                className="w-full p-4 border border-gray-200 rounded-xl shadow-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none resize-none h-24"
              />
              <button
                onClick={handleNpsSubmit}
                disabled={isSubmittingNps}
                className="w-full py-4 text-lg font-bold text-white bg-blue-600 rounded-xl shadow-md hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSubmittingNps ? 'Submitting...' : 'Submit Feedback'}
              </button>
            </div>
          )}
        </div>
      )}
      
      {state === "thank-you" && (
        <div className="flex flex-col items-center space-y-6 w-full max-w-md animate-fade-in">
          <div className="w-20 h-20 bg-green-100 text-green-500 rounded-full flex items-center justify-center mb-4">
            <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-3xl font-bold text-gray-800 text-center">Thank You!</h2>
          <p className="text-gray-600 text-center text-lg">Your feedback helps us improve the assistant.</p>
          <button
            onClick={() => {
              setSessionId("");
              setNpsScore(null);
              setNpsVerbatim("");
              setState("idle");
            }}
            className="mt-8 px-8 py-3 text-blue-600 font-medium hover:bg-blue-50 rounded-full transition-colors"
          >
            Start New Session
          </button>
        </div>
      )}
      
      {/* Hidden debug info */}
      <div className="fixed bottom-4 right-4 text-xs text-gray-400">
        {sessionId && <p>Session: {sessionId}</p>}
        {retryCount > 0 && <p>Retries: {retryCount}</p>}
      </div>
    </main>
  );
}
