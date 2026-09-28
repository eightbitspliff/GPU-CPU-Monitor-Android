"""Taktfrequenz je CPU-Kern und CPU-Temperatur.

Windows: Leistungsindikatoren über pdh.dll (ctypes, ohne Zusatzpakete).
         Aktueller Takt = "Processor Frequency" (Nenntakt) * "% Processor Performance"
         – dieselbe Rechnung wie im Task-Manager.
         Temperatur: LibreHardwareMonitor/OpenHardwareMonitor (falls laufend, genau),
         sonst ACPI-Thermalzone (ohne Adminrechte, aber nur ungefähr).
Linux:   psutil.cpu_freq(percpu=True) und psutil.sensors_temperatures().
"""

import os
import subprocess
import threading

import psutil

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _valid_temp(t):
    return t is not None and 5.0 < t < 125.0


class _Pdh:
    """Minimaler Zugriff auf Windows-Leistungsindikatoren mit Platzhalter-Instanzen."""

    FMT_DOUBLE = 0x00000200
    FMT_NOCAP100 = 0x00008000
    MORE_DATA = 0x800007D2

    def __init__(self, paths):
        import ctypes
        from ctypes import wintypes

        class Value(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", Value)]

        self.ct = ctypes
        self.Item = Item
        dll = ctypes.WinDLL("pdh")
        dll.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.PdhOpenQueryW.restype = ctypes.c_long
        dll.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p)]
        dll.PdhAddEnglishCounterW.restype = ctypes.c_long
        dll.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        dll.PdhCollectQueryData.restype = ctypes.c_long
        dll.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                     ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        dll.PdhGetFormattedCounterArrayW.restype = ctypes.c_long
        self.dll = dll

        self.query = ctypes.c_void_p()
        if dll.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery fehlgeschlagen")
        self.counters = {}
        for key, path in paths.items():
            h = ctypes.c_void_p()
            if dll.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(h)) == 0:
                self.counters[key] = h
        if not self.counters:
            raise OSError("Keine Leistungsindikatoren verfügbar")
        self.collect()

    def collect(self):
        self.dll.PdhCollectQueryData(self.query)

    def values(self, key):
        """{Instanzname: Wert} des zuletzt gesammelten Messwerts."""
        h = self.counters.get(key)
        if h is None:
            return {}
        ct, wt = self.ct, self.ct.wintypes
        fmt = self.FMT_DOUBLE | self.FMT_NOCAP100
        size, count = wt.DWORD(0), wt.DWORD(0)
        ret = self.dll.PdhGetFormattedCounterArrayW(h, fmt, ct.byref(size), ct.byref(count), None)
        if (ret & 0xFFFFFFFF) != self.MORE_DATA or size.value == 0:
            return {}
        buf = (ct.c_byte * size.value)()
        ret = self.dll.PdhGetFormattedCounterArrayW(h, fmt, ct.byref(size), ct.byref(count),
                                                    ct.cast(buf, ct.c_void_p))
        if ret != 0:
            return {}
        items = ct.cast(buf, ct.POINTER(self.Item))
        out = {}
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus in (0, 1):  # PDH_CSTATUS_VALID_DATA / NEW_DATA
                out[it.szName] = it.FmtValue.doubleValue
        return out


def _core_key(name):
    """'0,5' -> (0, 5); '_Total' und '0,_Total' -> None."""
    parts = name.split(",")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


class _HardwareMonitorTemp(threading.Thread):
    """Liest die CPU-Temperatur aus LibreHardwareMonitor/OpenHardwareMonitor (WMI),
    sofern eines der Programme läuft. Beendet sich, wenn keins vorhanden ist."""

    _PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$inv = [Globalization.CultureInfo]::InvariantCulture
while ($true) {
  $t = $null
  foreach ($ns in 'root/LibreHardwareMonitor', 'root/OpenHardwareMonitor') {
    $s = Get-CimInstance -Namespace $ns -ClassName Sensor -Filter "SensorType='Temperature'" |
         Where-Object { $_.Identifier -match 'cpu' }
    if ($s) {
      $pick = $s | Where-Object { $_.Name -match 'Package|Tctl|Tdie' } | Select-Object -First 1
      if (-not $pick) { $pick = $s | Sort-Object Value -Descending | Select-Object -First 1 }
      $t = [double]$pick.Value
      break
    }
  }
  if ($t -ne $null) { [Console]::Out.WriteLine("T:" + $t.ToString($inv)) }
  else { [Console]::Out.WriteLine("T:") }
  [Console]::Out.Flush()
  Start-Sleep -Seconds 2
}
"""

    def __init__(self):
        super().__init__(daemon=True)
        self.value = None
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", self._PS_SCRIPT],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, creationflags=NO_WINDOW)
        except Exception:
            return
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("T:"):
                continue
            try:
                t = float(line[2:].replace(",", "."))
                self.value = round(t, 1) if _valid_temp(t) else None
            except ValueError:
                self.value = None


class CpuSensors:
    def __init__(self):
        self._pdh = None
        self._hwmon = None
        if IS_WINDOWS:
            try:
                self._pdh = _Pdh({
                    "perf": r"\Processor Information(*)\% Processor Performance",
                    "freq": r"\Processor Information(*)\Processor Frequency",
                    "zone": r"\Thermal Zone Information(*)\Temperature",
                })
            except Exception as e:
                print("Leistungsindikatoren nicht verfügbar:", e)
            self._hwmon = _HardwareMonitorTemp()
            self._hwmon.start()

    def read(self):
        """-> (Takt je Kern in MHz oder None, mittlerer Takt in MHz, Temperatur in °C)"""
        cores = self._core_freqs()
        if cores:
            valid = [f for f in cores if f]
            avg = round(sum(valid) / len(valid)) if valid else None
        else:
            avg = None
            try:
                freq = psutil.cpu_freq()
                avg = round(freq.current) if freq and freq.current else None
            except Exception:
                pass
        return cores, avg, self._temperature()

    def _core_freqs(self):
        if self._pdh is not None:
            try:
                self._pdh.collect()
                perf = self._pdh.values("perf")
                base = self._pdh.values("freq")
                cores = []
                for name in sorted((n for n in perf if _core_key(n)), key=_core_key):
                    b = base.get(name)
                    cores.append(round(b * perf[name] / 100.0) if b else None)
                if cores:
                    return cores
            except Exception:
                pass
        try:
            freqs = psutil.cpu_freq(percpu=True)
            if freqs and len(freqs) > 1:
                return [round(f.current) if f.current else None for f in freqs]
        except Exception:
            pass
        return None

    def _temperature(self):
        if self._hwmon is not None and self._hwmon.value is not None:
            return self._hwmon.value
        if self._pdh is not None:
            try:
                zones = [k - 273.15 for k in self._pdh.values("zone").values()]
                zones = [t for t in zones if _valid_temp(t)]
                if zones:
                    return round(max(zones), 1)
            except Exception:
                pass
        try:
            temps = psutil.sensors_temperatures()  # nur Linux/BSD
        except Exception:
            return None
        for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            entries = temps.get(key)
            if entries:
                t = max(e.current for e in entries)
                if _valid_temp(t):
                    return round(t, 1)
        return None
