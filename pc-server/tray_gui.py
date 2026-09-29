"""Fenster + Systemtray für den PC Monitor Server (Windows).

Minimieren -> Fenster verschwindet, Icon bleibt im Systemtray.
Doppelklick aufs Tray-Icon oder "Anzeigen" holt das Fenster zurück.
"""

import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import pystray

import autostart
import caster as caster_mod
from app_icon import make_icon

LHM_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"

BG = "#0E1116"
CARD = "#171B22"
TEXT = "#E8ECF2"
MUTED = "#8A94A6"
CPU = "#3FA9F5"
GPU = "#7BD85A"


def show_error(msg):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("PC Monitor Server", msg)
    root.destroy()


def _pct(v):
    return "–" if v is None else f"{round(v)} %"


def run(sampler, caster, ips, port, start_hidden=False):
    root = tk.Tk()
    if start_hidden:
        root.withdraw()  # sofort unsichtbar, kein kurzes Aufblitzen beim Autostart
    root.title("PC Monitor Server")
    root.configure(bg=BG)
    root.resizable(False, False)
    icon_img = make_icon(64)
    try:
        from PIL import ImageTk
        root.iconphoto(True, ImageTk.PhotoImage(icon_img))
    except Exception:
        pass

    pad = {"padx": 16}
    tk.Label(root, text="PC Monitor Server läuft", bg=BG, fg=TEXT,
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(14, 2), **pad)
    tk.Label(root, text="In der App 'Suchen' tippen oder diese Adresse eintragen:",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", **pad)
    for ip in ips or ["(keine Netzwerkadresse gefunden)"]:
        tk.Label(root, text=f"{ip}:{port}" if ips else ip, bg=BG, fg=TEXT,
                 font=("Consolas", 12)).pack(anchor="w", **pad)

    card = tk.Frame(root, bg=CARD)
    card.pack(fill="x", pady=12, **pad)
    cpu_var = tk.StringVar(value="CPU –")
    gpu_var = tk.StringVar(value="GPU –")
    tk.Label(card, textvariable=cpu_var, bg=CARD, fg=CPU,
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
    tk.Label(card, text=sampler.cpu_name, bg=CARD, fg=MUTED,
             font=("Segoe UI", 8)).pack(anchor="w", padx=12)
    sensor_var = tk.StringVar(value="CPU Power/Temp: suche Quelle…")
    sensor_label = tk.Label(card, textvariable=sensor_var, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
                            wraplength=300, justify="left")
    sensor_label.pack(anchor="w", padx=12)
    lhm_link = tk.Label(card, text="→ LibreHardwareMonitor herunterladen", bg=CARD, fg=CPU,
                        font=("Segoe UI", 8, "underline"), cursor="hand2")
    lhm_link.bind("<Button-1>", lambda e: webbrowser.open(LHM_URL))
    lhm_shown = [False]
    tk.Label(card, textvariable=gpu_var, bg=CARD, fg=GPU,
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
    tk.Label(card, text="GPU-Quelle: " + (sampler.gpu.source or "keine GPU-Daten gefunden"),
             bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 10))

    # ------------------------------------------------ Nest Hub / Chromecast
    cast_card = tk.Frame(root, bg=CARD)
    cast_card.pack(fill="x", pady=(0, 12), **pad)
    tk.Label(cast_card, text="Auf Nest Hub / Chromecast anzeigen", bg=CARD, fg=TEXT,
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
    row = tk.Frame(cast_card, bg=CARD)
    row.pack(fill="x", padx=12)
    device_var = tk.StringVar(value=caster.target or "")
    combo = ttk.Combobox(row, textvariable=device_var, width=26,
                         values=[caster.target] if caster.target else [])
    combo.pack(side="left")
    search_btn = ttk.Button(row, text="Suchen")
    search_btn.pack(side="left", padx=(6, 0))
    row2 = tk.Frame(cast_card, bg=CARD)
    row2.pack(fill="x", padx=12, pady=(6, 0))
    start_btn = ttk.Button(row2, text="Anzeigen")
    start_btn.pack(side="left")
    stop_btn = ttk.Button(row2, text="Stoppen")
    stop_btn.pack(side="left", padx=(6, 0))
    cast_status = tk.StringVar(value=caster.status)
    tk.Label(cast_card, textvariable=cast_status, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
             wraplength=300, justify="left").pack(anchor="w", padx=12, pady=(6, 10))

    caster.listeners.append(lambda text: root.after(0, cast_status.set, text))

    def do_search():
        search_btn.configure(state="disabled", text="Suche…")

        def work():
            try:
                names = [d["name"] for d in caster.discover()]
            except Exception:
                names = []

            def done():
                search_btn.configure(state="normal", text="Suchen")
                combo.configure(values=names)
                if names and device_var.get() not in names:
                    device_var.set(names[0])
                if not names:
                    cast_status.set("Kein Gerät gefunden – gleiches WLAN? Firewall?")
            root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def do_start():
        name = device_var.get().strip()
        if name:
            caster.start(name)
        else:
            cast_status.set("Erst 'Suchen' und ein Gerät wählen")

    search_btn.configure(command=do_search)
    start_btn.configure(command=do_start)
    stop_btn.configure(command=caster.stop)
    if not caster.state()["available"]:
        for b in (search_btn, start_btn, stop_btn):
            b.configure(state="disabled")
    elif not caster.target:
        do_search()

    tk.Label(root, text="Minimieren legt das Fenster in den Systemtray.\n"
                        "Schließen (X) beendet den Server komplett.", justify="left",
             bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 12), **pad)

    # ---------------------------------------------------------------- Tray
    def show_window(icon=None, item=None):
        root.after(0, _show)

    def _show():
        root.deiconify()
        root.state("normal")
        root.lift()
        root.focus_force()

    def quit_app(icon=None, item=None):
        root.after(0, _quit)

    def _quit():
        tray.stop()
        root.destroy()

    def toggle_autostart(icon=None, item=None):
        on = not autostart.is_enabled()
        autostart.set_enabled(on)
        with caster.lock:
            caster.cfg["autostart"] = on
            caster_mod._save_config(caster.cfg)

    tray = pystray.Icon(
        "pcmonitor", icon_img, "PC Monitor Server",
        menu=pystray.Menu(
            pystray.MenuItem("Anzeigen", show_window, default=True),
            pystray.MenuItem("Mit Windows starten", toggle_autostart,
                             checked=lambda item: autostart.is_enabled(),
                             visible=autostart.AVAILABLE),
            pystray.MenuItem("Beenden", quit_app),
        ),
    )
    threading.Thread(target=tray.run, daemon=True).start()

    def on_unmap(event):
        # Minimiert -> ganz ausblenden, nur das Tray-Icon bleibt.
        if event.widget is root and root.state() == "iconic":
            root.withdraw()

    root.bind("<Unmap>", on_unmap)
    root.protocol("WM_DELETE_WINDOW", _quit)

    # ------------------------------------------------------- Live-Werte
    def refresh():
        d = sampler.snapshot()
        if d:
            cpu = d["cpu"]["usage"]
            gpus = d.get("gpus") or []
            gpu = gpus[0]["usage"] if gpus else None
            cpu_var.set(f"CPU  {_pct(cpu)}")
            src = d.get("cpu_sensor_source")
            if src:
                sensor_var.set(f"CPU Power/Temp: {src}")
            elif d.get("cpu_sensor_running"):
                sensor_var.set("CPU Power/Temp: Windows liefert auf diesem PC keine Werte. "
                               "Mit LibreHardwareMonitor im Hintergrund erscheinen sie automatisch.")
            cpu_d = d.get("cpu") or {}
            need_link = bool(d.get("cpu_sensor_running")) and (
                cpu_d.get("power_w") is None or cpu_d.get("temp_c") is None) and "Libre" not in (src or "")
            if need_link != lhm_shown[0]:
                if need_link:
                    lhm_link.pack(anchor="w", padx=12, after=sensor_label)
                else:
                    lhm_link.pack_forget()
                lhm_shown[0] = need_link
            gpu_var.set(f"GPU  {_pct(gpu)}")
            try:
                tray.title = f"PC Monitor – CPU {_pct(cpu)} · GPU {_pct(gpu)}"
            except Exception:
                pass
        root.after(1000, refresh)

    refresh()
    if start_hidden:
        root.withdraw()
    root.mainloop()
