#!/usr/bin/env python3
"""PC Monitor Server

Liefert CPU-, RAM- und GPU-Auslastung dieses PCs als JSON im lokalen Netzwerk,
damit die Android-App "PC Monitor" sie anzeigen kann.

  HTTP  GET http://<pc-ip>:47811/stats   -> aktuelle Werte als JSON
  HTTP  GET http://<pc-ip>:47811/history -> CPU/GPU-Verlauf der letzten 60 Minuten
  HTTP  GET http://<pc-ip>:47811/        -> Dashboard (Browser / Nest Hub)
  HTTP  GET/POST /cast…                  -> Anzeige auf Nest Hub/Chromecast steuern
  UDP   Port 47810                       -> automatische Suche der App

Start:  python pc_monitor_server.py   (oder die fertige PCMonitorServer.exe)
        Optionen: --tray (versteckt im Systemtray starten), --console (ohne Fenster)
"""

import base64
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from caster import Caster
from dashboard import DASHBOARD_HTML

try:
    import psutil
except ImportError:
    print("Das Paket 'psutil' fehlt. Installieren mit:  pip install psutil")
    sys.exit(1)

HTTP_PORT = int(os.environ.get("PCMON_PORT", "47811"))
DISCOVERY_PORT = 47810
DISCOVERY_REQUEST = b"PCMON_DISCOVER"
SAMPLE_INTERVAL = 1.0
HISTORY_SECONDS = 900  # Verlauf für die Diagramme (15 Minuten)
IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW

# Alle Hintergrundprozesse (PowerShell), die beim Beenden mit weg müssen
_children = []

# Beendet die PowerShell-Schleifen von selbst, sobald der Server nicht mehr läuft
# (z.B. wenn er im Task-Manager hart beendet wurde).
_PS_PARENT_CHECK = r"""
$parentPid = %d
function Test-Parent { return [bool](Get-Process -Id $parentPid -ErrorAction SilentlyContinue) }
"""


def start_powershell(script):
    """Startet ein PowerShell-Skript unsichtbar im Hintergrund und merkt es sich."""
    # -EncodedCommand statt -Command: keine Probleme mit Anführungszeichen beim Übergeben
    code = base64.b64encode(((_PS_PARENT_CHECK % os.getpid()) + script).encode("utf-16-le")).decode()
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        creationflags=NO_WINDOW)
    _children.append(proc)
    return proc


def shutdown(exit_code=0):
    """Beendet den Server vollständig – inklusive aller Hintergrundprozesse."""
    try:
        if caster is not None:
            caster.shutdown()
    except Exception:
        pass
    for proc in _children:
        try:
            proc.kill()
        except Exception:
            pass
    try:  # sicherheitshalber alles, was sonst noch von uns gestartet wurde
        for child in psutil.Process().children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
    except Exception:
        pass
    # Hart beenden: offene Netzwerk-/Cast-Threads dürfen den Prozess nicht am Leben halten
    os._exit(exit_code)


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


def cpu_temperature():
    try:
        temps = psutil.sensors_temperatures()  # nur Linux/BSD
    except Exception:
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        entries = temps.get(key)
        if entries:
            return round(max(e.current for e in entries), 1)
    return None


class CpuSensors:
    """Leistungsaufnahme (W) und Temperatur (°C) der CPU.

    Windows: LibreHardwareMonitor / OpenHardwareMonitor (falls gestartet, liefert
    Leistung + Temperatur; per WMI oder über den LHM-Webserver auf Port 8085),
    sonst Windows-Leistungsindikator "Energy Meter" (RAPL, nur Leistung).
    Windows selbst stellt die CPU-Temperatur ohne solchen Treiber nicht bereit.
    Linux: RAPL über /sys/class/powercap und psutil für die Temperatur.
    """

    _PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$inv = [Globalization.CultureInfo]::InvariantCulture
function Walk($n) { $n; foreach ($c in $n.Children) { Walk $c } }
function Num($v) {
  if ($v -eq $null) { return $null }
  $m = [regex]::Match([string]$v, '-?[0-9]+([.,][0-9]+)?')
  if (-not $m.Success) { return $null }
  return [double]::Parse($m.Value.Replace(',', '.'), $inv)
}
function Pick($list, $type, $namePattern) {
  $x = $list | Where-Object { $_.T -eq $type -and $_.N -match $namePattern } | Select-Object -First 1
  if (-not $x) { $x = $list | Where-Object { $_.T -eq $type } | Sort-Object V -Descending | Select-Object -First 1 }
  return $x
}
while (Test-Parent) {
  $p = $null; $t = $null; $src = ''
  $list = $null
  # 1) LibreHardwareMonitor / OpenHardwareMonitor über WMI
  foreach ($ns in 'root/LibreHardwareMonitor', 'root/OpenHardwareMonitor') {
    $s = Get-CimInstance -Namespace $ns -ClassName Sensor
    if ($s) {
      $list = $s | Where-Object { $_.Identifier -match 'cpu' } |
        ForEach-Object { [pscustomobject]@{ T = [string]$_.SensorType; N = [string]$_.Name; V = [double]$_.Value } }
      $src = if ($ns -match 'Libre') { 'LibreHardwareMonitor' } else { 'OpenHardwareMonitor' }
      break
    }
  }
  # 2) LibreHardwareMonitor-Webserver (Options -> Remote Web Server)
  if (-not $list) {
    $j = Invoke-RestMethod -Uri 'http://127.0.0.1:8085/data.json' -TimeoutSec 1
    if ($j) {
      $list = Walk $j | Where-Object { $_.SensorId -match 'cpu' -and $_.Type } |
        ForEach-Object { [pscustomobject]@{ T = [string]$_.Type; N = [string]$_.Text; V = (Num $_.Value) } }
      $src = 'LibreHardwareMonitor (Webserver)'
    }
  }
  if ($list) {
    $pw = Pick $list 'Power' 'Package'
    if ($pw) { $p = $pw.V }
    $tp = Pick $list 'Temperature' 'Package|Tctl|Tdie'
    if ($tp) { $t = $tp.V }
  }
  # 3) Windows-Leistungsindikator "Energy Meter" (RAPL), nur Leistung
  if ($p -eq $null) {
    $e = Get-CimInstance Win32_PerfFormattedData_Counters_EnergyMeter | Where-Object { $_.Name -match 'PKG' } | Select-Object -First 1
    if ($e -and $e.Power -gt 0) {
      $p = $e.Power / 1000
      if (-not $src) { $src = 'Windows Energy Meter' }
    }
  }
  $ps = if ($p -ne $null) { ([double]$p).ToString($inv) } else { '' }
  $ts = if ($t -ne $null) { ([double]$t).ToString($inv) } else { '' }
  [Console]::Out.WriteLine('VAL:' + $ps + ';' + $ts + ';' + $src)
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds 1000
}
"""

    def __init__(self):
        self.power_w = None
        self.temp_c = None
        self.source = None  # woher die Werte kommen (für die Anzeige im Server-Fenster)
        self.running = False  # Messskript meldet sich (Windows)
        self._rapl_path = None
        self._rapl_last = None
        if IS_WINDOWS:
            try:
                self._proc = start_powershell(self._PS_SCRIPT)
                threading.Thread(target=self._pump_windows, daemon=True).start()
            except Exception:
                pass
        else:
            self._init_rapl()

    def _pump_windows(self):
        for line in self._proc.stdout:
            line = line.strip()
            if not line.startswith("VAL:"):
                continue
            self.running = True
            parts = line[4:].split(";")
            parts += [""] * (3 - len(parts))
            self.power_w = self._num(parts[0])
            self.temp_c = self._num(parts[1])
            self.source = parts[2].strip() or None

    @staticmethod
    def _num(v):
        try:
            return round(float(v.replace(",", ".")), 1) if v.strip() else None
        except ValueError:
            return None

    def _init_rapl(self):
        base = "/sys/class/powercap"
        try:
            for name in sorted(os.listdir(base)):
                path = os.path.join(base, name)
                # Package-Zone: "intel-rapl:0" (auch bei AMD so benannt)
                if name.count(":") == 1 and os.path.exists(os.path.join(path, "energy_uj")):
                    with open(os.path.join(path, "energy_uj")) as f:
                        f.read()
                    self._rapl_path = path
                    self.source = "RAPL (powercap)"
                    return
        except OSError:
            pass  # nicht vorhanden oder nur mit root lesbar

    def _read_rapl(self):
        try:
            with open(os.path.join(self._rapl_path, "energy_uj")) as f:
                energy = int(f.read())
        except (OSError, ValueError):
            return None
        now = time.monotonic()
        last, self._rapl_last = self._rapl_last, (now, energy)
        if not last or energy < last[1] or now <= last[0]:
            return None  # erste Messung oder Zählerüberlauf
        return round((energy - last[1]) / 1e6 / (now - last[0]), 1)

    def read(self):
        """Liefert (power_w, temp_c)."""
        if IS_WINDOWS:
            return self.power_w, self.temp_c
        power = self._read_rapl() if self._rapl_path else None
        return power, cpu_temperature()


# --------------------------------------------------------------------------- GPU

class GpuReader:
    """Liest die GPU-Auslastung. Probiert der Reihe nach:
    NVML (NVIDIA), nvidia-smi, Windows-Leistungsindikatoren (AMD/Intel/alle),
    Linux sysfs (AMD)."""

    def __init__(self):
        self.source = None
        self._nvml = None
        self._nvml_handles = []
        self._win_value = None
        self._win_proc = None

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
        n = self._nvml
        result = []
        for h in self._nvml_handles:
            gpu = {"name": None, "usage": None, "mem_used_mb": None,
                   "mem_total_mb": None, "temp_c": None, "power_w": None}
            try:
                name = n.nvmlDeviceGetName(h)
                gpu["name"] = name.decode() if isinstance(name, bytes) else name
            except Exception:
                pass
            try:
                gpu["usage"] = float(n.nvmlDeviceGetUtilizationRates(h).gpu)
            except Exception:
                pass
            try:
                mem = n.nvmlDeviceGetMemoryInfo(h)
                gpu["mem_used_mb"] = round(mem.used / 1048576)
                gpu["mem_total_mb"] = round(mem.total / 1048576)
            except Exception:
                pass
            try:
                gpu["temp_c"] = float(n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU))
            except Exception:
                pass
            try:
                gpu["power_w"] = round(n.nvmlDeviceGetPowerUsage(h) / 1000.0, 1)
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
    # Die WMI-Klasse hat sprachunabhängige Namen (anders als Get-Counter).
    _PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$name = (Get-CimInstance Win32_VideoController | Select-Object -First 1).Name
[Console]::Out.WriteLine("NAME:" + $name)
[Console]::Out.Flush()
while (Test-Parent) {
  $eng = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine
  $max = 0
  if ($eng) {
    $groups = $eng | Group-Object { ($_.Name -split 'engtype_')[-1] }
    foreach ($g in $groups) {
      $s = ($g.Group | Measure-Object -Property UtilizationPercentage -Sum).Sum
      if ($s -gt $max) { $max = $s }
    }
  }
  $mem = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory
  $used = 0
  if ($mem) { $used = ($mem | Measure-Object -Property DedicatedUsage -Sum).Sum }
  [Console]::Out.WriteLine("VAL:" + [math]::Min(100, $max) + ";" + $used)
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds 800
}
"""

    def _init_windows_counters(self):
        try:
            self._win_proc = start_powershell(self._PS_SCRIPT)
        except Exception:
            return False
        self._win_value = {"name": "GPU", "usage": None, "mem_used_mb": None,
                           "mem_total_mb": None, "temp_c": None, "power_w": None}
        threading.Thread(target=self._pump_windows, daemon=True).start()
        return True

    def _pump_windows(self):
        for line in self._win_proc.stdout:
            line = line.strip()
            if line.startswith("NAME:"):
                self._win_value["name"] = line[5:] or "GPU"
            elif line.startswith("VAL:"):
                try:
                    usage, used = line[4:].split(";")
                    self._win_value["usage"] = round(float(usage.replace(",", ".")), 1)
                    self._win_value["mem_used_mb"] = round(float(used.replace(",", ".")) / 1048576)
                except ValueError:
                    pass

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
            return [dict(self._win_value)]
        if self.source == "sysfs":
            return self._read_sysfs() or []
        return []


# --------------------------------------------------------------------------- Sampler

class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.gpu = GpuReader()
        self.cpu_sensors = CpuSensors()
        self.cpu_name = cpu_name()
        self.hostname = socket.gethostname()
        self.data = {}
        self.history = deque(maxlen=int(HISTORY_SECONDS / SAMPLE_INTERVAL))  # (cpu, gpu)
        psutil.cpu_percent(percpu=True)  # erste Messung initialisieren

    def run(self):
        while True:
            time.sleep(SAMPLE_INTERVAL)
            try:
                self.sample()
            except Exception as e:
                print("Messfehler:", e)

    def sample(self):
        cores = psutil.cpu_percent(percpu=True)
        total = round(sum(cores) / len(cores), 1) if cores else 0.0
        try:
            freq = psutil.cpu_freq()
            freq_mhz = round(freq.current) if freq else None
        except Exception:
            freq_mhz = None
        vm = psutil.virtual_memory()
        cpu_power, cpu_temp = self.cpu_sensors.read()
        data = {
            "host": self.hostname,
            "time": time.time(),
            "cpu": {
                "name": self.cpu_name,
                "usage": total,
                "cores": [round(c, 1) for c in cores],
                "freq_mhz": freq_mhz,
                "temp_c": cpu_temp,
                "power_w": cpu_power,
            },
            "ram": {
                "used_mb": round(vm.used / 1048576),
                "total_mb": round(vm.total / 1048576),
                "usage": round(vm.percent, 1),
            },
            "gpus": self.gpu.read(),
            "gpu_source": self.gpu.source,
            "cpu_sensor_source": self.cpu_sensors.source,
            "cpu_sensor_running": self.cpu_sensors.running or not IS_WINDOWS,
        }
        gpus = data["gpus"]
        gpu_usage = gpus[0].get("usage") if gpus else None
        with self.lock:
            self.data = data
            self.history.append((total, gpu_usage))

    def snapshot(self):
        with self.lock:
            return self.data

    def history_snapshot(self):
        with self.lock:
            items = list(self.history)
        return {
            "interval": SAMPLE_INTERVAL,
            "cpu": [c for c, _ in items],
            "gpu": [g for _, g in items],
        }


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
            self._json(sampler.snapshot())
        elif url.path == "/history":
            self._json(sampler.history_snapshot())
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


def watch_launcher():
    """Die EXE (PyInstaller onefile) besteht aus zwei Prozessen: einem Startprozess und
    diesem Python-Prozess. Wird der Startprozess beendet (z.B. im Task-Manager),
    beendet sich auch dieser Prozess samt Hintergrundprozessen."""
    if not getattr(sys, "frozen", False):
        return
    try:
        parent = psutil.Process().parent()
        exe = os.path.basename(sys.executable).lower()
        if parent is None or parent.name().lower() != exe:
            return  # kein onefile-Startprozess
    except Exception:
        return

    def loop():
        while True:
            time.sleep(2)
            try:
                if not parent.is_running():
                    shutdown()
            except Exception:
                shutdown()
    threading.Thread(target=loop, daemon=True, name="launcher-watch").start()


def start_backend():
    """Startet Messung, UDP-Suche und HTTP-Server im Hintergrund."""
    global sampler, caster
    watch_launcher()
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
    print(" Beenden mit Strg+C")
    print("=" * 56)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    shutdown()


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
        shutdown(1)

    try:
        import autostart
        autostart.apply_from_config(caster.cfg)
    except Exception as e:
        print("Autostart konnte nicht gesetzt werden:", e)

    if gui:
        gui.run(sampler, caster, local_ips(), HTTP_PORT, start_hidden="--tray" in args)
        shutdown()  # Fenster geschlossen / "Beenden" im Tray -> alles beenden
    else:
        run_console()


if __name__ == "__main__":
    main()
