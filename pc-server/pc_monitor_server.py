#!/usr/bin/env python3
"""PC Monitor Server

Liefert CPU-, RAM- und GPU-Auslastung dieses PCs als JSON im lokalen Netzwerk,
damit die Android-App "PC Monitor" sie anzeigen kann.

  HTTP  GET http://<pc-ip>:47811/stats   -> aktuelle Werte als JSON
  HTTP  GET http://<pc-ip>:47811/        -> Dashboard (Browser / Nest Hub)
  HTTP  GET/POST /cast…                  -> Anzeige auf Nest Hub/Chromecast steuern
  UDP   Port 47810                       -> automatische Suche der App

Start:  python pc_monitor_server.py   (oder die fertige PCMonitorServer.exe)
        Optionen: --tray (versteckt im Systemtray starten), --console (ohne Fenster)
"""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from caster import Caster
from dashboard import DASHBOARD_HTML

try:
    import psutil
except ImportError:
    print("Das Paket 'psutil' fehlt. Installieren mit:  pip install psutil")
    sys.exit(1)

from cpu_sensors import CpuSensors
from lhm_helper import LhmHelper, merge_gpus
from fps import FpsMonitor

HTTP_PORT = int(os.environ.get("PCMON_PORT", "47811"))
DISCOVERY_PORT = 47810
DISCOVERY_REQUEST = b"PCMON_DISCOVER"
SAMPLE_INTERVAL = 1.0  # App und Nest Hub fragen ebenfalls jede Sekunde ab
IDLE_INTERVAL = 10.0   # niemand schaut zu: nur alle 10 s messen (Tray-Tooltip)
ACTIVE_TIMEOUT = 15.0  # so lange nach der letzten Abfrage wird im Sekundentakt gemessen
IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


# --------------------------------------------------------------------------- CPU

def cpu_name():
    try:
        if IS_WINDOWS:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        if sys.platform == "darwin":
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True, stdin=subprocess.DEVNULL).strip()
    except Exception:
        pass
    return platform.processor() or "CPU"


# --------------------------------------------------------------------------- GPU

def _gpu_name_from_registry():
    """Name der Grafikkarte aus der Registry (Grafikkarte vor integrierter Grafik)."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        names = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                sub = winreg.EnumKey(k, i)
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(k, sub) as sk:
                        names.append(winreg.QueryValueEx(sk, "DriverDesc")[0])
                except OSError:
                    pass
    except OSError:
        return None
    names = [n for n in names if n and "basic" not in n.lower() and "virtual" not in n.lower()]
    for n in names:
        if re.search(r"NVIDIA|Radeon|AMD|Arc", n, re.I):
            return n
    return names[0] if names else None


class GpuReader:
    """Liest die GPU-Auslastung. Probiert der Reihe nach:
    NVML (NVIDIA), nvidia-smi, Windows-Leistungsindikatoren (AMD/Intel/alle),
    Linux sysfs (AMD)."""

    def __init__(self):
        self.source = None
        self._nvml = None
        self._nvml_handles = []
        self._nvml_static = {}
        self._win_value = None
        self._win_pdh = None

        if self._init_nvml():
            self.source = "nvml"
        elif shutil.which("nvidia-smi") and self._read_nvidia_smi():
            self.source = "nvidia-smi"
        elif IS_WINDOWS and self._init_windows_counters():
            self.source = "windows-counters"
        elif self._read_sysfs():
            self.source = "sysfs"

    # NVIDIA über NVML --------------------------------------------------------
    def _init_nvml(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            if count == 0:
                return False
            self._nvml = pynvml
            self._nvml_handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
            return True
        except Exception:
            return False

    def _read_nvml(self):
        # Pro Messung nur 4 Treiberaufrufe je GPU; Name und VRAM-Größe ändern sich nicht
        n = self._nvml
        result = []
        for i, h in enumerate(self._nvml_handles):
            static = self._nvml_static.get(i)
            if static is None:
                static = {"name": "GPU", "mem_total_mb": None}
                try:
                    name = n.nvmlDeviceGetName(h)
                    static["name"] = name.decode() if isinstance(name, bytes) else name
                except Exception:
                    pass
                self._nvml_static[i] = static
            gpu = {"name": static["name"], "usage": None, "mem_used_mb": None,
                   "mem_total_mb": static["mem_total_mb"], "temp_c": None, "power_w": None}
            try:
                gpu["usage"] = float(n.nvmlDeviceGetUtilizationRates(h).gpu)
            except Exception:
                pass
            try:
                mem = n.nvmlDeviceGetMemoryInfo(h)
                gpu["mem_used_mb"] = round(mem.used / 1048576)
                gpu["mem_total_mb"] = static["mem_total_mb"] = round(mem.total / 1048576)
            except Exception:
                pass
            try:
                gpu["temp_c"] = float(n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU))
            except Exception:
                pass
            try:  # Leistungsaufnahme der ganzen Karte
                w = n.nvmlDeviceGetPowerUsage(h) / 1000.0
                gpu["power_w"] = round(w, 1) if 0 < w < 2000 else None
            except Exception:
                pass
            result.append(gpu)
        return result

    # NVIDIA über nvidia-smi --------------------------------------------------
    def _read_nvidia_smi(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=5, creationflags=NO_WINDOW,
                stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None

        def num(v):
            try:
                return float(v)
            except ValueError:
                return None

        result = []
        for line in out.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) < 6:
                continue
            result.append({"name": p[0], "usage": num(p[1]), "mem_used_mb": num(p[2]),
                           "mem_total_mb": num(p[3]), "temp_c": num(p[4]), "power_w": num(p[5])})
        return result or None

    # Windows: Leistungsindikatoren (funktioniert für jede GPU) ---------------
    # Direkt über pdh.dll statt WMI/PowerShell – spart viel CPU im WMI-Anbieterhost.
    def _init_windows_counters(self):
        try:
            from cpu_sensors import _Pdh
            self._win_pdh = _Pdh({
                "engine": r"\GPU Engine(*)\Utilization Percentage",
                "memory": r"\GPU Adapter Memory(*)\Dedicated Usage",
            })
        except Exception:
            return False
        self._win_value = {"name": _gpu_name_from_registry() or "GPU", "usage": None,
                           "mem_used_mb": None, "mem_total_mb": None, "temp_c": None,
                           "power_w": None}
        return True

    def _read_windows_counters(self):
        v = dict(self._win_value)
        try:
            self._win_pdh.collect()
            per_type = {}
            for name, val in self._win_pdh.values("engine").items():
                t = name.rsplit("engtype_", 1)[-1]
                per_type[t] = per_type.get(t, 0.0) + val
            if per_type:
                v["usage"] = round(min(100.0, max(per_type.values())), 1)
            mem = self._win_pdh.values("memory")
            if mem:
                v["mem_used_mb"] = round(sum(mem.values()) / 1048576)
        except Exception:
            pass
        return v

    # Linux: AMD über sysfs ---------------------------------------------------
    def _read_sysfs(self):
        base = "/sys/class/drm"
        if not os.path.isdir(base):
            return None
        result = []
        for card in sorted(os.listdir(base)):
            dev = os.path.join(base, card, "device")
            busy = os.path.join(dev, "gpu_busy_percent")
            if "-" in card or not os.path.exists(busy):
                continue

            def rd(path):
                try:
                    with open(path) as f:
                        return f.read().strip()
                except OSError:
                    return None

            gpu = {"name": card, "usage": None, "mem_used_mb": None,
                   "mem_total_mb": None, "temp_c": None, "power_w": None}
            v = rd(busy)
            gpu["usage"] = float(v) if v else None
            v = rd(os.path.join(dev, "mem_info_vram_used"))
            gpu["mem_used_mb"] = round(int(v) / 1048576) if v else None
            v = rd(os.path.join(dev, "mem_info_vram_total"))
            gpu["mem_total_mb"] = round(int(v) / 1048576) if v else None
            hwmon = os.path.join(dev, "hwmon")
            if os.path.isdir(hwmon):
                for h in os.listdir(hwmon):
                    t = rd(os.path.join(hwmon, h, "temp1_input"))
                    if t:
                        gpu["temp_c"] = int(t) / 1000.0
                        break
            result.append(gpu)
        return result or None

    def read(self):
        if self.source == "nvml":
            return self._read_nvml()
        if self.source == "nvidia-smi":
            return self._read_nvidia_smi() or []
        if self.source == "windows-counters":
            return [self._read_windows_counters()]
        if self.source == "sysfs":
            return self._read_sysfs() or []
        return []


# --------------------------------------------------------------------------- Sampler

class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.gpu = GpuReader()
        self.cpu_name = cpu_name()
        self.lhm = LhmHelper(with_gpu=self.gpu.source != "nvml")
        self.cpu_sensors = CpuSensors(self.lhm)
        self.fps = FpsMonitor()
        self.hostname = socket.gethostname()
        self.data = {}
        self.last_request = time.monotonic()
        self.gui_visible = False
        self._wake = threading.Event()
        psutil.cpu_percent(percpu=True)  # erste Messung initialisieren

    def touch(self):
        """Ein Client (App, Nest Hub, Browser) hat Werte abgerufen."""
        idle = not self.active
        self.last_request = time.monotonic()
        if idle:
            self._wake.set()  # sofort wieder im Sekundentakt messen

    @property
    def active(self):
        """Messen im Sekundentakt nur, solange jemand zuschaut."""
        return self.gui_visible or time.monotonic() - self.last_request < ACTIVE_TIMEOUT

    def run(self):
        while True:
            self._wake.wait(SAMPLE_INTERVAL if self.active else IDLE_INTERVAL)
            self._wake.clear()
            try:
                self.sample()
            except Exception as e:
                print("Messfehler:", e)

    def sample(self):
        # Sensor-Modul (CPU-Temperatur/-Leistung) nur jede 3. Messung (alle 3 s) – das ist der
        # aufwendigste Teil, die Werte ändern sich langsam; es misst parallel, das Ergebnis gilt
        # ab dem nächsten Durchlauf
        self._n = getattr(self, "_n", 0) + 1
        if self._n % 3 == 1 or not self.active:
            self.lhm.poke()
        # FPS-Messung (PresentMon) nur, solange jemand zuschaut
        if self.active:
            self.fps.start()
        else:
            self.fps.stop()
        cores = psutil.cpu_percent(percpu=True)
        total = round(sum(cores) / len(cores), 1) if cores else 0.0
        temp_c = self.cpu_sensors.temperature()
        vm = psutil.virtual_memory()
        data = {
            "host": self.hostname,
            "time": time.time(),
            "cpu": {
                "name": self.cpu_name,
                "usage": total,
                "cores": [round(c, 1) for c in cores],
                "temp_c": temp_c,
                "power_w": self.cpu_sensors.power(),
                "temp_note": self.lhm.cpu_temp_note() if temp_c is None else None,
            },
            "ram": {
                "used_mb": round(vm.used / 1048576),
                "total_mb": round(vm.total / 1048576),
                "usage": round(vm.percent, 1),
            },
            "gpus": merge_gpus(self.gpu.read(), self.lhm.gpus()),
            "fps": self.fps.read(),
            "gpu_source": self.gpu.source,
        }
        with self.lock:
            self.data = data

    def own_cpu(self):
        """Eigene CPU-Last in % der Gesamt-CPU (wie im Task-Manager): (Server, Sensor-Modul)."""
        n = psutil.cpu_count() or 1
        procs = getattr(self, "_own_procs", None)
        if procs is None:
            procs = self._own_procs = {"server": psutil.Process()}
        pid = self.lhm.pid()
        if pid and (procs.get("helper") is None or procs["helper"].pid != pid):
            try:
                procs["helper"] = psutil.Process(pid)
                procs["helper"].cpu_percent(None)
            except Exception:
                procs["helper"] = None
        out = []
        for key in ("server", "helper"):
            p = procs.get(key)
            try:
                out.append(p.cpu_percent(None) / n if p else None)
            except Exception:
                out.append(None)
        return tuple(out)

    def snapshot(self):
        with self.lock:
            return self.data


# --------------------------------------------------------------------------- Netzwerk

sampler = None
caster = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/stats":
            sampler.touch()
            self._json(sampler.snapshot())
        elif url.path in ("/", "/index.html"):
            self._send(200, DASHBOARD_HTML, "text/html; charset=utf-8")
        elif url.path == "/cast":
            # Status; mit ?scan=1 zusätzlich Geräte im Netzwerk suchen
            state = caster.state()
            if parse_qs(url.query).get("scan") == ["1"]:
                try:
                    state["devices"] = caster.discover()
                except Exception as e:
                    state["devices"] = []
                    state["error"] = str(e)
            self._json(state)
        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/cast/start":
            name = (parse_qs(url.query).get("device") or [""])[0].strip()
            if not name:
                self._json({"error": "device fehlt"}, 400)
                return
            caster.start(name)
            self._json(caster.state())
        elif url.path == "/cast/stop":
            caster.stop()
            self._json(caster.state())
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # keine Ausgabe pro Anfrage


def discovery_responder():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    reply = json.dumps({"app": "pcmon", "name": socket.gethostname(), "port": HTTP_PORT}).encode()
    while True:
        try:
            msg, addr = sock.recvfrom(1024)
            if msg.strip() == DISCOVERY_REQUEST:
                sock.sendto(reply, addr)
        except Exception:
            time.sleep(0.5)


def local_ips():
    ips = set()
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    ips.add(a.address)
    except Exception:
        pass
    return sorted(ips)


def save_resources():
    """Niedrige Priorität und Effizienzmodus (EcoQoS, Windows 11) für diesen Prozess;
    Kindprozesse (Sensor-Modul) erben die niedrige Priorität."""
    try:
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if IS_WINDOWS else 10)
    except Exception:
        pass
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        class PowerThrottlingState(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                        ("StateMask", ctypes.c_ulong)]

        st = PowerThrottlingState(1, 1, 1)  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


# Eindeutige Stellen aus den PowerShell-Hintergrundskripten früherer Versionen.
# Diese liefen nach dem Beenden des alten Servers weiter und belasteten den
# WMI-Anbieterhost dauerhaft; die aktuelle Version startet kein PowerShell mehr.
_OLD_SCRIPT_MARKERS = (
    "GPUPerformanceCounters_GPUEngine",            # GPU-Auslastung (AMD/Intel)
    "{83DA6326-97A6-4088-9453-A1923F573B29} 15",   # Bluetooth-Geräte
    "SensorType='Temperature'",                    # CPU-Temperatur über LibreHardwareMonitor
    "Win32_PhysicalMemory",                        # RAM-Bandbreite
)


def kill_old_helpers():
    """Beendet verwaiste PowerShell-Skripte älterer PC-Monitor-Versionen (nur diese)."""
    if not IS_WINDOWS:
        return 0
    killed = 0
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if (proc.info["name"] or "").lower() not in ("powershell.exe", "pwsh.exe"):
                continue
            cmd = " ".join(proc.info["cmdline"] or [])
            if any(m in cmd for m in _OLD_SCRIPT_MARKERS):
                proc.kill()
                killed += 1
        except (psutil.Error, OSError):
            pass
    if killed:
        print(f"{killed} alte Hintergrundskripte beendet")
    return killed


_job_handle = None


def bind_children_to_process():
    """Hängt diesen Prozess (und damit alle später gestarteten Hilfsprozesse) an ein
    Windows-Job-Objekt mit KILL_ON_JOB_CLOSE: Endet der Server – beendet, abgestürzt
    oder per Task-Manager –, beendet Windows alle Hilfsprozesse automatisch mit."""
    global _job_handle
    if not IS_WINDOWS or _job_handle:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.GetCurrentProcess.restype = ctypes.c_void_p

        job = k.CreateJobObjectW(None, None)
        if not job:
            return
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return
        if k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            _job_handle = job  # Handle offen halten; schließt Windows es beim Prozessende -> Kinder enden
    except Exception as e:
        print("Job-Objekt nicht verfügbar:", e)


def start_backend():
    """Startet Messung, UDP-Suche und HTTP-Server im Hintergrund."""
    global sampler, caster
    save_resources()
    bind_children_to_process()
    kill_old_helpers()
    sampler = Sampler()
    sampler.sample()
    sampler.start()
    threading.Thread(target=discovery_responder, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    caster = Caster(HTTP_PORT)  # erst nach dem Port-Check, sonst castet ein zweiter Start mit
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return sampler


def run_console():
    print("=" * 56)
    print(" PC Monitor Server läuft")
    print(" CPU:", sampler.cpu_name)
    print(" GPU-Quelle:", sampler.gpu.source or "keine GPU-Daten gefunden")
    print(" In der App diese Adresse eintragen (oder 'Suchen' tippen):")
    for ip in local_ips():
        print(f"   {ip}:{HTTP_PORT}")
    print(" Dashboard im Browser: http://<diese-ip>:%d/" % HTTP_PORT)
    if caster.target:
        print(" Nest Hub / Chromecast:", caster.target)
    time.sleep(3)
    note = sampler.lhm.cpu_temp_note()
    if note:
        print(" CPU-Temperatur:", note)
    print(" Beenden mit Strg+C")
    print("=" * 56)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main():
    args = sys.argv[1:]
    # Fenster mit Tray-Icon unter Windows (oder mit --gui), sonst Konsole.
    want_gui = "--console" not in args and (IS_WINDOWS or "--gui" in args)
    gui = None
    if want_gui:
        try:
            import tray_gui as gui
        except Exception as e:
            print("Fenster/Tray nicht verfügbar, nutze Konsole:", e)
            gui = None

    try:
        start_backend()
    except OSError as e:
        msg = (f"Port {HTTP_PORT} ist belegt - läuft der Server schon?\n\n{e}")
        if gui:
            gui.show_error(msg)
        else:
            print(msg)
        sys.exit(1)

    try:
        if gui:
            gui.run(sampler, caster, local_ips(), HTTP_PORT, start_hidden="--tray" in args)
        else:
            run_console()
    finally:
        shutdown()


def shutdown():
    """Beim Schließen alles beenden: Sensor-Modul stoppen und den Prozess hart beenden.
    Hintergrund-Threads (z.B. Chromecast/zeroconf) hielten ihn sonst unsichtbar am Leben."""
    try:
        if sampler is not None:
            sampler.lhm.stop()
    except Exception:
        pass
    sys.stdout.flush() if sys.stdout else None
    os._exit(0)


if __name__ == "__main__":
    main()
