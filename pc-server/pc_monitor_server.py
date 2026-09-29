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
from net_sensors import NetSensors
from mem_bandwidth import RamBandwidth

HTTP_PORT = int(os.environ.get("PCMON_PORT", "47811"))
DISCOVERY_PORT = 47810
DISCOVERY_REQUEST = b"PCMON_DISCOVER"
SAMPLE_INTERVAL = 1.0
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

class GpuReader:
    """Liest die GPU-Auslastung. Probiert der Reihe nach:
    NVML (NVIDIA), nvidia-smi, Windows-Leistungsindikatoren (AMD/Intel/alle),
    Linux sysfs (AMD)."""

    def __init__(self):
        self.source = None
        self._nvml = None
        self._nvml_handles = []
        self._nvml_energy = {}
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

    def _nvml_power(self, h):
        """Alle Leistungswerte, die NVML hergibt (Watt). Je nach Karte/Treiber meldet
        die einfache Abfrage nur einen geglätteten oder Teilwert, daher mehrere Wege."""
        n = self._nvml
        out = {}
        try:
            out["NVML"] = round(n.nvmlDeviceGetPowerUsage(h) / 1000.0, 1)
        except Exception:
            pass
        try:  # Momentanwert (neuere Treiber)
            fv = n.nvmlDeviceGetFieldValues(h, [186])[0]  # NVML_FI_DEV_POWER_INSTANT
            if fv.nvmlReturn == 0 and fv.value.uiVal:
                out["NVML momentan"] = round(fv.value.uiVal / 1000.0, 1)
        except Exception:
            pass
        try:  # Energiezähler der ganzen Karte (mJ) -> mittlere Leistung seit letzter Messung
            energy = n.nvmlDeviceGetTotalEnergyConsumption(h)
            now = time.monotonic()
            key = id(h)  # Handles bleiben für die Laufzeit dieselben Objekte
            last = self._nvml_energy.get(key)
            self._nvml_energy[key] = (now, energy)
            if last and energy >= last[1] and now - last[0] > 0.2:
                out["NVML Energie"] = round((energy - last[1]) / 1000.0 / (now - last[0]), 1)
        except Exception:
            pass
        return {k: v for k, v in out.items() if 0 < v < 2000}

    def _nvml_bandwidth(self, h, gpu):
        """VRAM-Bandbreite: Maximum aus Speichertakt x Busbreite, aktuell ~ Auslastung des
        Speicher-Controllers x Maximum beim aktuellen Takt (GDDR überträgt 2x pro NVML-Takt)."""
        n = self._nvml
        try:
            bus = n.nvmlDeviceGetMemoryBusWidth(h)  # Bit
        except Exception:
            return
        try:
            clk_max = n.nvmlDeviceGetMaxClockInfo(h, n.NVML_CLOCK_MEM)
            gpu["vram_bw_max_gbs"] = round(clk_max * 2 * bus / 8 / 1000, 1)
        except Exception:
            pass
        try:
            clk = n.nvmlDeviceGetClockInfo(h, n.NVML_CLOCK_MEM)
            if gpu.get("vram_ctrl_pct") is not None:
                gpu["vram_bw_gbs"] = round(gpu["vram_ctrl_pct"] / 100 * clk * 2 * bus / 8 / 1000, 1)
        except Exception:
            pass

    def _read_nvml(self):
        n = self._nvml
        result = []
        for h in self._nvml_handles:
            gpu = {"name": None, "usage": None, "mem_used_mb": None,
                   "mem_total_mb": None, "temp_c": None, "power_w": None,
                   "clock_mhz": None}
            try:
                name = n.nvmlDeviceGetName(h)
                gpu["name"] = name.decode() if isinstance(name, bytes) else name
            except Exception:
                pass
            try:
                util = n.nvmlDeviceGetUtilizationRates(h)
                gpu["usage"] = float(util.gpu)
                gpu["vram_ctrl_pct"] = float(util.memory)  # Anteil der Zeit, in der VRAM gelesen/geschrieben wurde
            except Exception:
                pass
            self._nvml_bandwidth(h, gpu)
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
            sources = self._nvml_power(h)
            if sources:
                gpu["power_w"] = max(sources.values())
                gpu["power_sources"] = sources
            try:
                gpu["clock_mhz"] = float(n.nvmlDeviceGetClockInfo(h, n.NVML_CLOCK_GRAPHICS))
            except Exception:
                pass
            result.append(gpu)
        return result

    # NVIDIA über nvidia-smi --------------------------------------------------
    def _read_nvidia_smi(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr",
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
                           "mem_total_mb": num(p[3]), "temp_c": num(p[4]), "power_w": num(p[5]),
                           "clock_mhz": num(p[6]) if len(p) > 6 else None})
        return result or None

    # Windows: Leistungsindikatoren (funktioniert für jede GPU) ---------------
    # Die WMI-Klasse hat sprachunabhängige Namen (anders als Get-Counter).
    _PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$vc = Get-CimInstance Win32_VideoController
$name = ($vc | Where-Object { $_.Name -match 'NVIDIA|Radeon|AMD|Arc' } | Select-Object -First 1).Name
if (-not $name) { $name = ($vc | Select-Object -First 1).Name }
[Console]::Out.WriteLine("NAME:" + $name)
[Console]::Out.Flush()
while ($true) {
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
  # Takt gibt Windows selbst nicht her - nur über LibreHardwareMonitor/OpenHardwareMonitor
  $clock = ""
  foreach ($ns in 'root/LibreHardwareMonitor', 'root/OpenHardwareMonitor') {
    $c = Get-CimInstance -Namespace $ns -ClassName Sensor -Filter "SensorType='Clock'" |
         Where-Object { $_.Identifier -match 'gpu' -and $_.Name -match 'Core' } | Select-Object -First 1
    if ($c) { $clock = [math]::Round([double]$c.Value); break }
  }
  [Console]::Out.WriteLine("VAL:" + [math]::Min(100, $max) + ";" + $used + ";" + $clock)
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds 800
}
"""

    def _init_windows_counters(self):
        try:
            self._win_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", self._PS_SCRIPT],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                creationflags=NO_WINDOW)
        except Exception:
            return False
        self._win_value = {"name": "GPU", "usage": None, "mem_used_mb": None,
                           "mem_total_mb": None, "temp_c": None, "power_w": None,
                   "clock_mhz": None}
        threading.Thread(target=self._pump_windows, daemon=True).start()
        return True

    def _pump_windows(self):
        for line in self._win_proc.stdout:
            line = line.strip()
            if line.startswith("NAME:"):
                self._win_value["name"] = line[5:] or "GPU"
            elif line.startswith("VAL:"):
                try:
                    usage, used, clock = (line[4:].split(";") + [""])[:3]
                    self._win_value["usage"] = round(float(usage.replace(",", ".")), 1)
                    self._win_value["mem_used_mb"] = round(float(used.replace(",", ".")) / 1048576)
                    self._win_value["clock_mhz"] = float(clock) if clock.strip() else None
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
                   "mem_total_mb": None, "temp_c": None, "power_w": None,
                   "clock_mhz": None}
            v = rd(busy)
            gpu["usage"] = float(v) if v else None
            v = rd(os.path.join(dev, "mem_info_vram_used"))
            gpu["mem_used_mb"] = round(int(v) / 1048576) if v else None
            v = rd(os.path.join(dev, "mem_info_vram_total"))
            gpu["mem_total_mb"] = round(int(v) / 1048576) if v else None
            sclk = rd(os.path.join(dev, "pp_dpm_sclk"))  # aktive Stufe ist mit * markiert
            for sl in (sclk or "").splitlines():
                if sl.strip().endswith("*"):
                    try:
                        gpu["clock_mhz"] = float(sl.split(":")[1].strip().split("Mhz")[0].split("MHz")[0])
                    except (IndexError, ValueError):
                        pass
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
        self.cpu_name = cpu_name()
        self.lhm = LhmHelper()
        self.cpu_sensors = CpuSensors(self.lhm)
        self.net = NetSensors()
        self.ram_bw = RamBandwidth()
        self.hostname = socket.gethostname()
        self.data = {}
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
        core_freqs, freq_mhz, temp_c = self.cpu_sensors.read()
        vm = psutil.virtual_memory()
        data = {
            "host": self.hostname,
            "time": time.time(),
            "cpu": {
                "name": self.cpu_name,
                "usage": total,
                "cores": [round(c, 1) for c in cores],
                "core_freq_mhz": core_freqs,
                "freq_mhz": freq_mhz,
                "temp_c": temp_c,
                "power_w": self.cpu_sensors.power(),
                "temp_note": self.lhm.cpu_temp_note() if temp_c is None else None,
            },
            "ram": {
                "used_mb": round(vm.used / 1048576),
                "total_mb": round(vm.total / 1048576),
                "usage": round(vm.percent, 1),
                **self.ram_bw.read(),
            },
            "gpus": merge_gpus(self.gpu.read(), self.lhm.gpus()),
            "net": self.net.read(),
            "gpu_source": self.gpu.source,
        }
        with self.lock:
            self.data = data

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


def start_backend():
    """Startet Messung, UDP-Suche und HTTP-Server im Hintergrund."""
    global sampler, caster
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

    if gui:
        gui.run(sampler, caster, local_ips(), HTTP_PORT, start_hidden="--tray" in args)
    else:
        run_console()


if __name__ == "__main__":
    main()
