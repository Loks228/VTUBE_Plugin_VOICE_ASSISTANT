import importlib.util
import json
import sys
import types
from pathlib import Path


root = Path(__file__).resolve().parent
module_path = root / "code.py"

pyaudio_stub = types.ModuleType("pyaudio")
pyaudio_stub.PyAudio = object
pyaudio_stub.paInt16 = 8
sys.modules.setdefault("pyaudio", pyaudio_stub)

pydirectinput_stub = types.ModuleType("pydirectinput")
pydirectinput_stub.press = lambda *_args, **_kwargs: None
sys.modules.setdefault("pydirectinput", pydirectinput_stub)

vosk_stub = types.ModuleType("vosk")
vosk_stub.KaldiRecognizer = object
vosk_stub.Model = object
sys.modules.setdefault("vosk", vosk_stub)

spec = importlib.util.spec_from_file_location("vTubeVoiceScript", module_path)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_find_trigger_hotkey_matches_emotion_phrase():
    hotkey, confidence = app.find_trigger_hotkey("тихо")
    assert hotkey == "N4"
    assert confidence >= 0.75


def test_build_emotion_result_contains_vtube_emotion():
    result = app.build_emotion_result("тихо", "N4", 0.95)
    assert result["intent_found"] is True
    assert result["emotion"] == "NERVOUS"
    assert result["vts_hotkey_name"] == "Nervous"


def reset_vts_cache():
    app._VTS_WS_CACHE = {"ws": None, "url": None, "authenticated": False}


def test_trigger_vtube_emotion_uses_plugin_api(monkeypatch):
    reset_vts_cache()
    called = []

    def fake_send(vts_name, emotion):
        called.append((vts_name, emotion))
        return True

    monkeypatch.setattr(app, "send_vtube_plugin_message", fake_send)

    result = app.trigger_vtube_emotion("N4")

    assert result["emotion"] == "NERVOUS"
    assert result["performed"] is True
    assert called == [("Nervous", "NERVOUS")]


def test_send_vtube_plugin_message_authenticates_before_hotkey_trigger(monkeypatch):
    reset_vts_cache()
    monkeypatch.setattr(app, "_VTS_CONTROLLER", None)
    sent = []
    responses = iter([
        {"messageType": "APIStateResponse", "data": {"active": True, "currentSessionAuthenticated": False}},
        {"messageType": "AuthenticationTokenResponse", "data": {"authenticationToken": "mock-token"}},
        {"messageType": "AuthenticationResponse", "data": {"authenticated": True}},
        {"messageType": "HotkeysInCurrentModelResponse", "data": {"availableHotkeys": [{"name": "Numpad4"}]}},
        {"messageType": "HotkeyTriggerResponse", "data": {"hotkeyID": "Numpad4"}},
    ])

    class FakeWS:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            sent.append(json.loads(payload))

        def recv(self):
            current = next(responses)
            return json.dumps(current)

        def close(self):
            self.closed = True

    fake_ws = FakeWS()

    monkeypatch.setattr(app, "websocket", types.SimpleNamespace(create_connection=lambda *args, **kwargs: fake_ws))

    result = app.send_vtube_plugin_message("Numpad4", "NERVOUS")

    assert result is True
    assert [message["messageType"] for message in sent] == [
        "APIStateRequest",
        "AuthenticationTokenRequest",
        "AuthenticationRequest",
        "HotkeysInCurrentModelRequest",
        "HotkeyTriggerRequest",
    ]
    assert sent[-1]["data"]["hotkeyName"] == "Numpad4"


def test_send_vtube_plugin_message_uses_hotkey_id_when_available(monkeypatch):
    reset_vts_cache()
    monkeypatch.setattr(app, "_VTS_CONTROLLER", None)
    sent = []
    responses = iter([
        {"messageType": "APIStateResponse", "data": {"active": True, "currentSessionAuthenticated": False}},
        {"messageType": "AuthenticationTokenResponse", "data": {"authenticationToken": "mock-token"}},
        {"messageType": "AuthenticationResponse", "data": {"authenticated": True}},
        {"messageType": "HotkeysInCurrentModelResponse", "data": {"availableHotkeys": [{"id": "hotkey-123", "name": "Angry"}]}},
        {"messageType": "HotkeyTriggerResponse", "data": {"hotkeyID": "hotkey-123"}},
    ])

    class FakeWS:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            sent.append(json.loads(payload))

        def recv(self):
            current = next(responses)
            return json.dumps(current)

        def close(self):
            self.closed = True

    fake_ws = FakeWS()

    monkeypatch.setattr(app, "websocket", types.SimpleNamespace(create_connection=lambda *args, **kwargs: fake_ws))

    result = app.send_vtube_plugin_message("Angry", "ANGRY")

    assert result is True
    assert sent[-1]["data"]["hotkeyName"] == "Angry"
    assert sent[-1]["data"]["hotkeyID"] == "hotkey-123"


def test_authentication_reuses_session_when_already_authenticated(monkeypatch):
    reset_vts_cache()
    monkeypatch.setattr(app, "_VTS_CONTROLLER", None)
    sent = []
    responses = iter([
        {"messageType": "APIStateResponse", "data": {"active": True, "currentSessionAuthenticated": True}},
        {"messageType": "HotkeysInCurrentModelResponse", "data": {"availableHotkeys": [{"id": "hotkey-123", "name": "Angry"}]}},
        {"messageType": "HotkeyTriggerResponse", "data": {"hotkeyID": "hotkey-123"}},
    ])

    class FakeWS:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            sent.append(json.loads(payload))

        def recv(self):
            current = next(responses)
            return json.dumps(current)

        def close(self):
            self.closed = True

    fake_ws = FakeWS()

    monkeypatch.setattr(app, "websocket", types.SimpleNamespace(create_connection=lambda *args, **kwargs: fake_ws))

    result = app.send_vtube_plugin_message("Angry", "ANGRY")

    assert result is True
    assert [message["messageType"] for message in sent] == [
        "APIStateRequest",
        "HotkeysInCurrentModelRequest",
        "HotkeyTriggerRequest",
    ]


def test_reuses_cached_vtube_connection_between_calls(monkeypatch):
    reset_vts_cache()
    monkeypatch.setattr(app, "_VTS_CONTROLLER", None)
    sent = []
    responses = iter([
        {"messageType": "APIStateResponse", "data": {"active": True, "currentSessionAuthenticated": True}},
        {"messageType": "HotkeysInCurrentModelResponse", "data": {"availableHotkeys": [{"id": "hotkey-123", "name": "Angry"}]}},
        {"messageType": "HotkeyTriggerResponse", "data": {"hotkeyID": "hotkey-123"}},
        {"messageType": "APIStateResponse", "data": {"active": True, "currentSessionAuthenticated": True}},
        {"messageType": "HotkeysInCurrentModelResponse", "data": {"availableHotkeys": [{"id": "hotkey-123", "name": "Angry"}]}},
        {"messageType": "HotkeyTriggerResponse", "data": {"hotkeyID": "hotkey-123"}},
    ])

    class FakeWS:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            sent.append(json.loads(payload))

        def recv(self):
            current = next(responses)
            return json.dumps(current)

        def close(self):
            self.closed = True

    fake_ws = FakeWS()
    create_calls = []

    def fake_create_connection(*args, **kwargs):
        create_calls.append(args[0])
        return fake_ws

    monkeypatch.setattr(app, "websocket", types.SimpleNamespace(create_connection=fake_create_connection))
    app._VTS_WS_CACHE = {"ws": None, "url": None, "authenticated": False}

    first = app.send_vtube_plugin_message("Angry", "ANGRY")
    second = app.send_vtube_plugin_message("Angry", "ANGRY")

    assert first is True
    assert second is True
    assert len(create_calls) == 1


def test_reconnects_after_broken_cached_socket(monkeypatch):
    reset_vts_cache()
    monkeypatch.setattr(app, "_VTS_CONTROLLER", None)
    responses = iter([
        {"messageType": "APIStateResponse", "data": {"active": True, "currentSessionAuthenticated": False}},
        {"messageType": "AuthenticationTokenResponse", "data": {"authenticationToken": "token-1"}},
        {"messageType": "AuthenticationResponse", "data": {"authenticated": True}},
        {"messageType": "HotkeysInCurrentModelResponse", "data": {"availableHotkeys": [{"id": "hotkey-123", "name": "Angry"}]}},
        {"messageType": "HotkeyTriggerResponse", "data": {"hotkeyID": "hotkey-123"}},
    ])

    class FirstBrokenWS:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            raise OSError(10053, "Program on your host computer has disconnected")

        def close(self):
            self.closed = True

    class WorkingWS:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            pass

        def recv(self):
            current = next(responses)
            return json.dumps(current)

        def close(self):
            self.closed = True

    create_calls = []
    sockets = [FirstBrokenWS(), WorkingWS()]

    def fake_create_connection(*args, **kwargs):
        create_calls.append(args[0])
        return sockets.pop(0)

    monkeypatch.setattr(app, "websocket", types.SimpleNamespace(create_connection=fake_create_connection))

    result = app.send_vtube_plugin_message("Angry", "ANGRY")

    assert result is True
    assert len(create_calls) == 2


def test_vts_client_reuses_saved_token_without_new_approval(monkeypatch, tmp_path):
    import vts_manager

    token_path = tmp_path / "vts_auth_token.txt"
    token_path.write_text("saved-token", encoding="utf-8")

    sent = []
    responses = iter([
        {"messageType": "AuthenticationResponse", "data": {"authenticated": True}},
    ])

    class FakeSocket:
        def __init__(self):
            self.closed = False

        def settimeout(self, *_args, **_kwargs):
            return None

        def send(self, payload):
            sent.append(json.loads(payload))

        def recv(self):
            return json.dumps(next(responses))

        def close(self):
            self.closed = True

    monkeypatch.setattr(vts_manager, "websocket", types.SimpleNamespace(create_connection=lambda *args, **kwargs: FakeSocket()))

    client = vts_manager.VTSClient(token_path=str(token_path))
    assert client.connect() is True
    assert client.authenticated is True
    assert client.auth_token == "saved-token"
    assert sent[0]["messageType"] == "AuthenticationRequest"
    assert sent[0]["data"]["authenticationToken"] == "saved-token"


def test_vts_ws_url_uses_lan_host_from_environment(monkeypatch):
    monkeypatch.setenv("VTS_HOST", "192.168.1.50")
    monkeypatch.setenv("VTS_PORT", "8001")
    import vts_manager

    url = vts_manager.resolve_vts_ws_url()
    assert url == "ws://192.168.1.50:8001"
