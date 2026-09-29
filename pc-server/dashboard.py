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
  .wrap { height:100%; display:flex; flex-direction:column; padding:2vh 1.8vw; gap:1.6vh; }
  header { display:flex; align-items:baseline; gap:1.6vw; }
  #clock { font-size:min(6vh, 5vw); font-weight:700; font-variant-numeric:tabular-nums; }
  #host { font-size:min(5vh, 4.5vw); font-weight:600; }
  #status { font-size:min(3.4vh, 3vw); color:var(--muted); }
  #status.err { color:var(--err); }
  .main { flex:1; min-height:0; display:grid; gap:1.8vh 1.4vw;
    grid-template-columns: minmax(0,1fr) minmax(0,1fr) minmax(0,1.4fr); grid-template-rows: 1fr 1fr; }
  .card { background:var(--card); border-radius:2vh; padding:1.6vh 1.3vw; min-height:0; min-width:0; }
  .gauge { grid-row:1 / 3; display:flex; flex-direction:column; align-items:center; justify-content:center; }
  .gauge svg { width:100%; flex:1; min-height:0; }
  .gauge .name { color:var(--muted); font-size:min(3.2vh, 2.4vw); white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; max-width:100%; text-align:center; }
  .kv { width:100%; display:flex; justify-content:space-between; align-items:baseline; gap:.6vw;
    margin-top:1.2vh; white-space:nowrap; }
  .kv span { font-size:min(3.3vh, 2.3vw); color:var(--muted); overflow:hidden; text-overflow:ellipsis; }
  .kv b { font-size:min(4.2vh, 3vw); font-variant-numeric:tabular-nums; }
  .track { fill:none; stroke:var(--track); stroke-width:8; stroke-linecap:round; }
  .arc { fill:none; stroke-width:8; stroke-linecap:round; transition:stroke-dasharray .6s ease-out; }
  .val { font-size:21px; font-weight:700; fill:var(--text); text-anchor:middle; }
  .lbl { font-size:11px; font-weight:600; fill:var(--muted); text-anchor:middle; letter-spacing:.5px; }
  .graph { display:flex; flex-direction:column; }
  .graph .t { color:var(--muted); font-size:min(3.4vh, 3vw); font-weight:600; margin-bottom:.6vh; }
  .graph canvas { flex:1; min-height:0; width:100%; }
  .gauge .mem { width:100%; margin-top:1.2vh; white-space:nowrap; }
  .mem .top { display:flex; justify-content:space-between; align-items:baseline; }
  .mem .top span { font-size:min(3.3vh, 2.3vw); color:var(--muted); }
  .mem .top b { font-size:min(4.2vh, 3vw); font-variant-numeric:tabular-nums; }
  .mem .det { font-size:min(3.4vh, 2.5vw); color:var(--muted); margin-top:.6vh; overflow:hidden; text-overflow:ellipsis; }
  .bar { height:1.8vh; background:var(--track); border-radius:1vh; margin-top:.8vh; overflow:hidden; }
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
  <header><div id="clock">--:--</div><div id="host">PC Monitor</div><div id="status">Verbinde…</div></header>
  <div class="main">
    <div class="card gauge" id="cpu">
      <svg viewBox="0 0 100 92"><path class="track" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100"/>
        <path class="arc" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100" stroke="var(--cpu)" stroke-dasharray="0 100"/>
        <text class="val" x="50" y="56">–</text><text class="lbl" x="50" y="84">CPU</text></svg>
      <div class="name">–</div>
      <div class="kv"><span>Power</span><b class="pow">–</b></div>
      <div class="kv"><span>Temp</span><b class="temp">–</b></div>
      <div class="mem"><div class="top"><span>RAM</span><b id="ramP">–</b></div>
        <div class="bar"><div id="ramB" style="background:var(--ram)"></div></div><div class="det" id="ramT">–</div></div>
    </div>
    <div class="card gauge" id="gpu">
      <svg viewBox="0 0 100 92"><path class="track" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100"/>
        <path class="arc" d="M21.7 78.3 A40 40 0 1 1 78.3 78.3" pathLength="100" stroke="var(--gpu)" stroke-dasharray="0 100"/>
        <text class="val" x="50" y="56">–</text><text class="lbl" x="50" y="84">GPU</text></svg>
      <div class="name">–</div>
      <div class="kv"><span>Power</span><b class="pow">–</b></div>
      <div class="kv"><span>Temp</span><b class="temp">–</b></div>
      <div class="mem"><div class="top"><span>VRAM</span><b id="vramP">–</b></div>
        <div class="bar"><div id="vramB" style="background:var(--gpu)"></div></div><div class="det" id="vramT">–</div></div>
    </div>
    <div class="card graph"><div class="t">Verlauf (15 Minuten)</div><canvas id="graphLong" data-min="15"></canvas></div>
    <div class="card graph"><div class="t">Verlauf (5 Minuten)</div><canvas id="graphShort" data-min="5"></canvas></div>
  </div>
</div>
<script>
(function () {
  if (/[?&]cast=1/.test(location.search)) document.body.className = "cast";
  // Ein Wert pro Sekunde, 15 Minuten lang; das 5-Minuten-Diagramm zeigt den letzten Teil davon.
  var CAP = 900, hist = { cpu: [], gpu: [] }, fails = 0;
  function $(id) { return document.getElementById(id); }
  function pct(v) { return v == null ? "–" : Math.round(v) + " %"; }
  function num(mb) { return mb == null ? "–" : (mb / 1024).toFixed(1).replace(".", ","); }
  function gb(mb) { return mb == null ? "–" : num(mb) + " GB"; }
  function watt(v) { return v == null ? "–" : Math.round(v) + " W"; }
  function temp(v) { return v == null ? "–" : Math.round(v) + " °C"; }
  function setGauge(id, v, name, power, tempC) {
    var el = $(id);
    var val = v == null ? 0 : Math.max(0, Math.min(100, v));
    el.querySelector(".arc").setAttribute("stroke-dasharray", val + " 100");
    el.querySelector(".val").textContent = v == null ? "–" : Math.round(v) + "%";
    el.querySelector(".name").textContent = name;
    el.querySelector(".pow").textContent = watt(power);
    el.querySelector(".temp").textContent = temp(tempC);
  }
  function push(arr, v) { arr.push(v); while (arr.length > CAP) arr.shift(); }

  function drawGraph(c) {
    var dpr = window.devicePixelRatio || 1;
    var W = c.clientWidth, H = c.clientHeight;
    if (!W || !H) return;
    if (c.width !== W * dpr || c.height !== H * dpr) { c.width = W * dpr; c.height = H * dpr; }
    var g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    var mins = +c.getAttribute("data-min"), cap = mins * 60;
    // gut lesbar auf dem 7"-Display des Nest Hub (1024x600)
    var fs = Math.max(12, Math.min(18, Math.round(window.innerHeight * 0.028)));
    g.font = "600 " + fs + "px sans-serif";
    // Beschriftung liegt in eigenen Rändern (links Prozent, unten Zeit) und verdeckt die Kurven nicht
    var left = Math.ceil(g.measureText("100").width) + 8, bottom = fs + 8, top = Math.ceil(fs / 2);
    var w = W - left, h = H - bottom - top;
    if (w <= 10 || h <= 10) return;
    g.save();
    g.translate(left, top);
    g.strokeStyle = "#262C36"; g.lineWidth = 1; g.fillStyle = "#8A94A6";
    g.textBaseline = "middle"; g.textAlign = "right";
    [0, 25, 50, 75, 100].forEach(function (p) {
      var y = Math.round(h - h * p / 100) + 0.5;
      g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
      if (p > 0) g.fillText(p, -6, y);
    });
    // Zeitachse: 15 min -> alle 5 min, 5 min -> jede Minute
    var tick = mins >= 15 ? 5 : 1;
    g.textBaseline = "top"; g.textAlign = "center";
    for (var m = 0; m < mins; m += tick) {  // linker Rand ohne Marke, die Spanne steht im Titel
      var x = Math.round(w - w * m / mins) + 0.5;
      if (m > 0) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke(); }
      g.textAlign = m === 0 ? "right" : "center";
      g.fillText(m === 0 ? "jetzt" : "-" + m + " min", x, h + 5);
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
    g.restore();
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
    setGauge("cpu", cpu.usage, cpu.name, cpu.power_w, cpu.temp_c);
    setGauge("gpu", gpu ? gpu.usage : null, gpu ? gpu.name : "Keine GPU-Daten",
             gpu ? gpu.power_w : null, gpu ? gpu.temp_c : null);
    $("ramP").textContent = pct(ram.usage);
    $("ramT").textContent = num(ram.used_mb) + " / " + gb(ram.total_mb);
    $("ramB").style.width = ram.usage + "%";
    if (gpu && gpu.mem_used_mb != null) {
      var t = gpu.mem_total_mb, p = t ? gpu.mem_used_mb / t * 100 : null;
      $("vramP").textContent = pct(p);
      $("vramT").textContent = t ? num(gpu.mem_used_mb) + " / " + gb(t) : gb(gpu.mem_used_mb) + " belegt";
      $("vramB").style.width = (p || 0) + "%";
    } else { $("vramP").textContent = "–"; $("vramT").textContent = "–"; $("vramB").style.width = "0"; }
    push(hist.cpu, cpu.usage); push(hist.gpu, gpu ? gpu.usage : null);
    draw();
  }

  function fail() {
    fails++;
    $("status").className = "err"; $("status").textContent = "Keine Verbindung zum PC";
    if (fails >= 3) { setGauge("cpu", null, "–", null, null); setGauge("gpu", null, "–", null, null); }
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
  loadHistory(); poll(); setInterval(poll, 1000);
})();
</script>
</body>
</html>
"""
