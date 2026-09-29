"""Web-Dashboard (gleiche Ansicht wie die Android-App).

Wird vom Server unter http://<pc-ip>:47811/ ausgeliefert und auf den
Nest Hub gecastet. Funktioniert auch in jedem Browser.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PC Monitor</title>
<style>
  :root {
    --bg:#0E1116; --card:#171B22; --text:#E8ECF2; --muted:#8A94A6;
    --cpu:#3FA9F5; --gpu:#7BD85A; --ram:#F5A623; --track:#262C36; --err:#FF5C5C;
  }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font-family:"Google Sans",Roboto,"Segoe UI",Arial,sans-serif; overflow:hidden; }
  body.cast { cursor:none; }
  .wrap { height:100%; display:flex; flex-direction:column; padding:2vh 1.8vw; gap:1.8vh; }
  header { display:flex; align-items:baseline; gap:1.2vw; }
  #clock { font-size:6vh; font-weight:600; font-variant-numeric:tabular-nums; color:var(--text);
    padding-right:1.2vw; border-right:2px solid var(--track); }
  #host { font-size:6vh; font-weight:600; }
  #status { font-size:3.6vh; color:var(--muted); }
  #status.err { color:var(--err); }
  .main { flex:1; min-height:0; display:grid; gap:1.8vh 1.4vw;
    grid-template-columns: minmax(0,0.85fr) minmax(0,0.85fr) minmax(0,1.8fr); grid-template-rows: 1fr auto; }
  .card { background:var(--card); border-radius:2vh; padding:1.6vh 1.2vw; min-height:0; min-width:0; }
  .gauge { grid-row:1 / 3; display:flex; flex-direction:column; align-items:center; justify-content:center; }
  .gauge svg { width:100%; flex:1; min-height:0; }
  .gauge .name { color:var(--muted); font-size:3.6vh; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; max-width:100%; text-align:center; }
  .gauge .info { font-size:5vh; line-height:1.25; margin-top:.6vh; min-height:5.5vh; text-align:center;
    display:flex; flex-direction:column; align-items:center; }
  .gauge .info span { white-space:nowrap; }
  .track { fill:none; stroke:var(--track); stroke-width:8; stroke-linecap:round; }
  .arc { fill:none; stroke-width:8; stroke-linecap:round; transition:stroke-dasharray .6s ease-out; }
  .val { font-size:22px; font-weight:600; fill:var(--text); text-anchor:middle; }
  .lbl { font-size:11px; fill:var(--muted); text-anchor:middle; letter-spacing:.5px; }
  .graph { display:flex; flex-direction:column; }
  .graph .t { color:var(--muted); font-size:3.6vh; margin-bottom:1vh; }
  .graph canvas { flex:1; min-height:0; width:100%; }
  .bars .row { font-size:4.2vh; }
  .bars .row + .row { margin-top:1.4vh; }
  .bar { height:1.8vh; background:var(--track); border-radius:1vh; margin-top:.8vh; overflow:hidden; }
  .bar > div { height:100%; width:0; border-radius:1vh; transition:width .6s ease-out; }
  @media (max-aspect-ratio: 1/1) {
    .main { grid-template-columns:minmax(0,1fr) minmax(0,1fr); grid-template-rows: 1.3fr 1fr auto; }
    .gauge { grid-row:auto; }
    .graph, .bars { grid-column:1 / 3; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header><div id="clock">--:--</div><div id="host">PC Monitor</div><div id="status">Verbinde…</div></header>
  <div class="main">
    <div class="card gauge" id="cpu">
      <svg viewBox="0 0 100 92"><path class="track" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100"/>
        <path class="arc" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100" stroke="var(--cpu)" stroke-dasharray="0 100"/>
        <text class="val" x="50" y="56">–</text><text class="lbl" x="50" y="84">CPU</text></svg>
      <div class="name">–</div><div class="info"></div>
    </div>
    <div class="card gauge" id="gpu">
      <svg viewBox="0 0 100 92"><path class="track" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100"/>
        <path class="arc" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100" stroke="var(--gpu)" stroke-dasharray="0 100"/>
        <text class="val" x="50" y="56">–</text><text class="lbl" x="50" y="84">GPU</text></svg>
      <div class="name">–</div><div class="info"></div>
    </div>
    <div class="card graph"><div class="t">Verlauf (5 Minuten)</div><canvas id="graph"></canvas></div>
    <div class="card bars">
      <div class="row"><span id="ramT">RAM –</span><div class="bar"><div id="ramB" style="background:var(--ram)"></div></div></div>
      <div class="row"><span id="vramT">VRAM –</span><div class="bar"><div id="vramB" style="background:var(--gpu)"></div></div></div>
    </div>
  </div>
</div>
<script>
(function () {
  if (/[?&]cast=1/.test(location.search)) document.body.className = "cast";
  var CAP = 300,  // 5 Minuten bei einem Wert pro Sekunde
      hist = { cpu: [], gpu: [] }, fails = 0;
  function $(id) { return document.getElementById(id); }
  function pct(v) { return v == null ? "–" : Math.round(v) + " %"; }
  function gb(mb) { return mb == null ? "–" : (mb / 1024).toFixed(1).replace(".", ",") + " GB"; }
  function setGauge(id, v, name, info) {
    var el = $(id);
    var val = v == null ? 0 : Math.max(0, Math.min(100, v));
    el.querySelector(".arc").setAttribute("stroke-dasharray", val + " 100");
    el.querySelector(".val").textContent = v == null ? "–" : Math.round(v) + "%";
    el.querySelector(".name").textContent = name;
    var box = el.querySelector(".info");
    box.textContent = "";
    info.forEach(function (t) { var sp = document.createElement("span"); sp.textContent = t; box.appendChild(sp); });
  }
  function push(arr, v) { arr.push(v); while (arr.length > CAP) arr.shift(); }

  function draw() {
    var c = $("graph"), dpr = window.devicePixelRatio || 1;
    var w = c.clientWidth, h = c.clientHeight;
    if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
    var g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.strokeStyle = "#262C36"; g.lineWidth = 1; g.fillStyle = "#8A94A6";
    g.font = Math.round(Math.max(12, window.innerHeight * 0.03)) + "px sans-serif";
    [0, 25, 50, 75, 100].forEach(function (p) {
      var y = h - h * p / 100;
      g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
      if (p > 0 && p < 100) g.fillText(p, 3, y - 4);
    });
    var step = w / (CAP - 1);
    [["cpu", "#3FA9F5"], ["gpu", "#7BD85A"]].forEach(function (s) {
      var q = hist[s[0]], off = CAP - q.length, first = null, last = null;
      g.beginPath();
      q.forEach(function (v, i) {
        if (v == null) return;
        var x = (off + i) * step, y = h - h * Math.max(0, Math.min(100, v)) / 100;
        if (first === null) { g.moveTo(x, y); first = x; } else g.lineTo(x, y);
        last = x;
      });
      if (first === null) return;
      g.strokeStyle = s[1]; g.lineWidth = 2.5; g.lineJoin = "round"; g.stroke();
      g.lineTo(last, h); g.lineTo(first, h); g.closePath();
      g.fillStyle = s[1] + "22"; g.fill();
    });
  }

  function render(d) {
    fails = 0;
    $("host").textContent = d.host;
    $("status").className = ""; $("status").textContent = "Live";
    var cpu = d.cpu, ram = d.ram, gpu = (d.gpus && d.gpus[0]) || null;
    var ci = [];
    if (cpu.power_w != null) ci.push(Math.round(cpu.power_w) + " W");
    if (cpu.temp_c != null) ci.push(Math.round(cpu.temp_c) + " °C");
    setGauge("cpu", cpu.usage, cpu.name, ci);
    var gi = [];
    if (gpu && gpu.power_w != null) gi.push(Math.round(gpu.power_w) + " W");
    if (gpu && gpu.temp_c != null) gi.push(Math.round(gpu.temp_c) + " °C");
    setGauge("gpu", gpu ? gpu.usage : null, gpu ? gpu.name : "Keine GPU-Daten", gi);
    $("ramT").textContent = "RAM  " + gb(ram.used_mb) + " / " + gb(ram.total_mb) + "  (" + pct(ram.usage) + ")";
    $("ramB").style.width = ram.usage + "%";
    if (gpu && gpu.mem_used_mb != null) {
      var t = gpu.mem_total_mb, p = t ? gpu.mem_used_mb / t * 100 : null;
      $("vramT").textContent = t ? "VRAM  " + gb(gpu.mem_used_mb) + " / " + gb(t) + "  (" + pct(p) + ")"
                                 : "VRAM  " + gb(gpu.mem_used_mb) + " belegt";
      $("vramB").style.width = (p || 0) + "%";
    } else { $("vramT").textContent = "VRAM –"; $("vramB").style.width = "0"; }
    push(hist.cpu, cpu.usage); push(hist.gpu, gpu ? gpu.usage : null);
    draw();
  }

  function fail() {
    fails++;
    $("status").className = "err"; $("status").textContent = "Keine Verbindung zum PC";
    if (fails >= 3) { setGauge("cpu", null, "–", []); setGauge("gpu", null, "–", []); }
    push(hist.cpu, null); push(hist.gpu, null); draw();
  }

  function poll() {
    var x = new XMLHttpRequest();
    x.open("GET", "/stats?t=" + Date.now());
    x.timeout = 2500;
    x.onload = function () {
      try { if (x.status === 200) render(JSON.parse(x.responseText)); else fail(); } catch (e) { fail(); }
    };
    x.onerror = x.ontimeout = fail;
    x.send();
  }
  function tick() {
    var d = new Date();
    $("clock").textContent = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  tick(); setInterval(tick, 1000);
  window.addEventListener("resize", draw);
  poll(); setInterval(poll, 1000);
})();
</script>
</body>
</html>
"""
