"""WLAN-Empfang, verbundene Bluetooth-Geräte und aktueller Netzwerk-Traffic.

Windows: WLAN über die Native-WiFi-API (wlanapi.dll, sprachunabhängig),
         Bluetooth über die Geräteeigenschaften (PowerShell im Hintergrund).
Linux:   /proc/net/wireless, iwgetid und bluetoothctl, soweit vorhanden.
Traffic: psutil (Bytes je Sekunde der WLAN-Karte, sonst der aktivsten Verbindung).
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time

import psutil

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

_WIFI_NAME = re.compile(r"wlan|wi-?fi|wireless|funk|^wl", re.IGNORECASE)
_VIRTUAL_NAME = re.compile(r"loopback|^lo$|vethernet|virtual|vmware|vbox|hyper-v|docker|"
                           r"tailscale|zerotier|wireguard|bluetooth|isatap|teredo|pseudo",
                           re.IGNORECASE)

# DOT11_PHY_TYPE -> WLAN-Standard
_PHY = {4: "802.11a", 5: "802.11g", 6: "802.11b", 7: "Wi-Fi 4", 8: "Wi-Fi 5",
        10: "Wi-Fi 6", 11: "Wi-Fi 7"}


# --------------------------------------------------------------------------- WLAN (Windows)

class _WlanApi:
    OPCODE_CURRENT_CONNECTION = 7
    OPCODE_CHANNEL_NUMBER = 8
    STATE_CONNECTED = 1

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class InterfaceInfo(ctypes.Structure):
            _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", ctypes.c_wchar * 256),
                        ("isState", ctypes.c_int)]

        class InterfaceList(ctypes.Structure):
            _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                        ("InterfaceInfo", InterfaceInfo * 1)]

        class Ssid(ctypes.Structure):
            _fields_ = [("uSSIDLength", wintypes.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]

        class Association(ctypes.Structure):
            _fields_ = [("dot11Ssid", Ssid), ("dot11BssType", ctypes.c_int),
                        ("dot11Bssid", ctypes.c_ubyte * 6), ("dot11PhyType", ctypes.c_int),
                        ("uDot11PhyIndex", wintypes.ULONG), ("wlanSignalQuality", wintypes.ULONG),
                        ("ulRxRate", wintypes.ULONG), ("ulTxRate", wintypes.ULONG)]

        class Security(ctypes.Structure):
            _fields_ = [("bSecurityEnabled", wintypes.BOOL), ("bOneXEnabled", wintypes.BOOL),
                        ("dot11AuthAlgorithm", ctypes.c_int), ("dot11CipherAlgorithm", ctypes.c_int)]

        class Connection(ctypes.Structure):
            _fields_ = [("isState", ctypes.c_int), ("wlanConnectionMode", ctypes.c_int),
                        ("strProfileName", ctypes.c_wchar * 256),
                        ("wlanAssociationAttributes", Association),
                        ("wlanSecurityAttributes", Security)]

        self.ct = ctypes
        self.InterfaceList, self.InterfaceInfo, self.Connection = InterfaceList, InterfaceInfo, Connection
        dll = ctypes.WinDLL("wlanapi")
        dll.WlanOpenHandle.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                       ctypes.POINTER(wintypes.HANDLE)]
        dll.WlanOpenHandle.restype = wintypes.DWORD
        dll.WlanEnumInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        dll.WlanEnumInterfaces.restype = wintypes.DWORD
        dll.WlanQueryInterface.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.c_int, ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.POINTER(ctypes.c_int)]
        dll.WlanQueryInterface.restype = wintypes.DWORD
        dll.WlanFreeMemory.argtypes = [ctypes.c_void_p]
        dll.WlanFreeMemory.restype = None
        self.dll = dll
        self.handle = wintypes.HANDLE()
        version = wintypes.DWORD()
        if dll.WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(self.handle)) != 0:
            raise OSError("Kein WLAN-Dienst")

    def _query(self, guid, opcode):
        ct, wt = self.ct, self.ct.wintypes
        size, data, vtype = wt.DWORD(), ct.c_void_p(), ct.c_int()
        if self.dll.WlanQueryInterface(self.handle, ct.byref(guid), opcode, None,
                                       ct.byref(size), ct.byref(data), ct.byref(vtype)) != 0:
            return None
        return data

    def read(self):
        ct = self.ct
        lst = ct.c_void_p()
        if self.dll.WlanEnumInterfaces(self.handle, None, ct.byref(lst)) != 0:
            return None
        try:
            head = ct.cast(lst, ct.POINTER(self.InterfaceList)).contents
            infos = ct.cast(ct.addressof(head.InterfaceInfo), ct.POINTER(self.InterfaceInfo))
            any_adapter = head.dwNumberOfItems > 0
            for i in range(head.dwNumberOfItems):
                info = infos[i]
                if info.isState != self.STATE_CONNECTED:
                    continue
                data = self._query(info.InterfaceGuid, self.OPCODE_CURRENT_CONNECTION)
                if not data:
                    continue
                try:
                    c = ct.cast(data, ct.POINTER(self.Connection)).contents
                    a = c.wlanAssociationAttributes
                    ssid = bytes(a.dot11Ssid.ucSSID[:a.dot11Ssid.uSSIDLength]).decode("utf-8", "replace")
                    result = {
                        "connected": True,
                        "ssid": ssid or c.strProfileName or None,
                        "signal": int(a.wlanSignalQuality),  # 0..100 %
                        "rssi_dbm": round(a.wlanSignalQuality / 2 - 100),  # Näherung laut Microsoft
                        "rx_mbps": round(a.ulRxRate / 1000),
                        "tx_mbps": round(a.ulTxRate / 1000),
                        "standard": _PHY.get(a.dot11PhyType),
                        "adapter": info.strInterfaceDescription,
                    }
                finally:
                    self.dll.WlanFreeMemory(data)
                ch = self._query(info.InterfaceGuid, self.OPCODE_CHANNEL_NUMBER)
                if ch:
                    try:
                        n = ct.cast(ch, ct.POINTER(ct.c_ulong)).contents.value
                        result["channel"] = n
                        result["band"] = "2,4 GHz" if n <= 14 else "5 GHz" if n <= 177 else "6 GHz"
                    finally:
                        self.dll.WlanFreeMemory(ch)
                return result
            return {"connected": False} if any_adapter else None
        finally:
            self.dll.WlanFreeMemory(lst)


def _linux_wifi():
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()[2:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[0].rstrip(":")
        try:
            link = float(parts[2].rstrip("."))
            level = float(parts[3].rstrip("."))
        except ValueError:
            continue
        ssid = None
        if shutil.which("iwgetid"):
            try:
                ssid = subprocess.check_output(["iwgetid", iface, "-r"], text=True, timeout=2,
                                               stderr=subprocess.DEVNULL).strip() or None
            except Exception:
                pass
        return {"connected": True, "ssid": ssid, "signal": round(min(100, link / 70 * 100)),
                "rssi_dbm": round(level) if level < 0 else None, "adapter": iface}
    return None


# --------------------------------------------------------------------------- Bluetooth

class _BluetoothWatcher(threading.Thread):
    """Fragt alle paar Sekunden die verbundenen Bluetooth-Geräte ab."""

    # DEVPKEY_Device_IsConnected bzw. Akkustand (Bluetooth-Geräte melden ihn dort)
    _PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$conn = '{83DA6326-97A6-4088-9453-A1923F573B29} 15'
$batt = '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2'
while ($true) {
  $all = Get-PnpDevice
  $radio = $all | Where-Object { $_.Class -eq 'Bluetooth' -and $_.InstanceId -notmatch '^BTH' -and $_.Status -eq 'OK' }
  $devs = $all | Where-Object { $_.InstanceId -match '^BTH(ENUM|LE)\\DEV_([0-9A-F]{12})' }
  $out = @()
  foreach ($d in $devs) {
    $mac = ([regex]::Match($d.InstanceId, 'DEV_([0-9A-F]{12})')).Groups[1].Value
    $c = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName $conn).Data
    if ($c -ne $true) { continue }
    $b = $null
    foreach ($s in ($all | Where-Object { $_.InstanceId -match $mac })) {
      $v = (Get-PnpDeviceProperty -InstanceId $s.InstanceId -KeyName $batt).Data
      if ($v -ne $null) { $b = [int]$v; break }
    }
    if ($out | Where-Object { $_.mac -eq $mac }) { continue }
    $out += [pscustomobject]@{ name = $d.FriendlyName; mac = $mac; battery = $b; le = ($d.InstanceId -match '^BTHLE') }
  }
  $j = [pscustomobject]@{ available = [bool]$radio; devices = @($out) } | ConvertTo-Json -Compress -Depth 4
  [Console]::Out.WriteLine($j)
  [Console]::Out.Flush()
  Start-Sleep -Seconds 5
}
"""

    def __init__(self):
        super().__init__(daemon=True)
        self.value = None

    def run(self):
        if IS_WINDOWS:
            self._run_windows()
        elif shutil.which("bluetoothctl"):
            while True:
                self.value = self._linux()
                time.sleep(5)

    def _run_windows(self):
        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", self._PS_SCRIPT],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except Exception:
            return
        for line in proc.stdout:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            devices = d.get("devices") or []
            if isinstance(devices, dict):  # PowerShell macht aus einer 1er-Liste ein Objekt
                devices = [devices]
            self.value = {
                "available": bool(d.get("available")),
                "devices": [{"name": x.get("name") or x.get("mac") or "Gerät",
                             "battery": x.get("battery")} for x in devices],
            }

    @staticmethod
    def _linux():
        try:
            out = subprocess.check_output(["bluetoothctl", "devices", "Connected"], text=True,
                                          timeout=3, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        devices = []
        for line in out.splitlines():
            m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
            if m:
                devices.append({"name": m.group(2), "battery": None})
        return {"available": True, "devices": devices}


# --------------------------------------------------------------------------- Gesamt

class NetSensors:
    def __init__(self):
        self._wlan = None
        if IS_WINDOWS:
            try:
                self._wlan = _WlanApi()
            except Exception as e:
                print("WLAN-Abfrage nicht verfügbar:", e)
        self._bt = _BluetoothWatcher()
        self._bt.start()
        self._last = None  # (Zeit, {nic: (gesendet, empfangen)})

    def read(self):
        return {"wifi": self._wifi(), "bluetooth": self._bt.value, "traffic": self._traffic()}

    def _wifi(self):
        try:
            if self._wlan is not None:
                return self._wlan.read()
            if not IS_WINDOWS:
                return _linux_wifi()
        except Exception:
            pass
        return None

    def _traffic(self):
        try:
            counters = psutil.net_io_counters(pernic=True)
            stats = psutil.net_if_stats()
        except Exception:
            return None
        now = time.monotonic()
        cur = {n: (c.bytes_sent, c.bytes_recv) for n, c in counters.items()}
        last, self._last = self._last, (now, cur)
        if last is None or now - last[0] <= 0:
            return None
        dt = now - last[0]
        rates = {}
        for n, (sent, recv) in cur.items():
            prev = last[1].get(n)
            st = stats.get(n)
            if prev is None or (st is not None and not st.isup) or _VIRTUAL_NAME.search(n):
                continue
            rates[n] = (max(0, recv - prev[1]) / dt, max(0, sent - prev[0]) / dt)
        if not rates:
            return None
        wifi = [n for n in rates if _WIFI_NAME.search(n)]
        # WLAN-Karte bevorzugen, sonst die Verbindung mit dem meisten Verkehr
        name = wifi[0] if wifi else max(rates, key=lambda n: sum(rates[n]))
        down, up = rates[name]
        return {"iface": name, "wifi": bool(wifi), "down_bps": round(down * 8), "up_bps": round(up * 8)}
