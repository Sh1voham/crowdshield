// Data sources. Default: the gateway WebSocket. Fallbacks: MQTT over WebSocket (port 9001)
// and the built-in simulator, which is always labelled "SIMULATED".
//   ?src=gateway (default) | ?src=mqtt&url=ws://HOST:9001&topic=crowdshield | ?src=sim&scenario=building&speed=4

import { MiniMqtt } from "./mqtt.js";
import { Sim } from "./sim/sim.js";

export function sourceFromURL() {
  const q = new URLSearchParams(location.search);
  return {
    src: q.get("src") || "gateway", url: q.get("url"), topic: q.get("topic") || "crowdshield",
    scenario: q.get("scenario") || "building", speed: parseFloat(q.get("speed") || "1") || 1,
  };
}

export function startSource(store, opts = sourceFromURL()) {
  if (opts.src === "sim") return startSim(store, opts);
  if (opts.src === "mqtt") return startMqtt(store, opts);
  return startGateway(store, opts);
}

function startGateway(store, opts) {
  const url = opts.url || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  store.sourceName = "gateway";
  let ws, backoff = 500, closed = false;
  const connect = () => {
    store.setConn("connecting", url);
    ws = new WebSocket(url);
    ws.onopen = () => { backoff = 500; store.setConn("up", url); loadHistory(store); };
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      const d = m.data;
      if (m.type === "state") store.pushState(d);
      else if (m.type === "alert") store.pushAlert(d);
      else if (m.type === "health") store.setHealth(d);
      else if (m.type === "status") { store.status = d; store.emit("status", d); }
      else if (m.type === "incident") refreshIncidents(store);
      else if (m.type === "hello" && d.health) store.setHealth(d.health);
    };
    ws.onclose = () => {
      store.setConn("down", url);
      if (!closed) { setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 8000); }
    };
  };
  connect();
  return { kind: "gateway", close() { closed = true; ws?.close(); } };
}

export async function loadHistory(store) {
  try {
    const r = await fetch("/api/history?fields=summary&since=0");
    if (!r.ok) return;
    const j = await r.json();
    store.setHistory(j.items, j.alerts);
  } catch (e) { /* history is optional */ }
  refreshIncidents(store);
}

export async function refreshIncidents(store) {
  try {
    const r = await fetch("/api/incidents");
    if (r.ok) store.setIncidents((await r.json()).items || []);
  } catch (e) { /* optional */ }
}

function startMqtt(store, opts) {
  const url = opts.url || `ws://${location.hostname || "localhost"}:9001`;
  store.sourceName = "mqtt";
  const prefix = opts.topic.replace(/\/#?$/, "");
  const client = new MiniMqtt(url, `${prefix}/#`, {
    onState: (s) => store.setConn(s === "up" ? "up" : s === "connecting" ? "connecting" : "down", url),
    onMessage: (topic, text) => {
      let d; try { d = JSON.parse(text); } catch (e) { return; }
      const suf = topic.slice(prefix.length + 1);
      if (suf === "state") store.pushState(d);
      else if (suf === "alerts") store.pushAlert(d);
      else if (suf === "status") { store.status = d; store.emit("status", d); }
    },
  });
  return { kind: "mqtt", close() { client.close(); } };
}

function startSim(store, opts) {
  store.sourceName = "sim";
  store.setConn("up", "built-in simulator");
  let sim = new Sim(opts.scenario, { seed: 7 });
  let timer = null, acc = 0, last = performance.now();
  const tick = () => {
    const now = performance.now();
    acc += ((now - last) / 1000) * opts.speed;
    last = now;
    let steps = 0;
    while (acc >= sim.dt && steps < 200) {
      acc -= sim.dt; steps++;
      const st = sim.step();
      if (st) {
        const { _events, _raw, ...msg } = st;
        store.pushState(msg);
        for (const e of _events) store.pushAlert({ v: 1, type: "alert", id: `sim-${msg.seq}-${e.kind}`, t: msg.t, zone: msg.zone,
          level: e.level, kind: e.kind, status: msg.status.level, action: msg.action,
          message: simMessage(e, msg), metrics: { dmax: msg.density.max, pidx: msg.pressure.index, count: msg.count } });
      }
      if (sim.done()) { sim = new Sim(opts.scenario, { seed: 7 + Math.floor(Math.random() * 1000) }); }
    }
  };
  timer = setInterval(tick, 50);
  return { kind: "sim", close() { clearInterval(timer); }, restart(scenario) { opts.scenario = scenario; sim = new Sim(scenario, { seed: 7 }); } };
}

function simMessage(e, msg) {
  const d = msg.density.max?.toFixed(1), p = msg.pressure.index?.toFixed(1);
  if (e.kind === "RECOVERED") return `Simulated: back to normal. Peak density ${d} people/m².`;
  if (e.kind === "TURBULENCE") return `Simulated: crowd turbulence, pressure ${p}× threshold at ${d} people/m².`;
  if (e.kind === "FORECAST") return `Simulated: Critical forecast in about ${Math.round(msg.forecast.t_red_s)} s.`;
  return `Simulated: ${e.level === "RED" ? "Critical" : "Building"} — ${e.kind === "PRESSURE" ? `pressure ${p}× threshold` : `density ${d} people/m²`}.`;
}
