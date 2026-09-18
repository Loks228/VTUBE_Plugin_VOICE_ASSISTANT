import json
import re
import sys
import time
import traceback
from difflib import SequenceMatcher
from pathlib import Path

try:
    import websocket
except ModuleNotFoundError:
    websocket = None

try:
    from vts_manager import VTSController
except Exception:  # pragma: no cover
    VTSController = None

try:
    import pyaudio
except ModuleNotFoundError:
    pyaudio = None

try:
    import pydirectinput
except ModuleNotFoundError:
    pydirectinput = None

try:
    from vosk import KaldiRecognizer, Model
except ModuleNotFoundError:
    KaldiRecognizer = None
    Model = None

ROOT_DIR = Path(__file__).resolve().parent
MODEL_PATH = ROOT_DIR / "VoiceAssistant" / "vosk_models" / "vosk-model-small-ru-0.22"
GLOBAL_COOLDOWN = 1.0

NUMPAD_MAP = {
    "N1": "Angry",
    "N2": "Blush",
    "N3": "HeartEyes",
    "N4": "Nervous",
    "N5": "Sad",
    "N6": "Lightsticks",
}

VTS_PLUGIN_PORTS = [8001, 8001, 8002, 8003, 8004, 8005]
_VTS_WS_CACHE = {"ws": None, "url": None, "authenticated": False}
_VTS_CONTROLLER = VTSController() if VTSController is not None else None


def _reset_vts_ws_cache():
    cached_ws = _VTS_WS_CACHE.get("ws")
    if cached_ws is not None:
        try:
            cached_ws.close()
        except Exception:
            pass
    _VTS_WS_CACHE.clear()
    _VTS_WS_CACHE.update({"ws": None, "url": None, "authenticated": False})

EMOTION_MAP = {
    "N1": {"emotion": "ANGRY", "vts_hotkey_name": "Angry"},
    "N2": {"emotion": "BLUSH", "vts_hotkey_name": "Blush"},
    "N3": {"emotion": "HEARTEYES", "vts_hotkey_name": "HeartEyes"},
    "N4": {"emotion": "NERVOUS", "vts_hotkey_name": "Nervous"},
    "N5": {"emotion": "SAD", "vts_hotkey_name": "Sad"},
    "N6": {"emotion": "SPARKLE", "vts_hotkey_name": "Lightsticks"},
}

TRIGGERS = {
    "да как так": "N1",
    "откуда": "N1",
    "сука": "N1",
    "та за шо": "N1",
    "какого хрена": "N1",
    "ой мля": "N2",
    "я тупой": "N2",
    "что я сделал": "N2",
    "упс": "N2",
    "та блин": "N2",
    "какая сладость": "N3",
    "опа лутаемся": "N3",
    "кайф": "N3",
    "красота": "N3",
    "топчик": "N3",
    "вижу типа": "N4",
    "кто-то есть": "N4",
    "тихо": "N4",
    "шаги": "N4",
    "опа кто там": "N4",
    "все пропало": "N5",
    "минус сет": "N5",
    "печаль": "N5",
    "боль": "N5",
    "лежать": "N6",
    "минус один": "N6",
    "легчайшая": "N6",
    "победа": "N6",
    "домой": "N6",
}


def find_microphone_index():
    pa = pyaudio.PyAudio()
    try:
        info = pa.get_default_input_device_info()
        return int(info.get("index", 0)), info.get("name", "Default microphone")
    except Exception:
        pass

    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                return int(info.get("index", i)), info.get("name", f"Microphone {i}")
    except Exception:
        pass

    return None, None


def normalize_text(text):
    if not text:
        return ""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^а-я0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_trigger_hotkey(text):
    normalized_text = normalize_text(text)
    if not normalized_text:
        return None, 0.0

    best_hotkey = None
    best_confidence = 0.0

    for phrase, hotkey in TRIGGERS.items():
        normalized_phrase = normalize_text(phrase)

        if normalized_phrase in normalized_text:
            confidence = 0.95
        elif normalized_text in normalized_phrase:
            confidence = 0.92
        else:
            phrase_tokens = normalized_phrase.split()
            text_tokens = normalized_text.split()
            if phrase_tokens and all(token in text_tokens for token in phrase_tokens):
                confidence = 0.88
            elif phrase_tokens and len(phrase_tokens) >= 2:
                confidence = SequenceMatcher(None, normalized_text, normalized_phrase).ratio()
            else:
                confidence = 0.0

        if confidence >= 0.75 and (best_hotkey is None or confidence > best_confidence):
            best_hotkey = hotkey
            best_confidence = confidence

    return best_hotkey, best_confidence


def build_emotion_result(recognized_text, hotkey, confidence):
    metadata = EMOTION_MAP.get(hotkey)
    if not hotkey or not metadata:
        return {
            "recognized_text": recognized_text,
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        }

    return {
        "recognized_text": recognized_text,
        "intent_found": True,
        "hotkey": hotkey,
        "vts_hotkey_name": metadata["vts_hotkey_name"],
        "emotion": metadata["emotion"],
        "confidence": round(float(confidence), 2),
    }


def _vts_payload(message_type, data=None, request_id=None):
    return {
        "apiName": "VTubeStudioPublicAPI",
        "apiVersion": "1.0",
        "requestID": request_id or f"vtube-{int(time.time() * 1000)}",
        "messageType": message_type,
        "data": data or {},
    }


def _vts_request(ws, message_type, data=None, timeout=3.0):
    if hasattr(ws, "settimeout"):
        ws.settimeout(timeout)
    payload = _vts_payload(message_type, data=data)
    ws.send(json.dumps(payload, ensure_ascii=False))
    try:
        raw = ws.recv()
    except Exception as exc:
        raise RuntimeError(f"VTube Studio did not respond to {message_type}: {exc}") from exc

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw, "messageType": message_type}


def _authenticate_vts_plugin(ws):
    try:
        if _VTS_WS_CACHE.get("authenticated") is True:
            return True

        api_state = _vts_request(ws, "APIStateRequest", timeout=2.0)
        if isinstance(api_state, dict) and api_state.get("messageType") == "APIError":
            print(f"[VTS_PLUGIN_ERROR] APIStateRequest failed: {api_state}", file=sys.stderr)

        state_data = api_state.get("data", {}) if isinstance(api_state, dict) else {}
        if state_data.get("currentSessionAuthenticated") is True:
            _VTS_WS_CACHE["authenticated"] = True
            print("[VTS_PLUGIN] Session already authenticated with VTube Studio", file=sys.stderr)
            return True

        token_request = _vts_request(
            ws,
            "AuthenticationTokenRequest",
            {
                "pluginName": "VTUBE Voice Assistant",
                "pluginDeveloper": "VTUBE",
                "pluginIcon": "",
            },
            timeout=5.0,
        )
        if isinstance(token_request, dict) and token_request.get("messageType") == "APIError":
            error_data = token_request.get("data", {})
            if error_data.get("errorID") == 51:
                print(f"[VTS_PLUGIN] Authentication already in progress in VTube Studio: {token_request}", file=sys.stderr)
                return False

        data = token_request.get("data", {}) if isinstance(token_request, dict) else {}
        token = data.get("authenticationToken")
        if not token:
            print(f"[VTS_PLUGIN_ERROR] No authentication token from VTube Studio: {token_request}", file=sys.stderr)
            return False

        auth_response = _vts_request(
            ws,
            "AuthenticationRequest",
            {
                "pluginName": "VTUBE Voice Assistant",
                "pluginDeveloper": "VTUBE",
                "authenticationToken": token,
            },
            timeout=5.0,
        )

        if isinstance(auth_response, dict):
            message_type = auth_response.get("messageType")
            data = auth_response.get("data", {})
            if message_type == "APIError":
                print(f"[VTS_PLUGIN_ERROR] Authentication failed: {auth_response}", file=sys.stderr)
                return False
            if message_type == "AuthenticationResponse" and data.get("authenticated") is True:
                _VTS_WS_CACHE["authenticated"] = True
                print("[VTS_PLUGIN] Authenticated with VTube Studio", file=sys.stderr)
                return True

        print(f"[VTS_PLUGIN_ERROR] Unexpected authentication response: {auth_response}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[VTS_PLUGIN_ERROR] Authentication failed: {exc}", file=sys.stderr)
        return False


def _fetch_vts_hotkeys(ws, include_metadata=False):
    try:
        response = _vts_request(ws, "HotkeysInCurrentModelRequest", timeout=5.0)
        if not isinstance(response, dict):
            return [] if not include_metadata else []
        data = response.get("data", {})
        hotkeys = data.get("availableHotkeys", [])
        items = []
        for item in hotkeys:
            if isinstance(item, dict):
                hotkey_id = item.get("id") or item.get("hotkeyID") or item.get("hotkeyId")
                name = item.get("name") or item.get("hotkeyName") or item.get("hotkeyId") or hotkey_id
                if name:
                    items.append({
                        "name": str(name),
                        "id": str(hotkey_id) if hotkey_id is not None else "",
                    })
            elif isinstance(item, str):
                items.append({"name": str(item), "id": ""})

        if include_metadata:
            return items
        return [item["name"] for item in items]
    except Exception as exc:
        print(f"[VTS_PLUGIN_ERROR] Hotkey listing failed: {exc}", file=sys.stderr)
        return [] if not include_metadata else []


def _connect_vts_websocket():
    cached_ws = _VTS_WS_CACHE.get("ws")
    if cached_ws is not None and not getattr(cached_ws, "closed", False):
        return cached_ws, _VTS_WS_CACHE.get("url")

    for port in VTS_PLUGIN_PORTS:
        for ws_url in (f"ws://127.0.0.1:{port}", f"ws://localhost:{port}"):
            try:
                ws = websocket.create_connection(ws_url, timeout=2)
                _VTS_WS_CACHE["ws"] = ws
                _VTS_WS_CACHE["url"] = ws_url
                _VTS_WS_CACHE["authenticated"] = False
                return ws, ws_url
            except Exception as exc:
                print(f"[VTS_PLUGIN] {ws_url} unavailable: {exc}", file=sys.stderr)

    return None, None


def send_vtube_plugin_message(vts_name, emotion):
    if _VTS_CONTROLLER is not None:
        try:
            if _VTS_CONTROLLER.connect():
                _VTS_CONTROLLER.trigger_hotkey(vts_name)
                return True
        except Exception as exc:
            print(f"[VTS_PLUGIN] fallback to direct WebSocket flow: {exc}", file=sys.stderr)

    if websocket is None:
        print("[VTS_PLUGIN_ERROR] websocket-client is not installed. pip install websocket-client", file=sys.stderr)
        return False

    for attempt in range(2):
        ws, ws_url = _connect_vts_websocket()
        if ws is None:
            print(f"[VTS_PLUGIN_ERROR] Could not reach VTube Studio plugin API on ports {VTS_PLUGIN_PORTS}; enable 'Start API (allow plugins)' in VTube Studio and use 127.0.0.1/localhost as the host. If the API is already running, check plugin permissions and the actual port shown in the UI.", file=sys.stderr)
            return False

        try:
            if not _authenticate_vts_plugin(ws):
                if attempt == 0:
                    _reset_vts_ws_cache()
                    continue
                return False

            hotkey_details = _fetch_vts_hotkeys(ws, include_metadata=True)
            available_hotkeys = [item["name"] for item in hotkey_details]
            if available_hotkeys:
                print(f"[VTS_PLUGIN] available hotkeys: {available_hotkeys}", file=sys.stderr)
                if vts_name not in available_hotkeys and vts_name.lower() not in [item.lower() for item in available_hotkeys]:
                    print(f"[VTS_PLUGIN_ERROR] Requested hotkey '{vts_name}' was not found in the current model. Use one of: {available_hotkeys}", file=sys.stderr)

            matching_hotkey = None
            for item in hotkey_details:
                if item.get("name", "").lower() == str(vts_name).lower():
                    matching_hotkey = item
                    break

            trigger_payload = {"hotkeyName": vts_name}
            if matching_hotkey and matching_hotkey.get("id"):
                trigger_payload["hotkeyID"] = matching_hotkey["id"]

            trigger_request = _vts_request(
                ws,
                "HotkeyTriggerRequest",
                trigger_payload,
                timeout=5.0,
            )
            response_type = trigger_request.get("messageType") if isinstance(trigger_request, dict) else None
            if response_type == "APIError":
                print(f"[VTS_PLUGIN_ERROR] Hotkey trigger failed: {trigger_request}", file=sys.stderr)
                return False
            if response_type in {"HotkeyTriggerResponse", "HotkeyTriggerResponse"}:
                print(f"[VTS_PLUGIN] {ws_url} -> {trigger_request}", file=sys.stderr)
                return True
            print(f"[VTS_PLUGIN] {ws_url} -> {trigger_request}", file=sys.stderr)
            return bool(trigger_request)
        except Exception as exc:
            if attempt == 0 and ("10053" in str(exc) or "broken pipe" in str(exc).lower() or "connection" in str(exc).lower() and "timed out" not in str(exc).lower()):
                _reset_vts_ws_cache()
                continue
            print(f"[VTS_PLUGIN] trigger failed on {ws_url}: {exc}", file=sys.stderr)
            return False

    return False


def trigger_vtube_emotion(hotkey):
    metadata = EMOTION_MAP.get(hotkey)
    if not hotkey or not metadata:
        return {
            "hotkey": hotkey,
            "vts_hotkey_name": None,
            "emotion": None,
            "performed": False,
        }

    vts_name = metadata.get("vts_hotkey_name") or NUMPAD_MAP.get(hotkey)
    if not vts_name:
        return {
            "hotkey": hotkey,
            "vts_hotkey_name": None,
            "emotion": metadata.get("emotion"),
            "performed": False,
        }

    try:
        performed = send_vtube_plugin_message(vts_name, metadata.get("emotion"))
        print(f"[VTUBE] {metadata.get('emotion')} -> {vts_name} via plugin API", file=sys.stderr)
        return {
            "hotkey": hotkey,
            "vts_hotkey_name": vts_name,
            "emotion": metadata.get("emotion"),
            "performed": performed,
        }
    except Exception as exc:
        print(f"[VTUBE_ERROR] {exc}", file=sys.stderr)
        return {
            "hotkey": hotkey,
            "vts_hotkey_name": vts_name,
            "emotion": metadata.get("emotion"),
            "performed": False,
        }


def send_keyboard_key(hotkey):
    return trigger_vtube_emotion(hotkey)


def emit_json(payload):
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main():
    last_trigger_time = 0.0
    model_dir = str(MODEL_PATH)

    if not MODEL_PATH.exists():
        emit_json({
            "recognized_text": "",
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        })
        print(f"[ERROR] Model directory not found: {model_dir}", file=sys.stderr)
        return

    if pyaudio is None:
        emit_json({
            "recognized_text": "",
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        })
        print("[ERROR] pyaudio is not installed. Install the package: pip install pyaudio", file=sys.stderr)
        return

    if pydirectinput is None:
        emit_json({
            "recognized_text": "",
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        })
        print("[ERROR] pydirectinput is not installed. Install the package: pip install pydirectinput", file=sys.stderr)
        return

    if Model is None or KaldiRecognizer is None:
        emit_json({
            "recognized_text": "",
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        })
        print("[ERROR] vosk is not installed. Install the package: pip install vosk", file=sys.stderr)
        return

    try:
        model = Model(model_dir)
    except Exception as exc:
        emit_json({
            "recognized_text": "",
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        })
        print(f"[ERROR] Failed to load Vosk model: {exc}", file=sys.stderr)
        return

    mic_index, mic_name = find_microphone_index()
    if mic_index is None or mic_name is None:
        emit_json({
            "recognized_text": "",
            "intent_found": False,
            "hotkey": None,
            "vts_hotkey_name": None,
            "emotion": None,
            "confidence": 0.0,
        })
        print("[ERROR] Microphone not found", file=sys.stderr)
        return

    print(f"[READY] Microphone: {mic_name}", file=sys.stderr)
    recognizer = KaldiRecognizer(model, 16000)
    recognizer.SetWords(True)

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        input_device_index=mic_index,
        frames_per_buffer=4000,
    )

    try:
        while True:
            data = stream.read(4000, exception_on_overflow=False)
            if not data:
                continue

            if recognizer.AcceptWaveform(data):
                payload = json.loads(recognizer.Result())
                text = payload.get("text", "").strip()
                if not text:
                    continue

                print(f"[STT] {text}", file=sys.stderr)
                current_time = time.time()
                if current_time - last_trigger_time < GLOBAL_COOLDOWN:
                    continue

                hotkey, confidence = find_trigger_hotkey(text)
                result = build_emotion_result(text, hotkey, confidence)
                if result["intent_found"]:
                    emit_json(result)
                    trigger_vtube_emotion(hotkey)
                    last_trigger_time = time.time()
                else:
                    emit_json(result)
    except KeyboardInterrupt:
        print("[EXIT] User stopped the script", file=sys.stderr)
    except Exception:
        print("[ERROR] Runtime error", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


if __name__ == "__main__":
    main()