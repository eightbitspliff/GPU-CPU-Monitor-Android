"""WLAN-Empfang und aktueller Netzwerk-Traffic.

Windows: WLAN über die Native-WiFi-API (wlanapi.dll, sprachunabhängig),
Linux:   /proc/net/wireless und iwgetid, soweit vorhanden.
Traffic: psutil (Bytes je Sekunde der WLAN-Karte, sonst der aktivsten Verbindung).
"""

import os
import re
import shutil
import subprocess
import time

import psutil

IS_WINDOWS = os.name == "nt"

_WIFI_NAME = re.compile(r"wlan|wi-?fi|wireless|funk|^wl", re.IGNORECASE)
_VIRTUAL_NAME = re.compile(r"vethernet|virtual|vmware|vbox|hyper-v|docker|"
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


# --------------------------------------------------------------------------- Gesamt

class NetSensors:
    def __init__(self):
        self._wlan = None
        if IS_WINDOWS:
            try:
                self._wlan = _WlanApi()
            except Exception as e:
                print("WLAN-Abfrage nicht verfügbar:", e)
        self._last = None  # (Zeit, {nic: (gesendet, empfangen)})
        self._wifi_cache = None

    def read(self):
        return {"wifi": self._wifi(), "traffic": self._traffic()}

    def _wifi(self):
        # WLAN-Empfang ändert sich langsam: nur alle 5 s neu abfragen
        now = time.monotonic()
        if self._wifi_cache is not None and now - self._wifi_cache[0] < 5:
            return self._wifi_cache[1]
        value = self._wifi_now()
        self._wifi_cache = (now, value)
        return value

    def _wifi_now(self):
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
        rates, virtual = {}, {}
        for n, (sent, recv) in cur.items():
            prev = last[1].get(n)
            st = stats.get(n)
            if prev is None or (st is not None and not st.isup) or re.search(r"loopback|^lo$", n, re.I):
                continue
            r = (max(0, recv - prev[1]) / dt, max(0, sent - prev[0]) / dt)
            (virtual if _VIRTUAL_NAME.search(n) else rates)[n] = r
        if not rates:
            rates = virtual  # nur virtuelle Adapter (z.B. VM) -> die nehmen
        if not rates:
            return None
        wifi = [n for n in rates if _WIFI_NAME.search(n)]
        # WLAN-Karte bevorzugen, sonst die Verbindung mit dem meisten Verkehr
        name = wifi[0] if wifi else max(rates, key=lambda n: sum(rates[n]))
        down, up = rates[name]
        return {"iface": name, "wifi": bool(wifi), "down_bps": round(down * 8), "up_bps": round(up * 8)}
