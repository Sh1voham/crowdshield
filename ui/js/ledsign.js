// Dot-matrix LED gate sign: exactly what the ESP32 shows. 64 x 16 pixels with per-pixel glow.
// On a change the new text flips in pixel by pixel (once, ~600 ms).

import { reducedMotion, cssVar } from "./util.js";

// 5x7 font (columns as bit rows, top to bottom), only the glyphs the sign needs.
const FONT = {
  A: ["01110", "10001", "10001", "11111", "10001", "10001", "10001"], C: ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
  D: ["11110", "10001", "10001", "10001", "10001", "10001", "11110"], E: ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
  F: ["11111", "10000", "10000", "11110", "10000", "10000", "10000"], H: ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
  I: ["11111", "00100", "00100", "00100", "00100", "00100", "11111"], L: ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
  M: ["10001", "11011", "10101", "10101", "10001", "10001", "10001"], N: ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
  O: ["01110", "10001", "10001", "10001", "10001", "10001", "01110"], P: ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
  R: ["11110", "10001", "10001", "11110", "10100", "10010", "10001"], S: ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
  T: ["11111", "00100", "00100", "00100", "00100", "00100", "00100"], W: ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
  Y: ["10001", "10001", "01010", "00100", "00100", "00100", "00100"], " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
  "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"], "?": ["01110", "10001", "00001", "00110", "00100", "00000", "00100"],
};

function bitmap(text, cols = 64, rows = 16) {
  const px = new Uint8Array(cols * rows);
  const words = text.split(" ");
  const lines = words.length > 1 && text.length * 6 > cols ? [words[0], words.slice(1).join(" ")] : [text];
  const lh = 7, gap = 1, total = lines.length * lh + (lines.length - 1) * gap;
  let y0 = Math.floor((rows - total) / 2);
  for (const line of lines) {
    const w = line.length * 6 - 1, x0 = Math.floor((cols - w) / 2);
    [...line].forEach((ch, i) => {
      const g = FONT[ch] || FONT["?"];
      for (let r = 0; r < 7; r++) for (let c = 0; c < 5; c++) if (g[r][c] === "1") {
        const x = x0 + i * 6 + c, y = y0 + r;
        if (x >= 0 && x < cols && y >= 0 && y < rows) px[y * cols + x] = 1;
      }
    });
    y0 += lh + gap;
  }
  return px;
}

export class LedSign {
  constructor(canvas) {
    this.canvas = canvas; this.ctx = canvas.getContext("2d");
    this.cols = 64; this.rows = 16;
    this.cur = bitmap("", 64, 16); this.target = this.cur; this.color = "#555"; this.flip = null;
    this.order = Array.from({ length: 64 * 16 }, (_, i) => i).sort(() => Math.random() - 0.5);
    this.draw();
  }

  show(text, colorVar, offline = false) {
    const key = `${text}|${colorVar}|${offline}`;
    if (key === this.key) return;
    this.key = key;
    this.color = cssVar(colorVar) || "#ccc";
    this.offline = offline;
    const next = bitmap(text, this.cols, this.rows);
    if (reducedMotion() || !this.text) { this.cur = next; this.target = next; this.flip = null; }
    else { this.from = this.cur; this.target = next; this.flip = performance.now(); }
    this.text = text;
    this.dirty = true;
  }

  tick(now) {
    if (this.flip !== null) {
      const u = Math.min(1, (now - this.flip) / 600), n = Math.floor(u * this.order.length);
      const out = this.from.slice();
      for (let i = 0; i < n; i++) { const k = this.order[i]; out[k] = this.target[k]; }
      this.cur = out;
      if (u >= 1) this.flip = null;
      this.draw();
    } else if (this.dirty) { this.draw(); this.dirty = false; }
  }

  draw() {
    const c = this.ctx, W = this.canvas.width, H = this.canvas.height, p = W / this.cols;
    c.fillStyle = "#05080d"; c.fillRect(0, 0, W, H);
    for (let y = 0; y < this.rows; y++) for (let x = 0; x < this.cols; x++) {
      const on = this.cur[y * this.cols + x];
      const cx = x * p + p / 2, cy = y * p + p / 2;
      if (on) {
        c.fillStyle = this.color; c.globalAlpha = 0.22;
        c.beginPath(); c.arc(cx, cy, p * 0.78, 0, Math.PI * 2); c.fill();
        c.globalAlpha = 1;
        c.beginPath(); c.arc(cx, cy, p * 0.36, 0, Math.PI * 2); c.fill();
      } else {
        c.fillStyle = "#151b25"; c.globalAlpha = 1;
        c.beginPath(); c.arc(cx, cy, p * 0.3, 0, Math.PI * 2); c.fill();
      }
    }
    c.globalAlpha = 1;
  }
}
