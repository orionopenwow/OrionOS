#version 300 es
// Orion living black hole, "Ultra" tier: a real ray-march through Schwarzschild space-time (GLSL ES 3.00 / WebGL2).
// Same host contract and uniforms as blackhole.frag (see README.md), so moods, voice, the agent-busy spin, smart
// reactions and the boot timeline all work unchanged. Draw a full-screen triangle; no varyings needed.
//
// Each pixel fires a light ray at the hole and bends it step by step with the photon equation of motion around a
// non-spinning mass, a'' = -1.5 h^2 x / r^5 (units: event horizon radius = 1, h = |x × v| is conserved). Where the
// ray crosses the disk plane it picks up the painted disk (black-hole-core.png, still the source), Doppler-beamed
// and redshifted by the gas's orbital speed; a thin glowing haze above and below the plane is gathered on the way;
// rays that fall below the horizon are black; rays that escape look up the starfield in the bent direction, so
// the sky, the far side of the disk and the photon ring are all lensed for real.
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
uniform float u_flow;
uniform float u_hue;
uniform float u_transparent;
uniform float u_flare;
uniform float u_ring2;
uniform vec4 u_bodies[10];

out vec4 fragColor;

const float PI = 3.14159265;
const float TAU = 6.2831853;

// Screen geometry, in units of the frame's shorter side (kept identical to blackhole.frag so tiers match).
const float RS = 0.082;     // apparent shadow radius
const float RIN = 0.092;    // inner edge of the disk
const float ROUT = 0.40;    // outer edge of the painting's structure
const float TEX_IN = 0.088;
const float TEX_OUT = 0.37;
const float EINSTEIN = 0.112;
const float FLOW_T = 5.0;

// Physical units: horizon radius = 1. The shadow of a Schwarzschild hole has impact parameter 3√3/2 ≈ 2.598, so
// one screen unit is K horizon radii. The inner disk edge then sits at ~2.9: the innermost stable orbit (3).
const float BC = 2.5980762;
const float K = BC / RS;
const float Z0 = 34.0;      // where rays start (parallel rays: impact parameter = screen offset)
const float SKY_L = 0.1987; // bent direction -> sky offset; matches blackhole.frag's Einstein radius for weak bends

const vec3 VIOLET = vec3(0.18, 0.42, 1.0);  // cobalt #2E6BFF (look pass; name kept)
const vec3 PHOTON = vec3(0.0, 0.898, 1.0);
const vec3 STAR = vec3(0.9, 0.925, 0.96);
const vec3 EMBER = vec3(1.0, 0.776, 0.42);

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

// ------------------------------------------------------------------ stars (three depths), as in blackhole.frag
vec3 starLayer(vec2 q, float scale, float density, float px, float t, float seed) {
  vec2 g = q * scale;
  vec3 acc = vec3(0.0);
  vec2 base = floor(g - 0.5);
  for (int j = 0; j < 2; j++) {
    for (int i = 0; i < 2; i++) {
      vec2 c = base + vec2(float(i), float(j));
      float h = hash12(c + seed);
      if (h > density) continue;
      vec2 pos = c + 0.15 + 0.7 * hash22(c + seed * 1.7);
      float d = length(g - pos) / scale / px;
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
vec3 starfield(vec2 q, float px, float t) {
  vec3 s = vec3(0.0);
  s += starLayer(q + u_parallax * 0.004, 26.0, 0.07, px, t, 1.0);
  s += starLayer(q + u_parallax * 0.010, 60.0, 0.06, px, t * 1.3, 17.0) * 0.55;
  s += starLayer(q + u_parallax * 0.018, 130.0, 0.07, px, t * 1.7, 41.0) * 0.3;
  float neb = fbm(q * 3.0 + 11.0, 3);
  s += VIOLET * pow(neb, 3.0) * 0.05;
  return s;
}

// ------------------------------------------------------------------ the painted disk (screen-unit radius rd)
// lod: explicit mip level. Texture lookups happen inside the ray loop, where the GPU can't work out the footprint
// itself (that gave speckles), so the caller passes it.
vec4 paintAt(float rd, float ang, float f, float turb, int oct, float lod) {
  float x = clamp((rd - RIN) / (ROUT - RIN), 0.0, 1.4);
  float omega = 0.40 * pow(max(rd, RIN) / RIN, -1.5);
  float omegaRef = 0.40 * pow(0.14 / RIN, -1.5);
  float rigid = f * omegaRef;
  vec4 acc = vec4(0.0);
  for (int k = 0; k < 2; k++) {
    float ph = fract(f / FLOW_T + 0.5 * float(k) + rd * 2.3);
    float tp = (ph - 0.5) * FLOW_T;
    float w = 1.0 - abs(2.0 * ph - 1.0);
    float a = ang - rigid - (omega - omegaRef) * tp;
    float rt = mix(TEX_IN, TEX_OUT, pow(x, 0.92)) * (1.0 + 0.018 * tp);
    vec2 dir = vec2(cos(a), sin(a));
    if (oct > 0) {
      vec2 np = dir * rd * 14.0 + float(k) * 19.7;
      vec2 wob = vec2(fbm(np, oct), fbm(np + 5.2, oct)) - 0.5;
      float amp = 0.006 + 0.03 * turb;
      rt += wob.x * amp;
      a += wob.y * amp * 6.0;
      dir = vec2(cos(a), sin(a));
    }
    acc += textureLod(u_disk, 0.5 + rt * dir, lod) * w;
  }
  return acc;
}

vec4 diskColor(float rd, float ang, float f, float t, float turb, int oct, float reveal, float lod) {
  if (rd < RIN * 0.92 || rd > ROUT * 1.25) return vec4(0.0);
  vec4 tex = paintAt(rd, ang, f, turb, oct, lod);
  vec3 col = tex.rgb;
  float x = (rd - RIN) / (ROUT - RIN);
  float ra = ang - f * 0.03;
  vec2 dq = vec2(cos(ra), sin(ra)) * rd;
  float n = fbm(dq * 9.0 + 3.0, 3);
  float edge = smoothstep(1.02, 0.22, x + (n - 0.5) * 0.55);
  edge *= edge;
  float lum = dot(col, vec3(0.3, 0.5, 0.2));
  vec3 cool = lum * vec3(0.42, 0.66, 1.25) * 1.25;
  col = mix(col, cool, smoothstep(0.35, 0.85, x));
  col = pow(col * 1.7, vec3(1.12)) * 1.25;
  float heat = exp(-max(rd - RIN, 0.0) / 0.022);
  col += vec3(0.66, 0.85, 1.0) * heat * 0.55 * (0.6 + 0.4 * lum * 4.0);
  float inner = smoothstep(RIN * 0.93, RIN * 1.06, rd);
  float rip = u_ripple * (0.5 + 0.5 * sin(rd * 90.0 - t * 7.0)) * smoothstep(0.0, 0.2, x) * (1.0 - x);
  col *= 1.0 + rip * 0.9;
  col *= 1.0 + clamp(u_flare, 0.0, 1.0) * 0.85 * (1.0 - 0.6 * clamp(x, 0.0, 1.0));
  float rv = smoothstep(reveal - 0.18, reveal + 0.02, x);
  float front = reveal > -0.1 ? exp(-pow((x - reveal + 0.04) / 0.07, 2.0)) * 0.6 : 0.0;
  col += vec3(0.62, 0.8, 1.0) * front * (0.3 + lum * 3.0);
  float a = edge * inner * rv;
  return vec4(col * a, a);
}

// Relativistic brightness of disk gas at radius r (horizon units) seen along backward ray direction v.
// Returns (relative beaming, frequency shift g) where beaming is normalised to the face-on value so the painting
// keeps its brightness and only the one-sided brightening and the colour shift come from the physics.
vec2 doppler(vec3 hit, vec3 v, vec3 N, float mass) {
  float r = max(length(hit), 1.05);
  float beta = min(sqrt(0.5 * mass / r), 0.62);          // Keplerian speed, c = 1
  float gam = inversesqrt(1.0 - beta * beta);
  vec3 vhat = -normalize(cross(N, hit) + vec3(1e-6));     // orbit sense: the left side comes toward you
  vec3 emit = -normalize(v);                              // the real photon travels back along the ray
  float delta = 1.0 / (gam * (1.0 - beta * dot(vhat, emit)));
  float grav = sqrt(max(1.0 - mass / r, 0.05));
  float g = delta * grav;
  float g0 = grav / gam;                                  // what a face-on viewer sees at this radius
  return vec2(clamp(pow(g / g0, 3.0), 0.12, 3.5), g);
}

void main() {
  float size = u_size > 0.0 ? u_size : 1.0;
  float minRes = min(u_resolution.x, u_resolution.y);
  vec2 p = (gl_FragCoord.xy - 0.5 * u_resolution) / minRes / size;
  float px = 1.0 / minRes / size;
  float t = u_time;
  int oct = u_quality > 4.5 ? 4 : 3;
  int maxSteps = u_quality > 4.5 ? 260 : 170;

  // ---------------------------------------------------------- boot timeline (same beats as blackhole.frag)
  float bt = u_boot < 0.0 ? 100.0 : u_boot;
  float starsIn = smoothstep(0.6, 2.8, bt);
  float sparkA = smoothstep(0.0, 0.8, bt) * (1.0 - smoothstep(2.55, 2.85, bt));
  float fallA = smoothstep(0.8, 1.3, bt) * (1.0 - smoothstep(2.4, 2.9, bt));
  float shadowK = smoothstep(2.55, 2.9, bt);              // the mass switches on: the horizon opens
  float flash = bt < 2.6 ? 0.0 : exp(-(bt - 2.72) * (bt - 2.72) / 0.012) + exp(-max(bt - 2.72, 0.0) * 3.5) * 0.35 * step(2.72, bt);
  float ringK = smoothstep(2.62, 2.78, bt) * (1.0 + 1.6 * exp(-max(bt - 2.75, 0.0) * 2.2));
  float accr = smoothstep(3.3, 4.9, bt);
  float reveal = 1.05 - 1.25 * accr;
  float spinIn = (1.0 - accr) * (1.0 - accr) * 2.6;
  float glowIn = smoothstep(3.0, 6.0, bt);
  float f = (u_flow != 0.0 ? u_flow : u_time * u_spin);
  float turb = clamp(u_turbulence, 0.0, 1.0);

  float b = length(p);
  float phi = atan(p.y, p.x);
  float tilt = clamp(u_tilt, 0.0, 1.0);
  float inc = tilt * 1.42;
  float mass = shadowK;                                   // horizon radius in our units (1 when formed)
  float rsScreen = RS * shadowK;

  // disk plane: normal N, in-plane basis e1/e2 (face-on: e1 = screen x, e2 = screen y; tilting sends the top away)
  vec3 N = vec3(0.0, sin(inc), -cos(inc));
  vec3 e1 = vec3(1.0, 0.0, 0.0);
  vec3 e2 = vec3(0.0, cos(inc), sin(inc));

  // ---------------------------------------------------------- the ray march
  vec3 pos = vec3(p * K, -Z0);
  vec3 vel = vec3(0.0, 0.0, 1.0);
  float h2 = dot(cross(pos, vel), cross(pos, vel));
  vec3 disk = vec3(0.0);   // gathered disk light (premultiplied), front to back
  float diskA = 0.0;
  vec3 haze = vec3(0.0);
  bool captured = false;
  bool escaped = false;
  float bright = (0.4 + 0.6 * accr) * mix(0.55, 1.0, clamp(u_glow, 0.0, 1.0));
  int crossings = 0;
  // one screen pixel in painting texels (the painting maps ~0.9 uv per screen unit), stretched by the tilt
  float texPx = px * 0.9 * float(textureSize(u_disk, 0).x) / max(cos(inc), 0.25);
  float baseLod = log2(max(texPx, 1.0));
  for (int i = 0; i < 260; i++) {
    if (i >= maxSteps) break;
    float r = length(pos);
    if (mass > 0.01 && r < mass * 1.002) { captured = true; break; }
    if (r > Z0 * 1.25 && dot(pos, vel) > 0.0) { escaped = true; break; }
    if (diskA > 0.985) break;
    // step: fine near the photon sphere (r = 1.5), coarse far away
    float dt = clamp(0.085 * r * (0.3 + 0.7 * smoothstep(1.5, 5.0, r)), 0.015, 2.4);
    vec3 prev = pos;
    vec3 acc = mass > 0.01 ? -1.5 * mass * h2 * pos / pow(r, 5.0) : vec3(0.0);
    vel += acc * (0.5 * dt);
    pos += vel * dt;
    float r1 = length(pos);
    vec3 acc1 = mass > 0.01 ? -1.5 * mass * h2 * pos / pow(max(r1, 0.2), 5.0) : vec3(0.0);
    vel += acc1 * (0.5 * dt);

    // crossing the disk plane: the painted, Doppler-beamed disk (later crossings are the lensed images)
    float s0 = dot(prev, N);
    float s1 = dot(pos, N);
    if (accr > 0.0 && s0 * s1 <= 0.0 && s0 != s1) {
      vec3 hit = mix(prev, pos, s0 / (s0 - s1));
      float rd = length(hit) / K;
      if (rd > RIN * 0.9 && rd < ROUT * 1.25) {
        float ang = atan(dot(hit, e2), dot(hit, e1)) - spinIn;
        // higher-order images are thin: fewer noise octaves are plenty
        float lod = baseLod + (crossings == 0 ? 0.0 : 1.5 + float(crossings));
        vec4 dc = diskColor(rd, ang, f, t, turb, crossings == 0 ? oct : 1, reveal, lod);
        vec2 dg = doppler(hit, vel, N, max(mass, 0.05));
        float face = 0.22 * (1.0 - tilt) * -cos(ang + spinIn) * exp(-max(rd - RIN, 0.0) / 0.10);  // calm one-sided glow face-on
        vec3 c = dc.rgb * dg.x * (1.0 + face);
        // blueshift on the approaching side, redshift on the far one (subtle, before the mood grade)
        float shift = clamp((dg.y - 1.0) * 2.0, -1.0, 1.0);
        c *= shift > 0.0 ? mix(vec3(1.0), vec3(0.82, 0.92, 1.22), shift) : mix(vec3(1.0), vec3(1.2, 0.86, 0.72), -shift);
        disk += (1.0 - diskA) * c * bright;
        diskA += (1.0 - diskA) * clamp(dc.a * 0.96, 0.0, 1.0);
      }
      crossings++;
    }
    // volumetric haze: thin flared gas above and below the plane, glowing faintly (adds depth and the photon ring)
    if (accr > 0.0) {
      float hgt = dot(pos, N);
      float rp = length(pos - N * hgt) / K;
      float H = 0.012 + 0.05 * rp;
      float prof = smoothstep(RIN * 0.95, RIN * 1.3, rp) * exp(-max(rp - RIN, 0.0) / 0.09) * accr;
      if (prof > 0.002) {
        float dens = exp(-(hgt / K) * (hgt / K) / (H * H)) * prof;
        dens *= 0.7 + 0.6 * vnoise(vec2(atan(dot(pos, e2), dot(pos, e1)) * 6.0 - f * 0.9, rp * 40.0));
        haze += (1.0 - diskA) * mix(vec3(0.42, 0.66, 1.0), vec3(0.86, 0.94, 1.0), exp(-(rp - RIN) / 0.03)) *
          dens * dt * 0.0042 * bright * (1.0 + 0.8 * u_voice);
      }
    }
  }

  // ---------------------------------------------------------- what's behind: the lensed sky, or the horizon
  vec3 col = vec3(0.0);
  vec3 sky = vec3(0.0);
  float skyW = 1.0;
  float alpha = 0.0;
  // soft horizon edge (the march itself is binary): blend across ~1.5 px at the critical impact parameter
  float bc = BC * mass;
  float edgeAA = smoothstep(-1.5, 1.5, (b * K - bc) / (px * K));
  // the captured set is always the disk of radius bc (the hole is spherical); the disk drawn in front covers it
  float shadow = mass > 0.01 ? 1.0 - edgeAA : 0.0;
  if (shadow < 1.0) {
    vec3 d = normalize(vel);
    vec2 q = d.z > 0.0 ? p + SKY_L * d.xy / max(d.z, 0.12) : vec2(37.0, 11.0) + SKY_L * d.xy / max(-d.z, 0.12);
    sky = starfield(q, px, t) * starsIn;
    if (u_transparent > 0.5) sky *= smoothstep(EINSTEIN * 3.2, EINSTEIN * 1.4, b);
  }
  skyW *= 1.0 - shadow;
  alpha = max(alpha, shadow * shadowK);

  // ---------------------------------------------------------- boot: spark and spiral starfall
  if (sparkA > 0.0) {
    float sp = exp(-b / 0.006) * 1.6 + exp(-b / 0.035) * 0.35 + exp(-b / 0.12) * 0.08;
    col += mix(VIOLET, vec3(0.85, 0.93, 1.0), exp(-b / 0.01)) * sp * sparkA * (1.0 + 0.6 * smoothstep(1.5, 2.55, bt));
  }
  if (fallA > 0.0) {
    float tt = max(bt - 0.8, 0.0);
    float travel = 0.25 * tt + 0.38 * tt * tt;
    float lr = log(max(b, 1e-3));
    float sArm = phi + 1.35 * lr;
    const float NS = 48.0;
    vec2 g = vec2((lr + travel) * 7.0, sArm / TAU * NS);
    vec2 id = floor(g);
    id.y = mod(id.y, NS);
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
  col *= 1.0 - shadow;

  // ---------------------------------------------------------- disk + haze over the sky
  col = col * (1.0 - diskA) + disk + haze * (1.0 - shadow * 0.5);
  skyW *= 1.0 - diskA * 0.95;
  alpha = max(alpha, diskA);

  // ---------------------------------------------------------- photon ring (kept as a tunable highlight so u_ring and
  // the voice still drive it; it sits on the critical curve the march finds)
  float ringI = max(u_ring, 0.0) * (1.0 + 1.4 * clamp(u_voice, 0.0, 1.0)) * ringK;
  if (ringI > 0.0) {
    float rr = rsScreen * 1.012;
    float w = 0.0018 + px * 0.9;
    float dop = 0.22 + (1.9 + 1.2 * tilt) * pow(0.5 + 0.5 * -cos(phi), 2.0);
    float ring = exp(-pow((b - rr) / w, 2.0));
    float halo = exp(-pow((b - rr) / (w * 5.0), 2.0)) * 0.14;
    vec3 rc = mix(vec3(0.86, 0.9, 1.0), PHOTON, 0.22 + 0.25 * (0.5 + 0.5 * cos(phi)));
    vec3 rcol = rc * (ring * 0.75 + halo) * dop * (tilt > 0.05 ? mix(1.0, 0.6, tilt) : 1.0);
    float r2 = exp(-pow((b - rsScreen * 1.075) / (w * 0.9), 2.0)) * 0.2;
    rcol += mix(VIOLET, PHOTON, 0.5) * r2 * (1.0 + 0.4 * -cos(phi));
    col += rcol * ringI * (1.0 - 0.85 * diskA);   // the near side of the disk passes in front of the ring
    alpha = max(alpha, clamp(max(ring, halo) * ringI, 0.0, 1.0));
  }
  float ring2 = clamp(u_ring2, 0.0, 1.0) * ringK;
  if (ring2 > 0.001) {
    float r2r = RS * 1.85;
    float r2w = 0.004 + px;
    float h2r = exp(-pow((b - r2r) / r2w, 2.0)) * 0.5 + exp(-pow((b - r2r) / (r2w * 4.5), 2.0)) * 0.12;
    h2r *= ring2 * (0.85 + 0.15 * sin(t * 0.8));
    col += mix(PHOTON, STAR, 0.45) * h2r;
    alpha = max(alpha, clamp(h2r, 0.0, 1.0));
  }

  // ---------------------------------------------------------- glow + collapse flash
  float gl = max(u_glow, 0.0) * (1.0 + 1.1 * clamp(u_flare, 0.0, 1.0));
  vec3 bloom = VIOLET * (exp(-max(b - RS, 0.0) / 0.16) * 0.10 + exp(-max(b - RS, 0.0) / 0.035) * 0.08) * gl * glowIn;
  bloom += vec3(0.25, 0.5, 0.75) * exp(-max(b - RS, 0.0) / 0.012) * 0.10 * gl * glowIn * (1.0 + u_voice);
  col += bloom * (1.0 - shadow);
  col += mix(PHOTON, vec3(1.0), 0.6) * flash * (exp(-max(b - RS, 0.0) / 0.03) * 1.2 + exp(-b / 0.14) * 0.22) * (1.0 - shadow * 0.7);

  // ---------------------------------------------------------- grade (matter only; the sky is never tinted)
  float temp = clamp(u_temp, -1.0, 1.0);
  float hue = u_hue + (temp > 0.0 ? temp * 2.4 : temp * 0.3);
  vec3 graded = hueRotate(col, hue);
  if (temp > 0.0) graded = mix(graded, graded * vec3(1.12, 0.95, 0.78), temp * 0.5);
  else graded = mix(graded, graded * vec3(0.85, 0.9, 1.15), -temp * 0.6);
  col = max(graded, 0.0) + sky * skyW;

  // ---------------------------------------------------------- small bodies (comets, approval stars), as blackhole.frag
  float ci = max(cos(inc), 0.07);
  for (int i = 0; i < 10; i++) {
    vec4 bd = u_bodies[i];
    if (bd.z <= 0.001) continue;
    float code = floor(bd.w + 1e-3);
    float tail = clamp(bd.w - code, 0.0, 0.99);
    vec2 bp = vec2(bd.x, bd.y * ci);
    vec2 q = p - bp;
    if (dot(q, q) > (0.05 + tail * 0.12) * (0.05 + tail * 0.12)) continue;
    float hide = (bd.y > 0.0 && tilt > 0.02) ? smoothstep(rsScreen * 0.95, rsScreen * 1.2, length(bp)) : smoothstep(rsScreen * 0.6, rsScreen * 1.05, length(bp));
    vec3 tint = code < 0.5 ? STAR : (code < 1.5 ? EMBER : (code < 2.5 ? PHOTON : VIOLET * 1.35));
    float dpx = length(q) / px;
    float rad = 1.3 + 1.8 * clamp(bd.z, 0.0, 1.0);
    float glowB = exp(-dpx * dpx / (rad * rad)) * 1.5 + exp(-dpx / (rad * 3.0)) * 0.3;
    vec2 aq = abs(q) / px;
    glowB += (exp(-aq.y * aq.y / 0.8) * exp(-aq.x / (rad * 2.2)) + exp(-aq.x * aq.x / 0.8) * exp(-aq.y / (rad * 2.2))) * 0.35;
    if (tail > 0.01) {
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

  col = col / (1.0 + col * 0.35);
  col = pow(col, vec3(0.96));

  if (u_transparent > 0.5) {
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
