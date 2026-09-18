import json
import os
import sys
import threading
import time

try:
    import websocket
except ModuleNotFoundError:  # pragma: no cover
    websocket = None


TOKEN_FILE = "vts_auth_token.txt"


def resolve_vts_ws_url():
    host = os.environ.get("VTS_HOST")
    port = os.environ.get("VTS_PORT")
    if host and port:
        return f"ws://{host}:{port}"
    if host:
        return f"ws://{host}:8001"
    return None


class VTSClient:
    def __init__(
        self,
        plugin_name="VoiceEmotionController",
        developer="LordLoks",
        token_path=None,
        ws_url="ws://127.0.0.1:8001",
        keepalive_interval=5,
    ):
        self.plugin_name = plugin_name
        self.developer = developer
        self.token_path = token_path or TOKEN_FILE
        self.ws_url = ws_url
        self.keepalive_interval = keepalive_interval
        self.ws = None
        self.connected = False
        self.authenticated = False
        self.last_url = None
        self.auth_token = None
        self._keepalive_thread = None
        self._keepalive_stop = threading.Event()
        self._lock = threading.Lock()

    def _load_token(self):
        try:
            if not os.path.exists(self.token_path):
                return None
            with open(self.token_path, "r", encoding="utf-8") as fh:
                token = fh.read().strip()
            return token or None
        except Exception:
            return None

    def _save_token(self, token):
        try:
            with open(self.token_path, "w", encoding="utf-8") as fh:
                fh.write(str(token).strip())
            return True
        except Exception:
            return False

    def _payload(self, message_type, data=None, request_id=None):
        return {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": request_id or f"vtube-{int(time.time() * 1000)}",
            "messageType": message_type,
            "data": data or {},
        }

    def _request(self, message_type, data=None, timeout=5.0):
        if self.ws is None:
            raise RuntimeError("VTube websocket is not connected")

        payload = self._payload(message_type, data=data)
        self.ws.settimeout(timeout)
        self.ws.send(json.dumps(payload, ensure_ascii=False))

        try:
            raw = self.ws.recv()
        except Exception as exc:
            raise RuntimeError(f"VTube Studio did not respond to {message_type}: {exc}") from exc

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw, "messageType": message_type}

    def _connect_ws(self):
        if websocket is None:
            raise RuntimeError("websocket-client is not installed. pip install websocket-client")

        if self.ws is not None and not getattr(self.ws, "closed", False):
            return self.ws

        env_url = resolve_vts_ws_url()
        candidates = []
        if env_url:
            candidates.append(env_url)
        if self.ws_url:
            candidates.append(self.ws_url)
        candidates.extend(["ws://127.0.0.1:8001", "ws://localhost:8001"])

        for ws_url in candidates:
            try:
                ws = websocket.create_connection(ws_url, timeout=2)
                self.ws = ws
                self.last_url = ws_url
                return ws
            except Exception as exc:
                print(f"[VTS_PLUGIN] {ws_url} unavailable: {exc}", file=sys.stderr)

        raise RuntimeError("Could not reach VTube Studio plugin API on the configured URL/ports.")

    def _state_ok(self):
        try:
            state = self._request("APIStateRequest", timeout=2.0)
            if isinstance(state, dict) and state.get("messageType") == "APIError":
                return False
            data = state.get("data", {}) if isinstance(state, dict) else {}
            return bool(data.get("currentSessionAuthenticated") is True or data.get("active") is True)
        except Exception:
            return False

    def _authenticate_once(self):
        if self.authenticated:
            return True

        saved_token = self._load_token()
        self.auth_token = saved_token

        if saved_token:
            try:
                auth_response = self._request(
                    "AuthenticationRequest",
                    {
                        "pluginName": self.plugin_name,
                        "pluginDeveloper": self.developer,
                        "authenticationToken": saved_token,
                    },
                    timeout=2.0,
                )
                if isinstance(auth_response, dict):
                    data = auth_response.get("data", {})
                    if auth_response.get("messageType") == "AuthenticationResponse" and data.get("authenticated") is True:
                        self.authenticated = True
                        self.connected = True
                        print("[VTS API] Reused saved authentication token without new approval.")
                        return True
            except Exception:
                self.auth_token = None

        try:
            token_response = self._request(
                "AuthenticationTokenRequest",
                {
                    "pluginName": self.plugin_name,
                    "pluginDeveloper": self.developer,
                    "pluginIcon": "",
                },
                timeout=5.0,
            )
            data = token_response.get("data", {}) if isinstance(token_response, dict) else {}
            token = data.get("authenticationToken")
            if not token:
                raise RuntimeError(f"No authentication token received: {token_response}")

            auth_response = self._request(
                "AuthenticationRequest",
                {
                    "pluginName": self.plugin_name,
                    "pluginDeveloper": self.developer,
                    "authenticationToken": token,
                },
                timeout=5.0,
            )

            if isinstance(auth_response, dict):
                data = auth_response.get("data", {})
                if auth_response.get("messageType") == "AuthenticationResponse" and data.get("authenticated") is True:
                    self.auth_token = token
                    self._save_token(token)
                    self.authenticated = True
                    self.connected = True
                    print("[VTS API] Authenticated successfully and token was saved.")
                    return True

            if isinstance(auth_response, dict) and auth_response.get("messageType") == "APIError":
                error_id = auth_response.get("data", {}).get("errorID")
                if error_id == 51:
                    print("[VTS API] Authentication already in progress. Wait until VTube Studio shows the approval window.")
                    return False

            raise RuntimeError(f"Unexpected auth response: {auth_response}")
        except Exception as exc:
            print(f"[VTS API] Initial authentication failed: {exc}")
            return False

    def connect(self):
        with self._lock:
            if self.connected and self.authenticated:
                self._start_keepalive()
                return True

            try:
                self._connect_ws()
            except Exception:
                return False

            saved_token = self._load_token()
            if saved_token:
                self.auth_token = saved_token
                if self._authenticate_once():
                    self.connected = True
                    self.authenticated = True
                    self._start_keepalive()
                    return True

            if self._state_ok() or self._authenticate_once():
                self.connected = True
                self.authenticated = True
                self._start_keepalive()
                return True

            self.connected = False
            self.authenticated = False
            return False

    def _start_keepalive(self):
        if self._keepalive_thread is not None and self._keepalive_thread.is_alive():
            return

        self._keepalive_stop.clear()

        def worker():
            while not self._keepalive_stop.is_set():
                try:
                    time.sleep(self.keepalive_interval)
                    if self.ws is None or getattr(self.ws, "closed", False):
                        break
                    self._request("APIStateRequest", timeout=2.0)
                except Exception:
                    self.authenticated = False
                    self.connected = False
                    try:
                        self.ws.close()
                    except Exception:
                        pass
                    self.ws = None
                    break

        self._keepalive_thread = threading.Thread(target=worker, daemon=True)
        self._keepalive_thread.start()

    def trigger_hotkey(self, hotkey_name):
        if not self.connect():
            raise RuntimeError("VTube Studio is not connected/authenticated")

        try:
            response = self._request("HotkeysInCurrentModelRequest", timeout=5.0)
            if not isinstance(response, dict):
                raise RuntimeError(f"Hotkeys response invalid: {response}")

            hotkeys = response.get("data", {}).get("availableHotkeys", [])
            hotkey_id = None
            for hk in hotkeys:
                if isinstance(hk, dict):
                    name = hk.get("name") or hk.get("hotkeyName")
                    if name and str(name).lower() == str(hotkey_name).lower():
                        hotkey_id = hk.get("id") or hk.get("hotkeyID") or hk.get("hotkeyId")
                        break

            if hotkey_id is None:
                raise RuntimeError(f"Hotkey '{hotkey_name}' was not found in the current model")

            trigger_response = self._request(
                "HotkeyTriggerRequest",
                {"hotkeyName": hotkey_name, "hotkeyID": hotkey_id},
                timeout=5.0,
            )
            if isinstance(trigger_response, dict) and trigger_response.get("messageType") == "APIError":
                raise RuntimeError(f"Hotkey trigger failed: {trigger_response}")

            print(f"[VTS API] Triggered hotkey: {hotkey_name}")
            return trigger_response
        except Exception as exc:
            print(f"[VTS API] Error while triggering hotkey: {exc}")
            self.authenticated = False
            self.connected = False
            raise

    def connect_and_auth(self):
        return self.connect()

    def close(self):
        self._keepalive_stop.set()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        finally:
            self.ws = None
            self.connected = False
            self.authenticated = False


class VTSController:
    def __init__(
        self,
        plugin_name="VoiceEmotionController",
        developer="LordLoks",
        token_path=None,
        ws_url="ws://127.0.0.1:8001",
        keepalive_interval=5,
    ):
        self.client = VTSClient(
            plugin_name=plugin_name,
            developer=developer,
            token_path=token_path or TOKEN_FILE,
            ws_url=ws_url,
            keepalive_interval=keepalive_interval,
        )

    def connect(self):
        try:
            return self.client.connect()
        except Exception:
            return False

    def trigger_hotkey(self, hotkey_name):
        try:
            return self.client.trigger_hotkey(hotkey_name)
        except Exception:
            return None

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass
