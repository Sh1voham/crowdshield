// Shared helpers for the timeline views (seismograph, river).
export { cssVar, isNum } from "./util.js";

export function lowerBoundW(arr, w) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m].w < w) lo = m + 1; else hi = m; }
  return lo;
}
