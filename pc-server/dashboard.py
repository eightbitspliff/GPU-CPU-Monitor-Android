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
  .wrap { height:100%; display:flex; flex-direction:column; padding:2.2vh 2.2vw; gap:2vh; }
  header { display:flex; align-items:baseline; gap:1.2vw; }
  #host { font-size:3.6vh; font-weight:600; }
  #status { font-size:2.2vh; color:var(--muted); }
  #status.err { color:var(--err); }
  .main { flex:1; min-height:0; display:grid; gap:2vh 1.6vw;
    grid-template-columns: minmax(0,1fr) minmax(0,1fr) minmax(0,1.5fr); grid-template-rows: 1fr 1fr; }
  .card { background:var(--card); border-radius:2vh; padding:1.8vh 1.4vw; min-height:0; min-width:0; }
  .gauge { grid-row:1 / 3; display:flex; flex-direction:column; align-items:center; justify-content:center; }
  .gauge svg { width:100%; flex:1; min-height:0; }
  .gauge .name { color:var(--muted); font-size:1.9vh; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; max-width:100%; text-align:center; }
  .gauge .info { font-size:2.4vh; margin-top:.6vh; min-height:3vh; text-align:center; }
  .track { fill:none; stroke:var(--track); stroke-width:8; stroke-linecap:round; }
  .arc { fill:none; stroke-width:8; stroke-linecap:round; transition:stroke-dasharray .6s ease-out; }
  .val { font-size:19px; font-weight:600; fill:var(--text); text-anchor:middle; }
  .lbl { font-size:8.5px; fill:var(--muted); text-anchor:middle; letter-spacing:.5px; }
  .graph { display:flex; flex-direction:column; }
  .graph .t { color:var(--muted); font-size:1.9vh; margin-bottom:1vh; }
  .graph canvas { flex:1; min-height:0; width:100%; }
  .gauge .mem { width:100%; font-size:min(2.2vh, 2.5vw); margin-top:1.6vh; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; }
  .bar { height:1.2vh; background:var(--track); border-radius:1vh; margin-top:.8vh; overflow:hidden; }
  .bar > div { height:100%; width:0; border-radius:1vh; transition:width .6s ease-out; }
  @media (max-aspect-ratio: 1/1) {
    .main { grid-template-columns:minmax(0,1fr) minmax(0,1fr); grid-template-rows: 1.3fr 1fr 1fr; }
    .gauge { grid-row:auto; }
    .graph { grid-column:1 / 3; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header><div id="host">PC Monitor</div><div id="status">Verbinde…</div></header>
  <div class="main">
    <div class="card gauge" id="cpu">
      <svg viewBox="0 0 100 92"><path class="track" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100"/>
        <path class="arc" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100" stroke="var(--cpu)" stroke-dasharray="0 100"/>
        <text class="val" x="50" y="56">–</text><text class="lbl" x="50" y="84">CPU</text></svg>
      <div class="name">–</div><div class="info"></div>
      <div class="mem"><span id="ramT">RAM –</span><div class="bar"><div id="ramB" style="background:var(--ram)"></div></div></div>
    </div>
    <div class="card gauge" id="gpu">
      <svg viewBox="0 0 100 92"><path class="track" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100"/>
        <path class="arc" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100" stroke="var(--gpu)" stroke-dasharray="0 100"/>
        <text class="val" x="50" y="56">–</text><text class="lbl" x="50" y="84">GPU</text></svg>
      <div class="name">–</div><div class="info"></div>
      <div class="mem"><span id="vramT">VRAM –</span><div class="bar"><div id="vramB" style="background:var(--gpu)"></div></div></div>
    </div>
    <div class="card graph"><div class="t">Verlauf (60 Minuten)</div><canvas id="graphLong" data-min="60"></canvas></div>
    <div class="card graph"><div class="t">Verlauf (15 Minuten)</div><canvas id="graphShort" data-min="15"></canvas></div>
  </div>
</div>
<script>
(function () {
  if (/[?&]cast=1/.test(location.search)) document.body.className = "cast";
  // Ein Wert pro Sekunde, 60 Minuten lang; das 15-Minuten-Diagramm zeigt den letzten Teil davon.
  var CAP = 3600, hist = { cpu: [], gpu: [] }, fails = 0;
  function $(id) { return document.getElementById(id); }
  function pct(v) { return v == null ? "–" : Math.round(v) + " %"; }
  function num(mb) { return mb == null ? "–" : (mb / 1024).toFixed(1).replace(".", ","); }
  function gb(mb) { return mb == null ? "–" : num(mb) + " GB"; }
  function setGauge(id, v, name, info) {
    var el = $(id);
    var val = v == null ? 0 : Math.max(0, Math.min(100, v));
    el.querySelector(".arc").setAttribute("stroke-dasharray", val + " 100");
    el.querySelector(".val").textContent = v == null ? "–" : Math.round(v) + "%";
    el.querySelector(".name").textContent = name;
    el.querySelector(".info").textContent = info;
  }
  function push(arr, v) { arr.push(v); while (arr.length > CAP) arr.shift(); }

  function drawGraph(c) {
    var dpr = window.devicePixelRatio || 1;
    var w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
    var g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    var mins = +c.getAttribute("data-min"), cap = mins * 60;
    g.strokeStyle = "#262C36"; g.lineWidth = 1; g.fillStyle = "#8A94A6";
    g.font = "11px sans-serif";
    [0, 25, 50, 75, 100].forEach(function (p) {
      var y = h - h * p / 100;
      g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
      if (p > 0 && p < 100) g.fillText(p, 2, y - 3);
    });
    // Zeitmarken: 60 min -> alle 15 min, 15 min -> alle 5 min
    var parts = mins % 4 === 0 ? 4 : 3;
    for (var k = 1; k < parts; k++) {
      var x = w * k / parts, m = mins * (parts - k) / parts;
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
      g.fillText("-" + m + " min", x + 3, h - 4);
    }
    // Werte in Eimer mitteln: höchstens ein Punkt pro ~1,5 px
    var per = Math.max(1, Math.ceil(cap / (w / 1.5))), buckets = Math.ceil(cap / per);
    var step = buckets > 1 ? w / (buckets - 1) : w;
    [["cpu", "#3FA9F5"], ["gpu", "#7BD85A"]].forEach(function (s) {
      var q = hist[s[0]].slice(-cap), off = cap - q.length, first = null, last = null;
      g.beginPath();
      for (var b = 0; b < buckets; b++) {
        var sum = 0, n = 0;
        for (var i = b * per; i < Math.min(cap, (b + 1) * per); i++) {
          var v = q[i - off];
          if (i >= off && v != null) { sum += v; n++; }
        }
        if (!n) continue;
        var x = b * step, y = h - h * Math.max(0, Math.min(100, sum / n)) / 100;
        if (first === null) { g.moveTo(x, y); first = x; } else g.lineTo(x, y);
        last = x;
      }
      if (first === null) return;
      g.strokeStyle = s[1]; g.lineWidth = 2.5; g.lineJoin = "round"; g.stroke();
      g.lineTo(last, h); g.lineTo(first, h); g.closePath();
      g.fillStyle = s[1] + "22"; g.fill();
    });
  }
  function draw() { drawGraph($("graphLong")); drawGraph($("graphShort")); }

  function loadHistory() {
    var x = new XMLHttpRequest();
    x.open("GET", "/history?t=" + Date.now());
    x.timeout = 5000;
    x.onload = function () {
      try {
        if (x.status !== 200) return;
        var d = JSON.parse(x.responseText);
        if (d.cpu && d.cpu.length) { hist.cpu = d.cpu.slice(-CAP); hist.gpu = (d.gpu || []).slice(-CAP); draw(); }
      } catch (e) {}
    };
    x.send();
  }

  function render(d) {
    fails = 0;
    $("host").textContent = d.host;
    $("status").className = ""; $("status").textContent = "Live";
    var cpu = d.cpu, ram = d.ram, gpu = (d.gpus && d.gpus[0]) || null;
    var ci = [];
    if (cpu.freq_mhz != null) ci.push((cpu.freq_mhz / 1000).toFixed(2).replace(".", ",") + " GHz");
    if (cpu.temp_c != null) ci.push(Math.round(cpu.temp_c) + " °C");
    setGauge("cpu", cpu.usage, cpu.name, ci.join("  ·  "));
    var gi = [];
    if (gpu && gpu.temp_c != null) gi.push(Math.round(gpu.temp_c) + " °C");
    if (gpu && gpu.power_w != null) gi.push(Math.round(gpu.power_w) + " W");
    setGauge("gpu", gpu ? gpu.usage : null, gpu ? gpu.name : "Keine GPU-Daten", gi.join("  ·  "));
    $("ramT").textContent = "RAM  " + num(ram.used_mb) + " / " + gb(ram.total_mb) + "  ·  " + pct(ram.usage);
    $("ramB").style.width = ram.usage + "%";
    if (gpu && gpu.mem_used_mb != null) {
      var t = gpu.mem_total_mb, p = t ? gpu.mem_used_mb / t * 100 : null;
      $("vramT").textContent = t ? "VRAM  " + num(gpu.mem_used_mb) + " / " + gb(t) + "  ·  " + pct(p)
                                 : "VRAM  " + gb(gpu.mem_used_mb) + " belegt";
      $("vramB").style.width = (p || 0) + "%";
    } else { $("vramT").textContent = "VRAM –"; $("vramB").style.width = "0"; }
    push(hist.cpu, cpu.usage); push(hist.gpu, gpu ? gpu.usage : null);
    draw();
  }

  function fail() {
    fails++;
    $("status").className = "err"; $("status").textContent = "Keine Verbindung zum PC";
    if (fails >= 3) { setGauge("cpu", null, "–", ""); setGauge("gpu", null, "–", ""); }
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
  window.addEventListener("resize", draw);
  loadHistory(); poll(); setInterval(poll, 1000);
})();
</script>
</body>
</html>
"""
