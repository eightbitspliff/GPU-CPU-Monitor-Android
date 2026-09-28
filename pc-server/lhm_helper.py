"""Startet den mitgelieferten SensorHelper (LibreHardwareMonitorLib) unter Windows
und stellt dessen Werte bereit: CPU-Temperatur, GPU-Leistungsaufnahme, -Takt, -Temperatur.

Für die CPU-Temperatur braucht es Administratorrechte und den Treiber PawnIO.
Die GPU-Werte kommen auch ohne beides.
"""

import json
import os
import subprocess
import sys
import threading
import time

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _find(*candidates):
    for c in candidates:
        p = os.path.join(HERE, *c)
        if os.path.isfile(p):
            return p
    return None


def helper_path():
    return _find(("sensor_helper", "SensorHelper.exe"), ("sensor_helper", "out", "SensorHelper.exe"))


def pawnio_setup_path():
    return _find(("PawnIO_setup.exe",), ("sensor_helper", "PawnIO_setup.exe"))


def is_admin():
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class LhmHelper:
    def __init__(self):
        self.data = {}
        self.error = None
        self._proc = None
        self._lock = threading.Lock()
        self.path = helper_path() if IS_WINDOWS else None
        if self.path:
            threading.Thread(target=self._run, daemon=True).start()

    @property
    def available(self):
        return self.path is not None

    def _run(self):
        failures = 0
        while failures < 5:
            started = time.time()
            try:
                with self._lock:
                    self._proc = subprocess.Popen(
                        [self.path], cwd=os.path.dirname(self.path),
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
                for line in self._proc.stdout:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in d:
                        self.error = d["error"]
                    else:
                        self.data = d
                        self.error = None
                self._proc.wait()
            except Exception as e:
                self.error = str(e)
            self.data = {}
            failures = 0 if time.time() - started > 60 else failures + 1
            time.sleep(2)

    def restart(self):
        """Nach der Treiberinstallation neu starten, damit er PawnIO findet."""
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def stop(self):
        self.path = None
        self.restart()

    # ------------------------------------------------------------------ Werte
    @property
    def admin(self):
        return bool(self.data.get("admin"))

    @property
    def pawnio(self):
        return bool(self.data.get("pawnio"))

    def cpu_temp(self):
        return self.data.get("cpu_temp")

    def cpu_power(self):
        return self.data.get("cpu_power")

    def gpus(self):
        return self.data.get("gpus") or []

    def needs_pawnio(self):
        """True, wenn nur der Treiber für die CPU-Temperatur fehlt."""
        return bool(self.data) and not self.pawnio and self.cpu_temp() is None

    def cpu_temp_note(self):
        """Hinweis, warum die CPU-Temperatur fehlt (oder None)."""
        if not IS_WINDOWS or self.cpu_temp() is not None:
            return None
        if not self.available:
            return "Sensor-Modul fehlt im PC-Server"
        if not self.data:
            return None  # startet noch
        if not self.admin:
            return "PC-Server als Administrator starten"
        if not self.pawnio:
            return "Im PC-Server-Fenster 'CPU-Temperatur aktivieren' klicken"
        return None

    def install_pawnio(self):
        """Installiert PawnIO still (braucht Adminrechte). -> (ok, Meldung)"""
        setup = pawnio_setup_path()
        if not setup:
            return False, "PawnIO-Installer fehlt in diesem Build."
        if not is_admin():
            return False, "PC Monitor Server bitte als Administrator starten."
        try:
            r = subprocess.run([setup, "-install", "-silent"], timeout=180,
                               stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except Exception as e:
            return False, f"Installation fehlgeschlagen: {e}"
        self.restart()
        if r.returncode not in (0, 3010):
            return False, f"Installation fehlgeschlagen (Code {r.returncode})."
        return True, "PawnIO installiert – CPU-Temperatur erscheint in wenigen Sekunden."


def merge_gpus(gpus, lhm_gpus):
    """Ergänzt fehlende GPU-Werte (Leistung, Takt, Temperatur) aus LibreHardwareMonitor."""
    if not lhm_gpus:
        return gpus
    if not gpus:
        return [{"name": g.get("name") or "GPU", "usage": g.get("usage"), "mem_used_mb": None,
                 "mem_total_mb": None, "temp_c": g.get("temp_c"), "power_w": g.get("power_w"),
                 "clock_mhz": g.get("clock_mhz"), "power_sources": g.get("powers")}
                for g in _discrete_first(lhm_gpus)]
    remaining = _discrete_first(lhm_gpus)
    for gpu in gpus:
        match = _match(gpu.get("name") or "", remaining)
        if match is None:
            continue
        remaining.remove(match)
        for key in ("clock_mhz", "temp_c"):
            if gpu.get(key) is None and match.get(key) is not None:
                gpu[key] = match[key]
        # Leistung: alle Sensoren sammeln, der höchste ist die Gesamtaufnahme der Karte
        sources = dict(gpu.get("power_sources") or {})
        if gpu.get("power_w") is not None and not sources:
            sources["Treiber"] = gpu["power_w"]
        for name, v in (match.get("powers") or {}).items():
            sources["LHM " + name] = v
        if match.get("power_w") is not None and not match.get("powers"):
            sources["LHM"] = match["power_w"]
        if sources:
            gpu["power_sources"] = sources
            gpu["power_w"] = max(sources.values())
    return gpus


def _discrete_first(lhm_gpus):
    # Integrierte Intel-Grafik ans Ende, die eigentliche Grafikkarte zuerst.
    return sorted(lhm_gpus, key=lambda g: g.get("vendor") == "intel")


def _match(name, candidates):
    if not candidates:
        return None
    n = name.lower()
    for c in candidates:
        cn = (c.get("name") or "").lower()
        if cn and (cn in n or n in cn):
            return c
    words = [w for w in n.replace("(r)", " ").replace("(tm)", " ").split() if len(w) > 2]
    for c in candidates:
        cn = (c.get("name") or "").lower()
        if sum(w in cn for w in words) >= 2:
            return c
    return candidates[0]
