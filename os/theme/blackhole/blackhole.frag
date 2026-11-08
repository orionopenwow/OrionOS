#version 300 es
// Orion living black hole: one full-screen fragment pass (GLSL ES 3.00 / WebGL2).
// Contract and uniform docs: os/theme/blackhole/README.md. Draw a full-screen triangle; no varyings needed.
// The painted accretion disk (black-hole-core.png) stays the source: it is sampled in polar coordinates,
// set flowing with Keplerian differential rotation, and dissolved into space at its rim.
precision highp float;

uniform vec2 u_resolution;
uniform float u_time;
uniform sampler2D u_disk;
uniform float u_spin;
uniform float u_temp;
uniform float u_ring;
uniform float u_glow;
uniform float u_turbulence;
uniform float u_size;
uniform float u_tilt;
uniform float u_boot;
uniform float u_voice;
uniform float u_ripple;
uniform float u_quality;
uniform vec2 u_parallax;
// Optional extras (0 when unset keeps the contract behaviour):
uniform float u_flow;        // integrated spin-time (seconds * spin); 0 = use u_time * u_spin
uniform float u_hue;         // extra hue rotation in radians (HUD skins)
uniform float u_transparent; // 1 = fade the frame edge to transparent (overlay on a page with its own sky)
// Smart reactions (Art Update stage 6; 0 when unset, so the greeter and boot frames are unchanged):
uniform float u_flare;       // 0..1 the disk flares (a file fed to the hole, an approval falling in)
uniform float u_ring2;       // 0..1 soft second ring (an update is ready)
uniform vec4 u_bodies[10];   // small bodies near the hole: (x, y) in the disk plane, z brightness (0 = off),
                             // w = colour (0 starlight, 1 ember, 2 photon, 3 violet) + tail length in the fraction

out vec4 fragColor;

const float PI = 3.14159265;
const float TAU = 6.2831853;

// Geometry, in units of the frame's shorter side (0.5 = the edge).
const float RS = 0.082;     // shadow (event horizon) radius
const float RIN = 0.092;    // inner edge of the disk
const float ROUT = 0.40;    // outer edge of the painting's structure
const float TEX_IN = 0.088; // painting radius at the inner edge (texture uv units)
const float TEX_OUT = 0.37; // painting radius where the paint ends
const float EINSTEIN = 0.112;
const float FLOW_T = 5.0;   // seconds per flow phase

const vec3 VIOLET = vec3(0.18, 0.42, 1.0);     // Accent, now cobalt #2E6BFF (look pass; name kept)
const vec3 PHOTON = vec3(0.0, 0.898, 1.0);     // Photon #00E5FF
const vec3 STAR = vec3(0.9, 0.925, 0.96);      // Starlight #E6ECF5
const vec3 EMBER = vec3(1.0, 0.776, 0.42);     // Ember #FFC66B

// ------------------------------------------------------------------ noise
float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
vec2 hash22(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.xx + p3.yz) * p3.zy);
}
float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x), mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p, int oct) {
  float s = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) {
    if (i >= oct) break;
    s += a * vnoise(p);
    p = mat2(1.6, 1.2, -1.2, 1.6) * p;
    a *= 0.5;
  }
  return s;
}

vec3 hueRotate(vec3 c, float a) {
  if (abs(a) < 1e-3) return c;
  const vec3 k = vec3(0.57735);
  float ca = cos(a);
  return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);
}

// ------------------------------------------------------------------ stars (three depths)
vec3 starLayer(vec2 q, float scale, float density, float px, float t, float seed) {
  vec2 g = q * scale;
  vec2 id = floor(g);
  vec3 acc = vec3(0.0);
  // 2x2 neighbourhood so soft stars near a cell edge are not clipped
  vec2 base = floor(g - 0.5);
  for (int j = 0; j < 2; j++) {
    for (int i = 0; i < 2; i++) {
      vec2 c = base + vec2(float(i), float(j));
      float h = hash12(c + seed);
      if (h > density) continue;
      vec2 pos = c + 0.15 + 0.7 * hash22(c + seed * 1.7);
      float d = length(g - pos) / scale / px; // distance in pixels
      float b = hash12(c + seed + 7.1);
      float mag = pow(b, 4.0) * 1.6 + 0.12;
      float tw = 0.65 + 0.35 * sin(t * (0.6 + 2.2 * hash12(c + 3.3)) + h * 60.0);
      float r = 0.75 + 1.1 * b;
      float core = exp(-d * d / (r * r));
      float halo = exp(-d / (2.6 * r)) * 0.06 * b;
      vec3 tint = mix(vec3(1.0, 0.86, 0.72), vec3(0.72, 0.8, 1.0), hash12(c + 9.2));
      acc += tint * (core + halo) * mag * tw;
    }
  }
  return acc;
}

vec3 starfield(vec2 q, float px, float t, float q_level) {
  vec3 s = vec3(0.0);
  s += starLayer(q + u_parallax * 0.004, 26.0, 0.07, px, t, 1.0) * 1.0;   // near: few, bright
  s += starLayer(q + u_parallax * 0.010, 60.0, 0.06, px, t * 1.3, 17.0) * 0.55;
  if (q_level >= 1.5 || q_level < 0.5) s += starLayer(q + u_parallax * 0.018, 130.0, 0.07, px, t * 1.7, 41.0) * 0.3;
  // faint violet haze so the sky is not dead black
  float neb = fbm(q * 3.0 + 11.0, 3);
  s += VIOLET * pow(neb, 3.0) * 0.05;
  return s;
}

// ------------------------------------------------------------------ the painted disk
// Sample the painting at disk-plane radius rd (screen units) and angle ang, flowing with flow-time f.
// Returns premultiplied rgb in .rgb and coverage in .a.
vec4 paintAt(float rd, float ang, float f, float turb, int oct) {
  float x = clamp((rd - RIN) / (ROUT - RIN), 0.0, 1.4);
  // Keplerian angular velocity relative to the inner edge, normalised (omega ~ r^-1.5)
  float omega = 0.40 * pow(max(rd, RIN) / RIN, -1.5);
  float omegaRef = 0.40 * pow(0.14 / RIN, -1.5); // rigid part: the whole pattern turns at the inner-mid rate
  float rigid = f * omegaRef;
  vec4 acc = vec4(0.0);
  for (int k = 0; k < 2; k++) {
    float ph = fract(f / FLOW_T + 0.5 * float(k) + rd * 2.3); // resets ripple outward, never all at once
    float tp = (ph - 0.5) * FLOW_T;
    float w = 1.0 - abs(2.0 * ph - 1.0);
    // differential winding (reset every phase by the cross-fade) + slow inward drift
    float a = ang - rigid - (omega - omegaRef) * tp;
    float rt = mix(TEX_IN, TEX_OUT, pow(x, 0.92)) * (1.0 + 0.018 * tp);
    vec2 dir = vec2(cos(a), sin(a));
    if (oct > 0) {
      // turbulent eddies advected with the flow
      vec2 np = dir * rd * 14.0 + float(k) * 19.7;
      vec2 wob = vec2(fbm(np, oct), fbm(np + 5.2, oct)) - 0.5;
      float amp = 0.006 + 0.03 * turb;
      rt += wob.x * amp;
      a += wob.y * amp * 6.0;
      dir = vec2(cos(a), sin(a));
    }
    vec4 tex = texture(u_disk, 0.5 + rt * dir);
    acc += tex * w;
  }
  return acc;
}

vec4 diskColor(float rd, float ang, float f, float t, float turb, int oct, float reveal) {
  if (rd < RIN * 0.92 || rd > ROUT * 1.25) return vec4(0.0);
  vec4 tex = paintAt(rd, ang, f, turb, oct);
  vec3 col = tex.rgb; // texture is uploaded premultiplied
  float x = (rd - RIN) / (ROUT - RIN);
  // the paint dissolves into space: ragged noisy rim, cooled toward cobalt
  // (noise in Cartesian disk coordinates so there is no seam at the angle wrap; turns slowly with the outer disk)
  float ra = ang - f * 0.03;
  vec2 dq = vec2(cos(ra), sin(ra)) * rd;
  float n = fbm(dq * 9.0 + 3.0, 3);
  float edge = smoothstep(1.02, 0.22, x + (n - 0.5) * 0.55);
  edge *= edge;
  float lum = dot(col, vec3(0.3, 0.5, 0.2));
  vec3 cool = lum * vec3(0.42, 0.66, 1.25) * 1.25;
  col = mix(col, cool, smoothstep(0.35, 0.85, x));
  // lift the painting: contrast + saturation so it glows against the void
  col = pow(col * 1.7, vec3(1.12)) * 1.25;
  // inner edge heats up (blue-white), soft inner rim fade so the disk meets the photon ring cleanly
  float heat = exp(-max(rd - RIN, 0.0) / 0.022);
  col += vec3(0.66, 0.85, 1.0) * heat * 0.55 * (0.6 + 0.4 * lum * 4.0);
  float inner = smoothstep(RIN * 0.93, RIN * 1.06, rd);
  // ripples: Orion speaking sends waves outward through the disk
  float rip = u_ripple * (0.5 + 0.5 * sin(rd * 90.0 - t * 7.0)) * smoothstep(0.0, 0.2, x) * (1.0 - x);
  col *= 1.0 + rip * 0.9;
  // flare: the disk brightens from the inside out as it swallows something
  col *= 1.0 + clamp(u_flare, 0.0, 1.0) * 0.85 * (1.0 - 0.6 * clamp(x, 0.0, 1.0));
  // boot: paint in from the outside
  float rv = smoothstep(reveal - 0.18, reveal + 0.02, x);
  float front = reveal > -0.1 ? exp(-pow((x - reveal + 0.04) / 0.07, 2.0)) * 0.6 : 0.0;
  col += vec3(0.62, 0.8, 1.0) * front * (0.3 + lum * 3.0);
  float a = edge * inner * rv;
  return vec4(col * a, a);
}

void main() {
  float size = u_size > 0.0 ? u_size : 1.0;
  float minRes = min(u_resolution.x, u_resolution.y);
  vec2 p = (gl_FragCoord.xy - 0.5 * u_resolution) / minRes / size;
  float px = 1.0 / minRes / size;
  float t = u_time;
  float qual = u_quality;
  int oct = qual < 0.5 ? 3 : (qual < 1.5 ? 0 : (qual < 2.5 ? 2 : 4));

  // ---------------------------------------------------------- boot timeline
  float bt = u_boot < 0.0 ? 100.0 : u_boot;
  float starsIn = smoothstep(0.6, 2.8, bt);                       // sky appears with the starfall
  float sparkA = smoothstep(0.0, 0.8, bt) * (1.0 - smoothstep(2.55, 2.85, bt));
  float fallA = smoothstep(0.8, 1.3, bt) * (1.0 - smoothstep(2.4, 2.9, bt));
  float shadowK = smoothstep(2.55, 2.9, bt);                      // horizon opens
  float flash = bt < 2.6 ? 0.0 : exp(-(bt - 2.72) * (bt - 2.72) / 0.012) + exp(-max(bt - 2.72, 0.0) * 3.5) * 0.35 * step(2.72, bt);
  float ringK = smoothstep(2.62, 2.78, bt) * (1.0 + 1.6 * exp(-max(bt - 2.75, 0.0) * 2.2));
  float accr = smoothstep(3.3, 4.9, bt);                          // disk paints in
  float reveal = 1.05 - 1.25 * accr;                              // reveal front moves outside -> inside
  float spinIn = (1.0 - accr) * (1.0 - accr) * 2.6;               // extra swirl that settles
  float glowIn = smoothstep(3.0, 6.0, bt);

  float rs = RS * shadowK;
  float f = (u_flow != 0.0 ? u_flow : u_time * u_spin);

  // ---------------------------------------------------------- polar + tilt
  float b = length(p);
  float phi = atan(p.y, p.x);
  float tilt = clamp(u_tilt, 0.0, 1.0);
  float inc = tilt * 1.42;
  float ci = max(cos(inc), 0.07);

  // ---------------------------------------------------------- background: lensed starfield
  vec3 col = vec3(0.0);
  vec3 sky = vec3(0.0);   // the sky is kept ungraded: moods tint the matter, never the stars
  float skyW = 1.0;       // how much of the sky still shows through
  float alpha = 0.0;
  if (b > rs * 0.98 || shadowK < 0.01) {
    float th2 = EINSTEIN * EINSTEIN * shadowK;
    vec2 src = p - th2 * p / max(b * b, 1e-4);
    float mag = clamp(1.0 / max(abs(1.0 - (th2 * th2) / max(b * b * b * b, 1e-6)), 0.25), 1.0, 3.0);
    sky = starfield(src, px, t, qual) * starsIn;
    // faint Einstein ring where the sky piles up at the shadow's edge
    float er = exp(-pow((b - EINSTEIN * 1.02) / 0.010, 2.0)) * 0.10 * shadowK;
    sky += STAR * er * (0.8 + 0.2 * sin(phi * 3.0 + t * 0.2));
    sky *= min(mag, 2.0);
    // overlay: the page has its own sky, so only the lensed part near the hole is ours
    if (u_transparent > 0.5) sky *= smoothstep(EINSTEIN * 3.2, EINSTEIN * 1.4, b);
  }

  // ---------------------------------------------------------- boot: spark and spiral starfall
  if (sparkA > 0.0) {
    float sp = exp(-b / 0.006) * 1.6 + exp(-b / 0.035) * 0.35 + exp(-b / 0.12) * 0.08;
    col += mix(VIOLET, vec3(0.85, 0.93, 1.0), exp(-b / 0.01)) * sp * sparkA * (1.0 + 0.6 * smoothstep(1.5, 2.55, bt));
  }
  if (fallA > 0.0) {
    float tt = max(bt - 0.8, 0.0);
    float travel = 0.25 * tt + 0.38 * tt * tt; // faster and faster
    float lr = log(max(b, 1e-3));
    float sArm = phi + 1.35 * lr;              // spiral coordinate
    const float N = 48.0;
    vec2 g = vec2((lr + travel) * 7.0, sArm / TAU * N);
    vec2 id = floor(g);
    id.y = mod(id.y, N);
    vec2 fr = fract(g);
    float h = hash12(id + 3.0);
    float on = step(0.55, h);
    float y0 = 0.2 + 0.6 * hash12(id + 8.0);
    float len = 0.25 + 0.6 * smoothstep(0.0, 1.8, tt);
    float along = smoothstep(0.0, len, fr.x) * (1.0 - smoothstep(len, len + 0.08, fr.x));
    float streak = exp(-pow((fr.y - y0) / (0.05 + 0.02 * tt), 2.0)) * along * on;
    float near = smoothstep(0.012, 0.06, b) * smoothstep(0.75, 0.25, b);
    col += mix(STAR, VIOLET * 1.4, hash12(id + 1.0)) * streak * near * fallA * 1.4;
  }

  // ---------------------------------------------------------- shadow
  float shadow = 1.0 - smoothstep(rs * 0.985, rs * 1.0 + px * 1.5, b);
  float absorb = (1.0 - shadow) * mix(1.0, smoothstep(rs * 0.98, rs * 1.35, b), shadowK * 0.6);
  col *= absorb; // light falls off toward the horizon (soft absorption just outside it)
  skyW *= absorb;
  alpha = max(alpha, shadow * shadowK);

  // ---------------------------------------------------------- the disk (primary image + lensed arc)
  float turb = clamp(u_turbulence, 0.0, 1.0);
  if (accr > 0.0) {
    float ang = phi - spinIn;
    // primary image: the disk plane seen at inclination
    vec2 dp = vec2(p.x, p.y / ci);
    float rd = length(dp);
    float angP = atan(dp.y, dp.x) - spinIn;
    vec4 d1 = diskColor(rd, angP, f, t, turb, oct, reveal);
    // far side passes behind the hole: hidden inside the shadow
    float behind = (b < rs && dp.y > 0.0) ? 1.0 : 0.0;
    d1 *= 1.0 - behind;
    // Doppler beaming: the side turning toward you is brighter (stronger when tilted)
    float beam = 1.0 + (0.28 + 0.5 * tilt) * -cos(angP + spinIn) * exp(-max(rd - RIN, 0.0) / 0.10);
    d1.rgb *= beam;

    vec4 d2 = vec4(0.0);
    if (tilt > 0.02 && b > rs) {
      // lensed image of the far side, bent up over (and faintly under) the shadow
      float rdA = RIN + (b - rs * 1.03) * 4.2;
      float top = smoothstep(-0.25, 0.6, sin(phi));
      float bottom = smoothstep(0.2, -0.7, sin(phi)) * 0.35;
      float angA = phi - spinIn;
      d2 = diskColor(rdA, angA, f, t, turb, max(oct - 2, 0), reveal);
      float w = smoothstep(0.02, 0.6, tilt) * (top + bottom) * smoothstep(rs * 1.02, rs * 1.12, b);
      d2 *= w * (1.0 - 0.6 * d1.a);
      d2.rgb *= (1.0 + 0.5 * -cos(phi)) * 1.5;
    }
    // composite: arc behind, primary over
    float bright = (0.4 + 0.6 * accr) * mix(0.55, 1.0, clamp(u_glow, 0.0, 1.0));
    col = col * (1.0 - d2.a * 0.9) + d2.rgb * bright;
    skyW *= 1.0 - d2.a * 0.9;
    float ga = clamp(d1.a, 0.0, 1.0);
    col = col * (1.0 - ga * 0.92) + d1.rgb * bright;
    skyW *= 1.0 - ga * 0.92;
    alpha = max(alpha, ga);
  }

  // ---------------------------------------------------------- photon ring(s)
  float ringI = max(u_ring, 0.0) * (1.0 + 1.4 * clamp(u_voice, 0.0, 1.0)) * ringK;
  if (ringI > 0.0) {
    float rr = rs * 1.012;
    float w = 0.0022 + px * 0.9;
    float dop = 0.22 + 1.9 * pow(0.5 + 0.5 * -cos(phi), 2.0);           // brighter on the approaching (left) side
    float ring = exp(-pow((b - rr) / w, 2.0));
    float halo = exp(-pow((b - rr) / (w * 5.0), 2.0)) * 0.16;
    vec3 rc = mix(vec3(0.86, 0.9, 1.0), PHOTON, 0.22 + 0.25 * (0.5 + 0.5 * cos(phi)));
    vec3 rcol = rc * (ring * 0.8 + halo) * dop;
    if (qual > 2.5 || qual < 0.5 || qual > 1.5) {
      float r2 = exp(-pow((b - rs * 1.075) / (w * 0.9), 2.0)) * 0.22; // faint secondary ring
      rcol += mix(VIOLET, PHOTON, 0.5) * r2 * (1.0 + 0.4 * -cos(phi));
    }
    col += rcol * ringI;
    alpha = max(alpha, clamp(max(ring, halo) * ringI, 0.0, 1.0));
  }
  // soft second ring: an update is ready (breathes very slowly)
  float ring2 = clamp(u_ring2, 0.0, 1.0) * ringK;
  if (ring2 > 0.001) {
    float r2r = RS * 1.85;
    float r2w = 0.004 + px;
    float h2 = exp(-pow((b - r2r) / r2w, 2.0)) * 0.5 + exp(-pow((b - r2r) / (r2w * 4.5), 2.0)) * 0.12;
    h2 *= ring2 * (0.85 + 0.15 * sin(t * 0.8));
    col += mix(PHOTON, STAR, 0.45) * h2;
    alpha = max(alpha, clamp(h2, 0.0, 1.0));
  }

  // ---------------------------------------------------------- glow + collapse flash
  float g = max(u_glow, 0.0);
  g *= 1.0 + 1.1 * clamp(u_flare, 0.0, 1.0);
  vec3 bloom = VIOLET * (exp(-max(b - RS, 0.0) / 0.16) * 0.10 + exp(-max(b - RS, 0.0) / 0.035) * 0.08) * g * glowIn;
  bloom += vec3(0.25, 0.5, 0.75) * exp(-max(b - RS, 0.0) / 0.012) * 0.10 * g * glowIn * (1.0 + u_voice);
  col += bloom * (1.0 - shadow);
  col += mix(PHOTON, vec3(1.0), 0.6) * flash * (exp(-max(b - RS, 0.0) / 0.03) * 1.2 + exp(-b / 0.14) * 0.22) * (1.0 - shadow * 0.7);

  // ---------------------------------------------------------- grade: temperature, skin hue
  float temp = clamp(u_temp, -1.0, 1.0);
  float hue = u_hue + (temp > 0.0 ? temp * 2.4 : temp * 0.3);
  vec3 graded = hueRotate(col, hue);
  if (temp > 0.0) graded = mix(graded, graded * vec3(1.12, 0.95, 0.78), temp * 0.5);
  else graded = mix(graded, graded * vec3(0.85, 0.9, 1.15), -temp * 0.6);
  col = max(graded, 0.0) + sky * skyW;

  // ---------------------------------------------------------- small bodies: messages falling in, approvals orbiting
  // Ungraded like the sky (an ember comet stays ember in every mood). Seen at the same tilt as the disk.
  for (int i = 0; i < 10; i++) {
    vec4 bd = u_bodies[i];
    if (bd.z <= 0.001) continue;
    float code = floor(bd.w + 1e-3);
    float tail = clamp(bd.w - code, 0.0, 0.99);
    vec2 bp = vec2(bd.x, bd.y * ci);
    vec2 q = p - bp;
    if (dot(q, q) > (0.05 + tail * 0.12) * (0.05 + tail * 0.12)) continue;
    float hide = (bd.y > 0.0 && tilt > 0.02) ? smoothstep(rs * 0.95, rs * 1.2, length(bp)) : smoothstep(rs * 0.6, rs * 1.05, length(bp));
    vec3 tint = code < 0.5 ? STAR : (code < 1.5 ? EMBER : (code < 2.5 ? PHOTON : VIOLET * 1.35));
    float dpx = length(q) / px;
    float rad = 1.3 + 1.8 * clamp(bd.z, 0.0, 1.0);
    float glowB = exp(-dpx * dpx / (rad * rad)) * 1.5 + exp(-dpx / (rad * 3.0)) * 0.3;
    // a faint four-point glint so a body reads as "something", not one more background star
    vec2 aq = abs(q) / px;
    glowB += (exp(-aq.y * aq.y / 0.8) * exp(-aq.x / (rad * 2.2)) + exp(-aq.x * aq.x / 0.8) * exp(-aq.y / (rad * 2.2))) * 0.35;
    if (tail > 0.01) {
      // the tail trails behind (bodies move counter-clockwise and inward)
      vec2 rdir = normalize(bp + vec2(1e-5));
      vec2 back = normalize(vec2(rdir.y, -rdir.x) * 0.85 + rdir * 0.5);
      float along = dot(q, back);
      float across = dot(q, vec2(-back.y, back.x)) / px;
      float L = tail * 0.1;
      float u = along / L;
      float wpx = 0.8 + 2.2 * u;
      if (u > 0.0 && u < 1.0) glowB += exp(-across * across / (wpx * wpx)) * (1.0 - u) * (1.0 - u) * 0.6;
    }
    col += tint * glowB * bd.z * hide;
  }

  // gentle filmic shoulder so bright parts never clip harshly
  col = col / (1.0 + col * 0.35);
  col = pow(col, vec3(0.96));

  // ---------------------------------------------------------- output
  if (u_transparent > 0.5) {
    // overlay mode: occlude the page's own sky near the hole, fade to nothing at the frame edge
    float edgeR = 0.5 / size;
    float occl = smoothstep(EINSTEIN * 3.2, EINSTEIN * 1.4, b) * 0.85 * starsIn;
    float fade = smoothstep(edgeR, edgeR * 0.7, b);
    col *= fade;
    float a = clamp(max(max(alpha, occl), max(col.r, max(col.g, col.b))), 0.0, 1.0) * max(fade, shadow);
    fragColor = vec4(min(col, vec3(a)), a);
  } else {
    fragColor = vec4(col, 1.0);
  }
}
