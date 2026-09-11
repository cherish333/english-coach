import pytest
from fastapi.testclient import TestClient
from src.server import app

client = TestClient(app)

def test_websocket_connection_and_invalid_inputs():
    with client.websocket_connect("/ws/chat") as websocket:
        # Initial ready message or status check
        
        # Test 1: Send invalid JSON string
        websocket.send_text("NOT_A_VALID_JSON")
        resp1 = websocket.receive_json()
        assert resp1["type"] == "error"
        assert "Invalid JSON" in resp1["message"]

        # Test 2: Send non-dict JSON (e.g. integer or list)
        websocket.send_text("12345")
        resp2 = websocket.receive_json()
        assert resp2["type"] == "error"
        assert "must be a JSON object" in resp2["message"]

        # Test 3: Send empty or null lecture_sentence (previously caused AttributeError: 'NoneType' object has no attribute 'strip')
        websocket.send_json({"type": "lecture_sentence", "text": None})
        resp3 = websocket.receive_json()
        assert resp3["type"] == "error"
        assert "must be a string" in resp3["message"]

        # Test 4: Send whitespace-only lecture_sentence
        websocket.send_json({"type": "lecture_sentence", "text": "   "})
        resp4 = websocket.receive_json()
        assert resp4["type"] == "error"

        # Test 5: Send speech evaluation with None target
        websocket.send_json({"type": "evaluate_speech", "target_text": None, "spoken_text": None})
        resp5 = websocket.receive_json()
        assert resp5["type"] == "pronunciation_eval"
        assert "eval" in resp5

        # Test 6: Teaching style update
        websocket.send_json({"type": "set_teaching_style", "style": "ielts"})
        resp6 = websocket.receive_json()
        assert resp6["type"] == "teaching_style_updated"
        assert resp6["style"] == "ielts"
