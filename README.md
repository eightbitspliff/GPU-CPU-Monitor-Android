# PC Monitor – CPU/GPU-Auslastung des PCs auf dem Android-Handy

Zwei Teile:

| Teil | Ordner | Läuft auf |
|---|---|---|
| **PC Monitor Server** – misst CPU, GPU, RAM, VRAM, Temperaturen | `pc-server/` | Windows-PC (auch Linux/macOS) |
| **PC Monitor App** – zeigt die Werte live an | `android-app/` | Android 8.0+ |

Funktioniert nur im eigenen Netzwerk (WLAN/LAN), kein Fernzugriff, kein Konto.

## Installation

1. Unter **Releases** (bzw. *Actions → Build PC Monitor → Artifacts*) herunterladen:
   - `PCMonitorServer.exe` → auf den PC
   - `PCMonitor.apk` → aufs Handy (Installation aus unbekannten Quellen erlauben)
2. `PCMonitorServer.exe` auf dem PC starten. Beim ersten Start fragt die Windows-Firewall –
   **„Private Netzwerke“ zulassen**. Das Fenster zeigt die IP-Adresse des PCs an.
3. App öffnen → sie sucht den PC automatisch. Klappt das nicht: oben auf den Titel tippen
   und die IP aus dem Server-Fenster eintragen.

Der Server hat ein kleines Fenster mit IP-Adresse und Live-Werten. **Minimieren legt ihn in den
Systemtray** (Icon unten rechts neben der Uhr, Tooltip zeigt CPU/GPU). Doppelklick aufs Icon holt das
Fenster zurück, Rechtsklick → „Beenden“ schließt ihn.

Tipp: Damit der Server immer läuft, eine Verknüpfung zur EXE in den Autostart-Ordner legen
(`Win+R` → `shell:startup`) und in den Eigenschaften der Verknüpfung
hinter das Ziel ` --tray` schreiben – dann startet er direkt unsichtbar im Tray.

## App-Funktionen

- Große Anzeigen für **CPU** und **GPU** (Auslastung in %), dazu Takt, Temperatur, Stromverbrauch
- Verlaufsdiagramm der letzten 2 Minuten
- RAM- und VRAM-Belegung
- Bildschirm bleibt an (z.B. als Zweitdisplay neben dem PC), abschaltbar im Menü ⋮
- Optional **dauerhafte Anzeige in der Benachrichtigungsleiste** (Menü ⋮ → „Anzeige in Benachrichtigung“),
  läuft auch weiter, wenn die App geschlossen ist
- Hoch- und Querformat

## Anzeige auf dem Google Nest Hub (oder Chromecast)

Der PC-Server kann die gleiche Ansicht wie die App direkt auf einen Nest Hub streamen:

- **Am PC:** im Server-Fenster unter „Auf Nest Hub / Chromecast anzeigen“ das Gerät wählen → **Anzeigen**
- **oder am Handy:** Menü ⋮ → **„Auf Nest Hub anzeigen…“** → Gerät antippen

Der Server merkt sich das Gerät und startet die Anzeige automatisch wieder – nach einem
Neustart des PCs, oder wenn der Hub sie nach einer Weile beendet. Nutzt jemand den Hub
gerade für etwas anderes (Musik, Wetter, Timer …), wird das nicht unterbrochen; die Anzeige
kommt zurück, sobald der Hub wieder frei ist. **Stoppen** beendet das dauerhaft.

Das Dashboard gibt es auch im Browser: `http://<pc-ip>:47811/`

Technik: Gestreamt wird über die Cast-App „DashCast“, die eine Webseite auf dem Gerät öffnet.
Der Nest Hub lädt die Seite direkt vom PC; das Handy muss dafür nicht an sein.

## GPU-Unterstützung

| GPU | Quelle | Werte |
|---|---|---|
| NVIDIA | NVML / `nvidia-smi` | Auslastung, VRAM, Temperatur, Leistung, Takt |
| AMD / Intel unter Windows | Windows-Leistungsindikatoren + LibreHardwareMonitorLib | Auslastung, VRAM, Temperatur, Leistung, Takt |
| AMD unter Linux | sysfs | Auslastung, VRAM, Temperatur, Takt |

## CPU-Temperatur unter Windows

Windows gibt die CPU-Temperatur nur über einen Treiber heraus. Die `PCMonitorServer.exe`
bringt dafür LibreHardwareMonitorLib und den Installer des signierten Open-Source-Treibers
**PawnIO** mit (den nutzt auch LibreHardwareMonitor selbst):

- Die EXE startet deshalb mit Administratorrechten (Windows fragt beim Start nach).
- Beim ersten Start fragt das Server-Fenster, ob PawnIO installiert werden soll; später
  geht das über den Knopf **„CPU-Temperatur aktivieren“** im Server-Fenster.

## Server ohne EXE (mit Python)

```
cd pc-server
pip install -r requirements.txt
python pc_monitor_server.py
```

## Technik

- HTTP `GET http://<pc-ip>:47811/stats` liefert JSON
- UDP-Port `47810` für die automatische Suche
- App selbst bauen: `cd android-app && ./gradlew assembleRelease` (Android SDK nötig);
  GitHub Actions baut APK und EXE bei jedem Push automatisch.
