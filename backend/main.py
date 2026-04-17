from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
import os
import time
import httpx
import datetime
from contextlib import asynccontextmanager
from typing import Optional
from audio_pipeline import load_models, process_audio_file
from conversation_state import conversation_manager
from llm_provider import get_llm_provider
import json

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load models at startup
    load_models()
    yield
    # Clean up resources if needed

app = FastAPI(title="Coat Advisor API", lifespan=lifespan)

# Add CORS middleware to allow frontend to communicate with backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/api/weather")
async def get_weather(lat: float, lon: float):
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENWEATHER_API_KEY not configured")
        
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            return {
                "temp_c": data["main"]["temp"],
                "condition": data["weather"][0]["description"],
                "wind_kph": data["wind"]["speed"] * 3.6  # convert m/s to km/h
            }
    except Exception as e:
        print(f"Weather fetch error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch weather data")

@app.post("/api/session/{session_id}/turn")
async def process_turn(
    session_id: str, 
    audio: UploadFile = File(...),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    retry_count: int = Form(0)
):
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No audio file provided")
    
    # Read the file contents first to check size
    file_contents = await audio.read()
    if len(file_contents) == 0:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    
    session = conversation_manager.get_or_create_session(session_id)
    if lat is not None and lon is not None:
        session.location = {"lat": lat, "lon": lon}
    
    # Save the uploaded file temporarily to process it
    try:
        # Create data directory if it doesn't exist
        os.makedirs(f"data/sessions/{session_id}", exist_ok=True)
        turn_num = session.turn_count + 1
        # The frontend now sends 'turn.wav' via encodeWAV instead of MediaRecorder's webm
        file_ext = audio.filename.split('.')[-1] if '.' in audio.filename else 'webm'
        file_path = os.path.join(f"data/sessions/{session_id}", f"turn_{turn_num}.{file_ext}")
        
        with open(file_path, "wb") as f:
            f.write(file_contents)
            
        turn_start_time = time.time()
        # Run STT + Emotion pipeline
        result = process_audio_file(file_path)
        backend_latency = int((time.time() - turn_start_time) * 1000)
        
        result["backend_latency_ms"] = backend_latency
        
        # Add the transcript to conversation state
        conversation_manager.add_user_message(session_id, result["transcript"])
        
        # Update metrics
        session.metrics["turn_count"] = session.turn_count
        session.metrics["retry_count"] += retry_count
        dom_emotion = result.get("emotion")
        if dom_emotion and "emotion_scores" in result:
            score = result["emotion_scores"].get(dom_emotion, 0.0)
            session.metrics["sentiment_trajectory"].append(score)
        
        session.metrics["total_duration_ms"] = int((time.time() - session.start_time) * 1000)
        
        # Save turn detailed history
        session.conversation_history.append({
            "turn": turn_num,
            "question": "", # Will be filled by previous assistant message if any
            "transcript": result["transcript"],
            "audio_file": f"{session_id}/turn_{turn_num}.{file_ext}",
            "emotion": dom_emotion,
            "emotion_scores": result.get("emotion_scores", {}),
            "word_count": result.get("word_count", 0),
            "turn_duration_ms": 0, # Difficult to accurately measure from frontend right now
            "backend_latency_ms": backend_latency
        })
        
        if len(session.messages) >= 2 and session.messages[-2]["role"] == "assistant":
            session.conversation_history[-1]["question"] = session.messages[-2]["content"]
        
        return result
    except Exception as e:
        print(f"Error processing audio: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def fetch_weather_for_session(session):
    """
    Attempts to fetch weather based on the extracted destination ('where').
    Falls back to GPS coordinates if geocoding fails or 'where' is unknown.
    """
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        print("OPENWEATHER_API_KEY not configured")
        return None
        
    try:
        lat, lon = None, None
        async with httpx.AsyncClient() as client:
            # 1. Try geocoding the destination
            destination = session.context_extracted.get("where")
            if destination:
                geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={destination}&limit=1&appid={api_key}"
                geo_res = await client.get(geo_url)
                geo_res.raise_for_status()
                geo_data = geo_res.json()
                if geo_data and len(geo_data) > 0:
                    lat = geo_data[0]["lat"]
                    lon = geo_data[0]["lon"]
            
            # 2. Fallback to GPS coordinates
            if lat is None or lon is None:
                if session.location.get("lat") is not None and session.location.get("lon") is not None:
                    lat = session.location["lat"]
                    lon = session.location["lon"]
            
            # 3. Fetch weather if we have coordinates
            if lat is not None and lon is not None:
                weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
                weather_res = await client.get(weather_url)
                weather_res.raise_for_status()
                w_data = weather_res.json()
                
                weather_info = {
                    "temp_c": w_data["main"]["temp"],
                    "condition": w_data["weather"][0]["description"],
                    "wind_kph": w_data["wind"]["speed"] * 3.6
                }
                session.weather_fetched = weather_info
                return weather_info
    except Exception as e:
        print(f"Error fetching weather for session: {e}")
        return None
    return None

@app.get("/api/session/{session_id}/respond")
async def respond(session_id: str):
    session = conversation_manager.get_or_create_session(session_id)
    llm_provider = get_llm_provider()
    
    # Attempt to fetch weather before generating the response
    weather_info = await fetch_weather_for_session(session)
    weather_context = ""
    if weather_info:
        weather_context = f"""
WEATHER DATA:
- Temperature: {weather_info['temp_c']}°C
- Condition: {weather_info['condition']}
- Wind Speed: {weather_info['wind_kph']:.1f} km/h
Use this weather data to inform your coat recommendation.
"""

    # System prompt enforcing free-form reasoning, slot collection, max 5 turns, 1-2 sentence recommendation
    # Also asks the LLM to output its slot extraction logic inside a special JSON block
    system_prompt = f"""You are a helpful voice assistant determining if the user needs a coat.
Your goal is to collect these 4 context slots: 'when', 'where' (destination), 'how_long', 'transport'.
Ask free-form conversational questions to gather this context.

CRITICAL RULE: You are fully in control of the conversation length. As soon as you decide you have enough context to make a good recommendation, or if the user directly asks if they need a coat, you MUST provide the final nuanced coat recommendation immediately and end the conversation. Do not artificially prolong the conversation.
(Note: You have a hard limit of 5 turns. This is turn {session.turn_count}.)

Once you provide the recommendation, DO NOT ask any further questions.

Keep all spoken responses to 1-2 short sentences.
{weather_context}
At the very beginning of your response, you MUST output a JSON block wrapped in <slots> tags with the current extracted values for the slots, and an "is_recommendation" boolean flag.
Use null for slots that are not yet known.
Output ONLY raw JSON inside the <slots> tags. Do not use markdown formatting.
Example:
<slots>
{{"when": "tonight at 8pm", "where": "Central Park", "how_long": "2 hours", "transport": "walking", "is_recommendation": false}}
</slots>
Set "is_recommendation" to true IF AND ONLY IF you are providing the final coat recommendation in this turn (e.g. answering "do I need a coat"). Setting this to true will immediately end the session and transition the user to the NPS feedback screen.
After the <slots> block, provide your spoken response.
"""
    
    async def sse_generator():
        full_response = ""
        slots_buffer = ""
        in_slots = False
        spoken_response = ""
        yielded_control = False
        
        try:
            async for token in llm_provider.chat_stream(session.messages, system_prompt):
                full_response += token
                
                # Parse the streaming tokens to separate <slots> from the spoken response
                # This is a simple state machine to buffer the slots block and stream the rest
                if not in_slots and "<slots>" in full_response and "</slots>" not in full_response:
                    in_slots = True
                    # If we just entered slots, we don't yield yet
                    continue
                elif in_slots:
                    if "</slots>" in full_response:
                        in_slots = False
                        # Extract the JSON part
                        try:
                            start_idx = full_response.find("<slots>") + len("<slots>")
                            end_idx = full_response.find("</slots>")
                            json_str = full_response[start_idx:end_idx].strip()
                            
                            # Clean up potential markdown blocks if LLM ignores instructions
                            if json_str.startswith("```json"):
                                json_str = json_str[7:]
                            if json_str.startswith("```"):
                                json_str = json_str[3:]
                            if json_str.endswith("```"):
                                json_str = json_str[:-3]
                            json_str = json_str.strip()

                            if json_str:
                                extracted_slots = json.loads(json_str)
                                conversation_manager.update_slots(session_id, extracted_slots)
                                
                                is_rec = extracted_slots.get("is_recommendation", False)
                                if (is_rec or session.turn_count >= 5) and not yielded_control:
                                    yield f"data: {json.dumps({'type': 'control', 'is_recommendation': True})}\n\n"
                                    yielded_control = True
                        except Exception as e:
                            print(f"Error parsing slots: {e}\nRaw JSON string: {json_str}")
                        
                        # Extract what comes after </slots> as the start of the spoken response
                        post_slots = full_response[full_response.find("</slots>") + len("</slots>"):].lstrip()
                        if post_slots and len(post_slots) > len(spoken_response):
                            new_text = post_slots[len(spoken_response):]
                            spoken_response += new_text
                            yield f"data: {json.dumps({'token': new_text})}\n\n"
                    continue
                
                # If we are not in slots, we just yield the token
                # Make sure we don't yield parts of the <slots> tag itself
                if "<slots>" not in full_response:
                    spoken_response += token
                    yield f"data: {json.dumps({'token': token})}\n\n"
                elif "</slots>" in full_response:
                    # We've already passed the slots block, just yield the new text
                    post_slots = full_response[full_response.find("</slots>") + len("</slots>"):].lstrip()
                    if post_slots and len(post_slots) > len(spoken_response):
                        new_text = post_slots[len(spoken_response):]
                        spoken_response += new_text
                        yield f"data: {json.dumps({'token': new_text})}\n\n"

        except Exception as e:
            print(f"SSE Generation Error: {e}")
            yield f"data: {json.dumps({'token': ' Sorry, I encountered an error.'})}\n\n"
        finally:
            print(f"DEBUG LLM FULL RESPONSE: {full_response}")
            # Save the full spoken response to conversation history
            if spoken_response:
                conversation_manager.add_assistant_message(session_id, spoken_response.strip())
            yield "data: [DONE]\n\n"
            
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

class NPSRequest(BaseModel):
    score: int
    verbatim: Optional[str] = None

@app.post("/api/session/{session_id}/complete")
async def complete_session(session_id: str, payload: NPSRequest):
    session = conversation_manager.get_or_create_session(session_id)
    
    # Update metrics
    session.metrics["completion_success"] = True
    session.metrics["total_duration_ms"] = int((time.time() - session.start_time) * 1000)
    
    # Determine dominant emotion overall
    dom_overall = "neutral"
    if session.metrics["sentiment_trajectory"]:
        # A simple heuristic: could take average or just last
        # But since we just store dominant scores, we need the classes.
        # Actually, let's just count the most frequent emotion from history
        emotions = [t["emotion"] for t in session.conversation_history if t.get("emotion")]
        if emotions:
            dom_overall = max(set(emotions), key=emotions.count)
    session.metrics["dominant_emotion_overall"] = dom_overall
    
    # Calculate avg latency
    latencies = [t["backend_latency_ms"] for t in session.conversation_history if "backend_latency_ms" in t]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    session.metrics["avg_backend_latency_ms"] = int(avg_latency)
    session.metrics["total_interruptions"] = 0 # Not tracked currently
    
    final_recommendation = ""
    if session.messages and session.messages[-1]["role"] == "assistant":
        final_recommendation = session.messages[-1]["content"]
    
    session_data = {
        "session_id": session.session_id,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "conversation": session.conversation_history,
        "context_extracted": session.context_extracted,
        "weather_fetched": session.weather_fetched,
        "recommendation": final_recommendation,
        "tnps_score": payload.score,
        "tnps_verbatim": payload.verbatim,
        "metrics": session.metrics
    }
    
    # Save to JSON
    os.makedirs(f"data/sessions/{session_id}", exist_ok=True)
    json_path = f"data/sessions/{session_id}/{session_id}.json"
    with open(json_path, "w") as f:
        json.dump(session_data, f, indent=2)
        
    return {"status": "success", "file": json_path}

