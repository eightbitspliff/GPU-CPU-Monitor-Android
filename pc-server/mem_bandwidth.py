"""RAM-Bandbreite.

Die aktuelle RAM-Bandbreite misst Windows selbst nicht – dafür braucht es die
Leistungszähler des Speicher-Controllers. Die liest HWiNFO mit eigenem Treiber;
läuft HWiNFO mit aktiviertem "Shared Memory Support", übernehmen wir dessen Werte.
Immer verfügbar ist die maximale (theoretische) Bandbreite aus RAM-Takt und Kanälen.
"""

import os
import re
import struct
import subprocess
import threading
import time

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _max_bandwidth_windows():
    """-> (GB/s, Beschreibung) aus Takt und Anzahl der Module."""
    script = ("$m = Get-CimInstance Win32_PhysicalMemory; "
              "$m | ForEach-Object { '' + $_.ConfiguredClockSpeed + ';' + $_.Speed + ';' + $_.SMBIOSMemoryType }")
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            text=True, timeout=20, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
    except Exception:
        return None, None
    speeds, kinds = [], set()
    for line in out.splitlines():
        parts = line.strip().split(";")
        if len(parts) < 3:
            continue
        try:
            mts = int(parts[0] or 0) or int(parts[1] or 0)
        except ValueError:
            continue
        if mts:
            speeds.append(mts)
        kinds.add({"26": "DDR4", "34": "DDR5", "24": "DDR3"}.get(parts[2], ""))
    if not speeds:
        return None, None
    mts = min(speeds)
    channels = 2 if len(speeds) >= 2 else 1  # Desktop: 2 Module = Dual-Channel (4 Module ebenso)
    kind = next((k for k in kinds if k), "RAM")
    gbs = round(mts * 8 * channels / 1000, 1)
    return gbs, f"{kind}-{mts} {'Dual' if channels == 2 else 'Single'}-Channel"


class _HwinfoReader:
    """Liest Werte aus dem Shared Memory von HWiNFO (nur lesend öffnen, nie anlegen)."""

    NAME = "Global\\HWiNFO_SENS_SM2"
    SIGNATURE = 0x53695748  # 'HWiS'

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.OpenFileMappingW.restype = wintypes.HANDLE
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k = k

    def readings(self):
        """-> Liste (Label, Einheit, Wert) oder None, wenn HWiNFO nicht läuft."""
        k, ct = self.k, self.ct
        h = k.OpenFileMappingW(0x0004, False, self.NAME)  # FILE_MAP_READ
        if not h:
            return None
        try:
            view = k.MapViewOfFile(h, 0x0004, 0, 0, 0)
            if not view:
                return None
            try:
                head = ct.string_at(view, 44)
                (sig, _ver, _rev, _t, _so, _ss, _sn,
                 r_off, r_size, r_num) = struct.unpack_from("<IIIqIIIIII", head)
                if sig != self.SIGNATURE or r_size < 292 or r_num > 100000:
                    return None
                data = ct.string_at(view + r_off, r_size * r_num)
            finally:
                k.UnmapViewOfFile(view)
        finally:
            k.CloseHandle(h)
        out = []
        for i in range(r_num):
            base = i * r_size
            label = data[base + 12:base + 140].split(b"\0", 1)[0].decode("latin-1", "replace")
            unit = data[base + 268:base + 284].split(b"\0", 1)[0].decode("latin-1", "replace")
            value = struct.unpack_from("<d", data, base + 284)[0]
            out.append((label, unit, value))
        return out


def _to_gbs(value, unit):
    u = unit.strip().lower()
    if u in ("gb/s", "gbps"):  # HWiNFO schreibt teils "Gbps", meint aber Gigabyte/s
        return value
    if u == "mb/s":
        return value / 1000
    if u in ("tb/s",):
        return value * 1000
    return None


class RamBandwidth(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.max_gbs = None
        self.max_desc = None
        self.current_gbs = None
        self.source = None
        self._hwinfo = None
        if IS_WINDOWS:
            try:
                self._hwinfo = _HwinfoReader()
            except Exception:
                self._hwinfo = None
        self.start()

    def run(self):
        if IS_WINDOWS:
            self.max_gbs, self.max_desc = _max_bandwidth_windows()
        while self._hwinfo is not None:
            try:
                self.current_gbs = self._from_hwinfo()
                self.source = "HWiNFO" if self.current_gbs is not None else None
            except Exception:
                self.current_gbs = None
            time.sleep(1)

    def _from_hwinfo(self):
        rows = self._hwinfo.readings()
        if not rows:
            return None
        read = write = total = None
        for label, unit, value in rows:
            gbs = _to_gbs(value, unit)
            if gbs is None:
                continue
            lab = label.lower()
            if "dram" not in lab and "memory" not in lab:
                continue
            if "gpu" in lab:
                continue
            if re.search(r"read", lab) and read is None:
                read = gbs
            elif re.search(r"write", lab) and write is None:
                write = gbs
            elif re.search(r"total|bandwidth", lab) and total is None:
                total = gbs
        if read is not None or write is not None:
            return round((read or 0) + (write or 0), 1)
        return round(total, 1) if total is not None else None

    def read(self):
        return {
            "bandwidth_gbs": self.current_gbs,
            "bandwidth_max_gbs": self.max_gbs,
            "bandwidth_desc": self.max_desc,
            "bandwidth_source": self.source,
        }
