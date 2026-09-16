(function () {
'use strict';
// WebGL2 renderer for the PC-98 frame: RGB565 straight from the core, filtered on the GPU.
//
// The frame arrives as raw 16-bit pixels (no PNG encode on the server, no decode here) and is
// uploaded with texSubImage2D as an RGB565 texture. Every filter is one fragment shader over
// that texture; they are written for this cockpit, after the ideas of the libretro CRT shaders
// (sharp-bilinear, crt-lottes-style beam and mask, aperture grille, LCD grid).

const VERT = `#version 300 es
in vec2 aPos;
out vec2 vUV;
void main() {
  vUV = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5);
  gl_Position = vec4(aPos, 0.0, 1.0);
}`;

const HEAD = `#version 300 es
precision highp float;
uniform sampler2D uTex;
uniform vec2 uSrc;      // source size in pixels (640x400)
uniform vec2 uOut;      // output size in device pixels
uniform vec4 uP;        // x: strength  y: curvature  z: glow  w: mask
in vec2 vUV;
out vec4 outColor;

vec3 tex(vec2 uv) { return texture(uTex, uv).rgb; }
vec3 texel(vec2 t) { return texture(uTex, (floor(t) + 0.5) / uSrc).rgb; }

// Barrel distortion, output -> source. The page uses the same formula to map the pointer.
vec2 curve(vec2 uv, float k) {
  if (k <= 0.0) return uv;
  vec2 c = uv * 2.0 - 1.0;
  c *= 1.0 + k * vec2(c.y * c.y, c.x * c.x);
  return c * 0.5 + 0.5;
}

// 1 inside the tube, 0 outside, with rounded corners.
float tube(vec2 uv, float k) {
  vec2 d = abs(uv - 0.5) * 2.0;
  if (k <= 0.0) return step(d.x, 1.0) * step(d.y, 1.0);
  float r = 0.03 + k * 0.25;
  vec2 q = max(d - (1.0 - r), 0.0);
  float dist = length(q) / r;
  return 1.0 - smoothstep(0.92, 1.0, dist);
}

// Sharp bilinear: pixels stay square, but the non-integer scale is smoothed at the edges
// instead of leaving some columns one device pixel wider than others.
vec3 sharp(vec2 uv) {
  vec2 t = uv * uSrc;
  vec2 scale = max(floor(uOut / uSrc), vec2(1.0));
  vec2 f = fract(t) - 0.5;
  vec2 region = 0.5 - 0.5 / scale;
  vec2 m = (f - clamp(f, -region, region)) * scale + 0.5;
  return tex((floor(t) + m) / uSrc);
}

vec3 toLin(vec3 c) { return pow(c, vec3(2.2)); }
vec3 toSrgb(vec3 c) { return pow(max(c, 0.0), vec3(1.0 / 2.2)); }

// One scanline: a gaussian across 4 horizontal taps.
vec3 hbeam(float row, float px) {
  float x0 = floor(px);
  vec3 s = vec3(0.0);
  float ws = 0.0;
  for (int i = -1; i <= 2; i++) {
    float x = x0 + float(i);
    float d = x - px;
    float w = exp2(-3.0 * d * d);
    s += toLin(tex((vec2(x, row) + 0.5) / uSrc)) * w;
    ws += w;
  }
  return s / ws;
}

// The beam: two nearest scanlines, each a vertical gaussian; bright lines get wider.
vec3 beam(vec2 uv, float hardness) {
  vec2 pos = uv * uSrc - 0.5;
  float j0 = floor(pos.y);
  vec3 a = hbeam(j0, pos.x), b = hbeam(j0 + 1.0, pos.x);
  float da = pos.y - j0, db = j0 + 1.0 - pos.y;
  float la = dot(a, vec3(0.3, 0.59, 0.11)), lb = dot(b, vec3(0.3, 0.59, 0.11));
  float ha = hardness * (1.0 - 0.45 * clamp(la, 0.0, 1.0));
  float hb = hardness * (1.0 - 0.45 * clamp(lb, 0.0, 1.0));
  return a * exp2(ha * da * da) + b * exp2(hb * db * db);
}

// Phosphor masks, in device pixels. kind 0: slot mask, 1: aperture grille.
vec3 mask(vec2 fc, float amount, int kind) {
  float lo = 1.0 - 0.55 * amount, hi = 1.0 + 0.35 * amount;
  float x = fc.x;
  if (kind == 0) x += floor(fc.y / 2.0) * 1.5;
  float m = mod(x, 3.0);
  vec3 c = vec3(lo);
  if (m < 1.0) c.r = hi; else if (m < 2.0) c.g = hi; else c.b = hi;
  if (kind == 0 && mod(fc.y, 2.0) < 1.0) c *= mix(1.0, 0.85, amount);
  return c;
}

// Cheap bloom: a wide blur of the source, added back in linear light.
vec3 glow(vec2 uv) {
  vec2 px = 1.6 / uSrc;
  vec3 s = vec3(0.0);
  s += toLin(tex(uv + vec2(-2.0, 0.0) * px)) + toLin(tex(uv + vec2(2.0, 0.0) * px));
  s += toLin(tex(uv + vec2(0.0, -2.0) * px)) + toLin(tex(uv + vec2(0.0, 2.0) * px));
  s += toLin(tex(uv + vec2(-1.4, -1.4) * px)) + toLin(tex(uv + vec2(1.4, 1.4) * px));
  s += toLin(tex(uv + vec2(-1.4, 1.4) * px)) + toLin(tex(uv + vec2(1.4, -1.4) * px));
  return s / 8.0;
}

float vignette(vec2 uv, float k) {
  vec2 d = uv - 0.5;
  return clamp(1.0 - dot(d, d) * (0.6 + k * 1.6), 0.0, 1.0);
}
`;

const BODIES = {
  pixel: `void main() { outColor = vec4(sharp(vUV), 1.0); }`,

  smooth: `void main() { outColor = vec4(tex(vUV), 1.0); }`,

  scanlines: `void main() {
    vec3 c = sharp(vUV);
    float d = fract(vUV.y * uSrc.y) - 0.5;
    float l = dot(c, vec3(0.3, 0.59, 0.11));
    float sigma = mix(0.2, 0.38, l);
    float w = exp(-d * d / (2.0 * sigma * sigma));
    c *= mix(1.0, w * 1.3, uP.x);
    outColor = vec4(c, 1.0);
  }`,

  crt: `void main() {
    float k = uP.y;
    vec2 uv = curve(vUV, k);
    float inside = tube(uv, k);
    vec3 c = beam(uv, mix(-4.0, -14.0, uP.x));
    c *= mask(gl_FragCoord.xy, uP.w, 0);
    c += glow(uv) * uP.z * 0.55;
    c *= 1.0 + 0.55 * uP.x;
    c *= mix(1.0, vignette(uv, k), 0.8);
    outColor = vec4(toSrgb(c) * inside, 1.0);
  }`,

  trinitron: `void main() {
    float k = uP.y;
    vec2 uv = curve(vUV, k) * vec2(1.0, 1.0);
    uv.y = mix(vUV.y, uv.y, 0.15);            // a Trinitron curves sideways only
    float inside = tube(uv, k * 0.5);
    vec3 c = beam(uv, mix(-5.0, -16.0, uP.x));
    c *= mask(gl_FragCoord.xy, uP.w, 1);
    c += glow(uv) * uP.z * 0.5;
    c *= 1.0 + 0.5 * uP.x;
    c *= mix(1.0, vignette(uv, k * 0.5), 0.6);
    outColor = vec4(toSrgb(c) * inside, 1.0);
  }`,

  lcd: `void main() {
    vec2 t = vUV * uSrc;
    vec3 c = texel(t);
    vec2 f = fract(t);
    vec2 cells = uOut / uSrc;
    float e = clamp(1.2 / min(cells.x, cells.y), 0.04, 0.5);
    float gx = smoothstep(0.0, e, f.x) * smoothstep(1.0, 1.0 - e, f.x);
    float gy = smoothstep(0.0, e, f.y) * smoothstep(1.0, 1.0 - e, f.y);
    c *= mix(1.0, 0.45 + 0.55 * gx * gy, uP.x);
    vec3 sub = f.x < 0.333 ? vec3(1.08, 0.94, 0.94) : f.x < 0.666 ? vec3(0.94, 1.08, 0.94) : vec3(0.94, 0.94, 1.08);
    c *= mix(vec3(1.0), sub, uP.w);
    outColor = vec4(c * 1.08, 1.0);
  }`,

  amber: `void main() {
    float k = uP.y;
    vec2 uv = curve(vUV, k);
    float inside = tube(uv, k);
    vec3 b = beam(uv, mix(-4.0, -12.0, uP.x));
    float l = dot(b, vec3(0.3, 0.59, 0.11));
    float g = dot(glow(uv), vec3(0.3, 0.59, 0.11)) * uP.z * 0.8;
    vec3 phos = vec3(1.0, 0.62, 0.18);
    vec3 c = phos * (l * (1.0 + 0.8 * uP.x) + g) + vec3(0.012, 0.006, 0.0);
    c *= mix(1.0, vignette(uv, k), 0.8);
    outColor = vec4(toSrgb(c) * inside, 1.0);
  }`,
};

// What the page offers. `sliders` names the uP components the preset actually uses.
const FILTERS = [
  { id: 'pixel', name: 'Pixel (sharp)', sliders: [], p: [0, 0, 0, 0] },
  { id: 'smooth', name: 'Smooth', sliders: [], p: [0, 0, 0, 0] },
  { id: 'scanlines', name: 'Scanlines', sliders: ['strength'], p: [0.55, 0, 0, 0] },
  { id: 'crt', name: 'CRT (slot mask)', sliders: ['strength', 'curvature', 'glow', 'mask'], p: [0.6, 0.0, 0.45, 0.5] },
  { id: 'crt_curved', body: 'crt', name: 'CRT curved', sliders: ['strength', 'curvature', 'glow', 'mask'], p: [0.6, 0.12, 0.5, 0.5] },
  { id: 'trinitron', name: 'Trinitron (PC-98 monitor)', sliders: ['strength', 'curvature', 'glow', 'mask'], p: [0.5, 0.06, 0.35, 0.55] },
  { id: 'lcd', name: 'LCD grid', sliders: ['strength', 'mask'], p: [0.5, 0, 0, 0.3] },
  { id: 'amber', name: 'Amber monochrome', sliders: ['strength', 'curvature', 'glow'], p: [0.55, 0.1, 0.6, 0] },
];
const SLIDER_INDEX = { strength: 0, curvature: 1, glow: 2, mask: 3 };

class Screen {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: false, preserveDrawingBuffer: false,
                                             powerPreference: 'high-performance' });
    if (!gl) throw new Error('WebGL2 is not available in this browser');
    this.gl = gl;
    this.programs = {};
    this.filter = FILTERS[0];
    this.params = FILTERS[0].p.slice();
    this.src = [640, 400];
    this.dirty = true;
    this.frames = 0;
    this.buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    this.texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 2);
    this.texSize = [0, 0];
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  _compile(type, src) {
    const gl = this.gl, s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }

  _program(f) {
    const key = f.body || f.id;
    if (this.programs[key]) return this.programs[key];
    const gl = this.gl, p = gl.createProgram();
    gl.attachShader(p, this._compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(p, this._compile(gl.FRAGMENT_SHADER, HEAD + BODIES[key]));
    gl.bindAttribLocation(p, 0, 'aPos');
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = n => gl.getUniformLocation(p, n);
    return (this.programs[key] = { p, uTex: u('uTex'), uSrc: u('uSrc'), uOut: u('uOut'), uP: u('uP') });
  }

  setFilter(id, params) {
    this.filter = FILTERS.find(f => f.id === id) || FILTERS[0];
    this.params = (params || this.filter.p).slice();
    this.dirty = true;
  }

  setParams(params) { this.params = params.slice(); this.dirty = true; }

  // Pixels as a Uint16Array of width*height RGB565 values.
  frame(w, h, pixels) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    if (this.texSize[0] !== w || this.texSize[1] !== h) {
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB565, w, h, 0, gl.RGB, gl.UNSIGNED_SHORT_5_6_5, pixels);
      this.texSize = [w, h];
      this.src = [w, h];
    } else {
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, w, h, gl.RGB, gl.UNSIGNED_SHORT_5_6_5, pixels);
    }
    this.frames++;
    this.dirty = true;
  }

  resize(cssW, cssH) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(cssW * dpr)), h = Math.max(1, Math.round(cssH * dpr));
    this.canvas.style.width = cssW + 'px';
    this.canvas.style.height = cssH + 'px';
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    this.dirty = true;
  }

  draw() {
    const gl = this.gl, pr = this._program(this.filter);
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.useProgram(pr.p);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.uniform1i(pr.uTex, 0);
    gl.uniform2f(pr.uSrc, this.src[0], this.src[1]);
    gl.uniform2f(pr.uOut, this.canvas.width, this.canvas.height);
    gl.uniform4f(pr.uP, ...this.params);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this.dirty = false;
  }

  _loop() {
    if (this.dirty && this.texSize[0]) {
      try { this.draw(); } catch (e) { console.error('[screen]', e); this.setFilter('pixel'); }
    }
    requestAnimationFrame(this._loop);
  }

  // Page position (0..1 inside the canvas) -> source pixel, through the same distortion.
  toSource(u, v) {
    let k = SLIDER_INDEX.curvature !== undefined && this.filter.sliders.includes('curvature') ? this.params[1] : 0;
    if (this.filter.id === 'trinitron') {
      const cx = u * 2 - 1, cy = v * 2 - 1;
      const x = cx * (1 + k * cy * cy), y = cy * (1 + k * cx * cx);
      u = x * 0.5 + 0.5;
      v = (v + ((y * 0.5 + 0.5) - v) * 0.15);
    } else if (k > 0) {
      const cx = u * 2 - 1, cy = v * 2 - 1;
      u = (cx * (1 + k * cy * cy)) * 0.5 + 0.5;
      v = (cy * (1 + k * cx * cx)) * 0.5 + 0.5;
    }
    return [Math.max(0, Math.min(this.src[0] - 1, Math.floor(u * this.src[0]))),
            Math.max(0, Math.min(this.src[1] - 1, Math.floor(v * this.src[1])))];
  }

  // A PNG of what is on screen right now, filter included.
  snapshot() {
    this.draw();
    return new Promise(res => this.canvas.toBlob(res, 'image/png'));
  }
}

window.WWScreen = { Screen, FILTERS, SLIDER_INDEX };
})();
