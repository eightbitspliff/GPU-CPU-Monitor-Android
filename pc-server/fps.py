"""Aktuelle FPS des Programms im Vordergrund (Spiel) über Intel PresentMon.

PresentMon (MIT-Lizenz) wertet die Windows-Grafikereignisse (ETW) aus und liefert
für jedes dargestellte Bild eine CSV-Zeile. Es läuft nur, solange jemand die
Anzeige ansieht (start()/stop() durch den Sampler), und braucht Adminrechte.
"""

import csv
import os
import subprocess
import sys
import threading
import time
from collections import deque

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
SESSION = "PCMonitorFPS"
WINDOW = 1.0  # Sekunden, über die die FPS gemittelt werden

# Diese Prozesse zeichnen ständig, sind aber keine Spiele
_IGNORE = {"dwm.exe", "pcmonitorserver.exe", "presentmon.exe", "explorer.exe",
           "searchhost.exe", "startmenuexperiencehost.exe", "textinputhost.exe"}


# Programme, die zwar Bilder zeichnen, aber keine Spiele sind: Sie werden nur angezeigt,
# wenn gerade kein Spiel läuft (z.B. im Fenstermodus kurz in den Browser gewechselt)
_DESKTOP_APPS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "discord",
                 "steamwebhelper", "steam", "spotify", "claude", "code", "teams", "ms-teams",
                 "slack", "whatsapp", "telegram", "obs64", "vlc", "explorer", "applicationframehost",
                 "msedgewebview2", "epicgameslauncher", "battle.net", "eadesktop", "ubisoftconnect",
                 "nvidia app", "nvidia overlay", "radeonsoftware", "pcmonitorserver"}


def _app_key(name):
    n = (name or "").lower()
    return n[:-4] if n.endswith(".exe") else n


def presentmon_path():
    for p in (os.path.join(HERE, "PresentMon.exe"), os.path.join(HERE, "presentmon", "PresentMon.exe")):
        if os.path.isfile(p):
            return p
    return None


def _foreground_pid():
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


class FpsMonitor:
    def __init__(self):
        self.path = presentmon_path() if IS_WINDOWS else None
        self._proc = None
        self._lock = threading.Lock()
        # je Prozess: deque[(Ankunftszeit, Bildzeit in ms)], Name
        self._frames = {}
        self._names = {}
        self.error = None
        self.last_message = ""
        self._last_fg = None

    @property
    def available(self):
        return self.path is not None

    # ------------------------------------------------------------ Steuerung
    def start(self):
        if not self.path or (self._proc and self._proc.poll() is None):
            return
        try:
            self._proc = subprocess.Popen(
                [self.path, "--output_stdout", "--no_console_stats", "--stop_existing_session",
                 "--session_name", SESSION, "--v1_metrics",
                 "--no_track_display", "--no_track_input", "--no_track_gpu"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except Exception as e:
            self.error = str(e)
            self._proc = None
            return
        threading.Thread(target=self._read, args=(self._proc,), daemon=True, name="fps").start()

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        with self._lock:
            self._frames.clear()

    # ------------------------------------------------------------ Einlesen
    def _read(self, proc):
        # Kopfzeile suchen (PresentMon kann vorher Hinweise ausgeben), danach immer
        # weiterlesen – sonst läuft die Pipe voll und PresentMon bleibt stehen.
        cols = None
        i_app = i_pid = i_ms = None
        self.error = "warte auf PresentMon…"
        for line in proc.stdout:
            if cols is None:
                low = line.lower()
                if "processid" in low and ("application" in low or "processname" in low):
                    header = next(csv.reader([line]))
                    # Spaltennamen ohne Rücksicht auf Groß-/Kleinschreibung
                    # (PresentMon 2.x schreibt z.B. "msBetweenPresents")
                    cols = {name.strip().lower(): i for i, name in enumerate(header)}
                    i_app = cols.get("application", cols.get("processname"))
                    i_pid = cols.get("processid")
                    i_ms = next((cols[c] for c in ("msbetweenpresents", "frametime", "msbetweenappstart",
                                                    "msbetweendisplaychange") if c in cols), None)
                    self.error = None if i_ms is not None else "unbekanntes Format: " + line.strip()[:120]
                else:
                    self.last_message = line.strip()[:200]
                continue
            if i_ms is None:
                continue
            row = line.rstrip("\r\n").split(",")
            try:
                pid = int(row[i_pid])
                ms = float(row[i_ms])
            except (ValueError, IndexError):
                continue
            if not 0 < ms < 1000:
                continue
            now = time.monotonic()
            with self._lock:
                q = self._frames.get(pid)
                if q is None:
                    q = self._frames[pid] = deque()
                    if i_app is not None and i_app < len(row):
                        self._names[pid] = row[i_app]
                q.append((now, ms))
                while q and now - q[0][0] > 3.0:
                    q.popleft()
        code = proc.poll()
        if self._proc is proc:
            self.error = f"PresentMon beendet (Code {code})" + (f": {self.last_message}" if self.last_message else "")

    def status(self):
        """Kurzer Grund, warum keine FPS kommen (fürs Server-Fenster)."""
        if not IS_WINDOWS:
            return "nur unter Windows"
        if not self.path:
            return "PresentMon fehlt in diesem Build"
        if self._proc is None:
            return "Messung pausiert"
        return self.error or "kein Programm zeichnet gerade Bilder"

    # ------------------------------------------------------------ Auswertung
    def read(self):
        """-> {"fps": Zahl, "app": Name} für das Vordergrund-Programm (oder das
        Programm mit den meisten Bildern), sonst None."""
        if not self._proc:
            return None
        now = time.monotonic()
        fg = _foreground_pid()
        best = None
        results = {}
        with self._lock:
            for pid, q in list(self._frames.items()):
                while q and now - q[0][0] > 3.0:
                    q.popleft()
                if not q:
                    del self._frames[pid]
                    continue
                name = self._names.get(pid, "")
                if name.lower() in _IGNORE:
                    continue
                # FPS aus den Bildzeiten des letzten ~1 s Datenstroms
                total, n = 0.0, 0
                for _, ms in reversed(q):
                    total += ms
                    n += 1
                    if total >= WINDOW * 1000:
                        break
                if not total:
                    continue
                results[pid] = (n * 1000.0 / total, name, len(q))
        if not results:
            return None
        # 1. Programm im Vordergrund (Vollbild oder Fenster), 2. zuletzt gespieltes
        #    Programm im Vordergrund, solange es noch zeichnet (Fenstermodus: kurz in
        #    Browser/Discord gewechselt), 3. Programm mit den meisten Bildern
        games = {p for p, r in results.items() if _app_key(r[1]) not in _DESKTOP_APPS}
        if fg in games:
            self._last_fg = fg
            pid = fg
        elif self._last_fg in games:
            pid = self._last_fg
        elif games:
            pid = max(games, key=lambda p: results[p][2])
        elif fg in results:
            pid = fg  # kein Spiel: dann eben das Vordergrund-Programm (z.B. Video im Browser)
        else:
            pid = max(results, key=lambda p: results[p][2])
        best = (None, results[pid][0], results[pid][1])
        app = best[2][:-4] if best[2].lower().endswith(".exe") else best[2]
        return {"fps": round(best[1]), "app": app}
