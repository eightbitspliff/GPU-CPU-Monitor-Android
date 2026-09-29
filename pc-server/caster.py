"""Zeigt das Web-Dashboard dauerhaft auf einem Google Nest Hub / Chromecast an.

Nutzt die Cast-App "DashCast", die eine beliebige Webseite auf dem Gerät öffnet.
Ein Hintergrund-Thread prüft regelmäßig, ob die Anzeige noch läuft, und startet
sie neu, sobald das Gerät wieder im Ruhezustand ist (z.B. nach einem Timeout).
Hat jemand gerade etwas anderes auf dem Gerät gestartet (Musik, Wetter, …),
wird das nicht unterbrochen.
"""

import json
import os
import socket
import threading

try:
    import pychromecast
    from pychromecast.config import APP_DASHCAST
    from pychromecast.controllers.dashcast import DashCastController
    AVAILABLE = True
except Exception:  # Paket fehlt
    AVAILABLE = False

CHECK_INTERVAL = 20  # Sekunden


def config_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "PCMonitor")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


def _load_config():
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _local_ip_towards(host):
    """Eigene IP im selben Netz wie das Cast-Gerät."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


class Caster:
    def __init__(self, http_port):
        self.http_port = http_port
        self.lock = threading.RLock()
        self.cfg = _load_config()
        self.target = self.cfg.get("cast_device")  # Name des Geräts oder None
        self.devices = []       # zuletzt gefundene Geräte (CastInfo)
        self.cast = None        # verbundenes Gerät
        self.dash = None
        self.force_launch = bool(self.target)
        self.status = "Aus" if not self.target else "Verbinde…"
        self.listeners = []
        self._wake = threading.Event()
        if AVAILABLE:
            threading.Thread(target=self._loop, daemon=True, name="caster").start()
        else:
            self.status = "Nicht verfügbar (pychromecast fehlt)"

    # ------------------------------------------------------------ öffentlich
    def discover(self, timeout=6):
        """Sucht Cast-Geräte im Netzwerk. Gibt eine Liste von Dicts zurück."""
        if not AVAILABLE:
            return []
        infos, browser = pychromecast.discovery.discover_chromecasts(timeout=timeout)
        pychromecast.discovery.stop_discovery(browser)
        infos = sorted(infos, key=lambda i: (i.friendly_name or "").lower())
        with self.lock:
            self.devices = infos
        return [{"name": i.friendly_name, "model": i.model_name, "host": i.host} for i in infos]

    def start(self, device_name):
        with self.lock:
            if self.cast is not None and self._name(self.cast) != device_name:
                self._disconnect(quit_app=True)
            self.target = device_name
            self.force_launch = True
            self.cfg["cast_device"] = device_name
            _save_config(self.cfg)
            self._set_status(f"Starte auf {device_name}…")
        self._wake.set()

    def stop(self):
        with self.lock:
            self.target = None
            self.cfg.pop("cast_device", None)
            _save_config(self.cfg)
            self._disconnect(quit_app=True)
            self._set_status("Aus")

    def shutdown(self):
        """Beim Beenden des Servers: Anzeige auf dem Gerät schließen und Verbindung trennen.
        Das Gerät bleibt gespeichert, beim nächsten Start wird wieder verbunden."""
        def work():
            with self.lock:
                self.target = None  # Hintergrund-Thread soll nicht neu verbinden
                self._disconnect(quit_app=True)
        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(4)  # nicht ewig auf ein nicht erreichbares Gerät warten

    def state(self):
        with self.lock:
            return {"available": AVAILABLE, "active": self.target, "status": self.status}

    # ------------------------------------------------------------ intern
    def _set_status(self, text):
        self.status = text
        for cb in list(self.listeners):
            try:
                cb(text)
            except Exception:
                pass

    @staticmethod
    def _name(cast):
        return cast.cast_info.friendly_name

    def _disconnect(self, quit_app=False):
        c = self.cast
        self.cast = None
        self.dash = None
        if c is None:
            return
        try:
            if quit_app and c.app_id == APP_DASHCAST:
                c.quit_app()
        except Exception:
            pass
        try:
            c.disconnect(timeout=3)
        except Exception:
            pass

    def _connect(self, name):
        info = next((i for i in self.devices if i.friendly_name == name), None)
        if info is None:
            self.discover()
            info = next((i for i in self.devices if i.friendly_name == name), None)
        if info is None:
            raise RuntimeError(f"'{name}' nicht gefunden – ist das Gerät an und im selben WLAN?")
        cast = pychromecast.get_chromecast_from_host(
            (info.host, info.port, info.uuid, info.model_name, info.friendly_name),
            tries=2, retry_wait=3, timeout=10)
        cast.wait(timeout=10)
        dash = DashCastController()
        cast.register_handler(dash)
        self.cast, self.dash = cast, dash

    def _url_for(self, cast):
        ip = _local_ip_towards(cast.cast_info.host)
        return f"http://{ip}:{self.http_port}/?cast=1"

    def _tick(self):
        with self.lock:
            name = self.target
            if not name:
                return
            if self.cast is None or not self.cast.socket_client.is_connected:
                self._disconnect()
                self._connect(name)
            cast = self.cast
            if cast.app_id == APP_DASHCAST and not self.force_launch:
                self._set_status(f"Läuft auf {name}")
                return
            if self.force_launch or cast.is_idle:
                url = self._url_for(cast)
                self.dash.load_url(url, force=True)
                self.force_launch = False
                self._set_status(f"Läuft auf {name}")
            else:
                other = cast.app_display_name or "eine andere App"
                self._set_status(f"{name} zeigt gerade {other} – starte danach wieder")

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                with self.lock:
                    self._disconnect()
                    if self.target:
                        self._set_status(f"Fehler: {e} – neuer Versuch gleich")
            self._wake.wait(CHECK_INTERVAL)
            self._wake.clear()
