function fnv1a(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function hueChannel(p, q, input) {
  let t = input;
  if (t < 0) t += 1;
  if (t > 1) t -= 1;
  if (t < 1 / 6) return p + (q - p) * 6 * t;
  if (t < 1 / 2) return q;
  if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
  return p;
}

function hslToHex(hue, saturation, lightness) {
  const h = ((hue % 360) + 360) % 360 / 360;
  const q = lightness < .5
    ? lightness * (1 + saturation)
    : lightness + saturation - lightness * saturation;
  const p = 2 * lightness - q;
  const channels = [
    hueChannel(p, q, h + 1 / 3),
    hueChannel(p, q, h),
    hueChannel(p, q, h - 1 / 3),
  ];
  return `#${channels.map((channel) => Math.round(channel * 255).toString(16).padStart(2, "0")).join("")}`;
}

// Return a Three.js-safe, deterministic color. A fused global track keeps the
// same color for its lifetime, independent of the camera currently observing it.
export function trackColor(globalTrackId) {
  const key = String(globalTrackId || "unknown-track");
  return hslToHex(fnv1a(key) % 360, .76, .46);
}
