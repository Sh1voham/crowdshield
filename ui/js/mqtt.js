// MiniMqtt: a subscribe-only MQTT 3.1.1 client over WebSocket (subprotocol "mqtt").
// Handles QoS 0/1 (PUBACK), messages split across WebSocket frames, keepalive and reconnect.

const enc = new TextEncoder(), dec = new TextDecoder();

function varint(n) { const out = []; do { let b = n % 128; n = Math.floor(n / 128); if (n > 0) b |= 128; out.push(b); } while (n > 0); return out; }
function str(s) { const b = enc.encode(s); return [b.length >> 8, b.length & 255, ...b]; }

export class MiniMqtt {
  constructor(url, topic, { onMessage, onState, keepalive = 30 } = {}) {
    this.url = url; this.topic = topic; this.onMessage = onMessage || (() => {}); this.onState = onState || (() => {});
    this.keepalive = keepalive; this.buf = new Uint8Array(0); this.backoff = 500; this.closed = false; this.pid = 1;
    this.connect();
  }

  connect() {
    if (this.closed) return;
    this.onState("connecting");
    let ws;
    try { ws = new WebSocket(this.url, ["mqtt"]); } catch (e) { this.retry(); return; }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      const id = "cs-ui-" + Math.random().toString(16).slice(2, 10);
      const vh = [...str("MQTT"), 4, 0x02, this.keepalive >> 8, this.keepalive & 255];
      const payload = str(id);
      ws.send(new Uint8Array([0x10, ...varint(vh.length + payload.length), ...vh, ...payload]));
    };
    ws.onmessage = (ev) => this.feed(new Uint8Array(ev.data));
    ws.onclose = () => { clearInterval(this.ping); this.onState("down"); this.retry(); };
    ws.onerror = () => {};
  }

  retry() {
    if (this.closed) return;
    setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 8000);
  }

  feed(chunk) {
    const b = new Uint8Array(this.buf.length + chunk.length);
    b.set(this.buf); b.set(chunk, this.buf.length);
    this.buf = b;
    for (;;) {
      if (this.buf.length < 2) return;
      let mult = 1, len = 0, i = 1, byte;
      do {
        if (i >= this.buf.length) return;
        byte = this.buf[i++]; len += (byte & 127) * mult; mult *= 128;
      } while (byte & 128);
      if (this.buf.length < i + len) return;
      const head = this.buf[0], body = this.buf.subarray(i, i + len);
      this.buf = this.buf.slice(i + len);
      this.packet(head, body);
    }
  }

  packet(head, body) {
    const type = head >> 4;
    if (type === 2) { // CONNACK
      if (body[1] !== 0) { this.onState("refused"); return; }
      this.backoff = 500;
      const pid = this.pid++;
      const p = [pid >> 8, pid & 255, ...str(this.topic), 1];
      this.ws.send(new Uint8Array([0x82, ...varint(p.length), ...p]));
      clearInterval(this.ping);
      this.ping = setInterval(() => { try { this.ws.send(new Uint8Array([0xC0, 0])); } catch (e) {} }, this.keepalive * 500);
      this.onState("up");
    } else if (type === 3) { // PUBLISH
      const qos = (head >> 1) & 3;
      const tl = (body[0] << 8) | body[1];
      const topic = dec.decode(body.subarray(2, 2 + tl));
      let off = 2 + tl;
      if (qos > 0) {
        const pid = (body[off] << 8) | body[off + 1]; off += 2;
        this.ws.send(new Uint8Array([0x40, 2, pid >> 8, pid & 255]));
      }
      this.onMessage(topic, dec.decode(body.subarray(off)));
    }
  }

  close() { this.closed = true; clearInterval(this.ping); try { this.ws.close(); } catch (e) {} }
}
