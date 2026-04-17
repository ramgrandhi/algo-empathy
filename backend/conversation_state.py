from typing import Dict, List, Any
import json
import time

class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.turn_count = 0
        self.start_time = time.time()
        self.messages: List[Dict[str, str]] = []
        self.conversation_history: List[Dict[str, Any]] = []
        self.context_extracted = {
            "when": None,
            "where": None,
            "how_long": None,
            "transport": None
        }
        self.location = {"lat": None, "lon": None}
        self.weather_fetched = None
        self.metrics = {
            "turn_count": 0,
            "total_duration_ms": 0,
            "retry_count": 0,
            "sentiment_trajectory": []
        }

class ConversationManager:
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}

    def get_or_create_session(self, session_id: str) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState(session_id)
        return self.sessions[session_id]

    def add_user_message(self, session_id: str, transcript: str):
        session = self.get_or_create_session(session_id)
        session.messages.append({"role": "user", "content": transcript})
        session.turn_count += 1
        
    def add_assistant_message(self, session_id: str, text: str):
        session = self.get_or_create_session(session_id)
        session.messages.append({"role": "assistant", "content": text})

    def update_slots(self, session_id: str, new_slots: dict):
        session = self.get_or_create_session(session_id)
        for key in session.context_extracted.keys():
            if key in new_slots and new_slots[key] is not None:
                session.context_extracted[key] = new_slots[key]

# Global state manager for in-memory session lifetime
conversation_manager = ConversationManager()
