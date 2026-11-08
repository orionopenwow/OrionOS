/**
 * Small rotating 3D Earth (three.js) for the context-window button: procedural continents, barely-visible
 * bump-mapped topography, a soft fresnel atmosphere instead of a ring. Renders ~30 fps and pauses when hidden.
 */
import { useEffect, useRef } from 'react';
import * as THREE from 'three';

// --------------------------------------------------------------- procedural planet texture
function hash(x: number, y: number, seed: number) {
  const s = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
  return s - Math.floor(s);
}
function valueNoise(x: number, y: number, seed: number) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash(xi, yi, seed), b = hash(xi + 1, yi, seed), c = hash(xi, yi + 1, seed), d = hash(xi + 1, yi + 1, seed);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
function fbm(x: number, y: number, seed: number) {
  let total = 0, amp = 0.5, freq = 1;
  for (let o = 0; o < 5; o++) {
    total += amp * valueNoise(x * freq, y * freq, seed + o * 13);
    freq *= 2.03;
    amp *= 0.5;
  }
  return total;
}

function makeTextures(): { map: THREE.CanvasTexture; bump: THREE.CanvasTexture } {
  const W = 512, H = 256;
  const color = document.createElement('canvas');
  const bump = document.createElement('canvas');
  color.width = bump.width = W;
  color.height = bump.height = H;
  const cctx = color.getContext('2d')!;
  const bctx = bump.getContext('2d')!;
  const cimg = cctx.createImageData(W, H);
  const bimg = bctx.createImageData(W, H);
  for (let y = 0; y < H; y++) {
    const lat = (y / H) * Math.PI;
    for (let x = 0; x < W; x++) {
      const lon = (x / W) * Math.PI * 2;
      // sample noise on the sphere surface so the texture wraps seamlessly
      const sx = Math.sin(lat) * Math.cos(lon), sy = Math.sin(lat) * Math.sin(lon), sz = Math.cos(lat);
      const h = fbm(sx * 1.6 + 3, sy * 1.6 + sz * 1.3 + 5, 7);
      const ridge = fbm(sx * 5 + 11, sy * 5 + sz * 4, 3);
      const land = h > 0.52;
      const polar = Math.abs(Math.cos(lat)) > 0.9;
      const i = (y * W + x) * 4;
      let r: number, g: number, b: number;
      if (polar) {
        [r, g, b] = [200, 214, 230];
      } else if (land) {
        const t = Math.min(1, (h - 0.52) * 4);
        r = 34 + t * 40 + ridge * 18;
        g = 82 + t * 34 + ridge * 16;
        b = 74 + t * 10;
      } else {
        const depth = Math.min(1, (0.52 - h) * 3);
        r = 10 + (1 - depth) * 18;
        g = 38 + (1 - depth) * 40;
        b = 78 + (1 - depth) * 52;
      }
      cimg.data[i] = r; cimg.data[i + 1] = g; cimg.data[i + 2] = b; cimg.data[i + 3] = 255;
      const elev = land ? 120 + (h - 0.52) * 260 + ridge * 40 : 110;
      bimg.data[i] = bimg.data[i + 1] = bimg.data[i + 2] = Math.min(255, elev);
      bimg.data[i + 3] = 255;
    }
  }
  cctx.putImageData(cimg, 0, 0);
  bctx.putImageData(bimg, 0, 0);
  const map = new THREE.CanvasTexture(color);
  map.colorSpace = THREE.SRGBColorSpace;
  return { map, bump: new THREE.CanvasTexture(bump) };
}

const ATMOSPHERE_VERTEX = `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }`;
const ATMOSPHERE_FRAGMENT = `
  varying vec3 vNormal;
  varying vec3 vView;
  uniform vec3 glow;
  uniform float strength;
  void main() {
    float rim = pow(1.0 - max(dot(vNormal, vView), 0.0), 3.2);
    gl_FragColor = vec4(glow, rim * strength);
  }`;

interface Props {
  size?: number;
  /** Brighter atmosphere while the panel is open. */
  active?: boolean;
}

export default function Globe3D({ size = 64, active = false }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef(active);
  activeRef.current = active;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch {
      return; // no WebGL: the button still works, it just shows the fallback glow
    }
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(dpr);
    renderer.setSize(size, size);
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 10);
    camera.position.set(0, 0, 3.55);

    const { map, bump } = makeTextures();
    const earth = new THREE.Mesh(
      new THREE.SphereGeometry(0.82, 64, 48),
      new THREE.MeshStandardMaterial({ map, bumpMap: bump, bumpScale: 0.6, roughness: 0.92, metalness: 0.02 }),
    );
    earth.rotation.z = THREE.MathUtils.degToRad(23.4);
    scene.add(earth);

    const atmoMat = new THREE.ShaderMaterial({
      vertexShader: ATMOSPHERE_VERTEX,
      fragmentShader: ATMOSPHERE_FRAGMENT,
      uniforms: { glow: { value: new THREE.Color('#9fdcff') }, strength: { value: 0.35 } },
      transparent: true,
      blending: THREE.AdditiveBlending,
      side: THREE.FrontSide,
      depthWrite: false,
    });
    // atmosphere: a faint haze inside the limb (FrontSide on a near-identical shell) — no halo ring outside the globe
    scene.add(new THREE.Mesh(new THREE.SphereGeometry(0.825, 48, 32), atmoMat));

    scene.add(new THREE.AmbientLight(0x9fb4d8, 0.35));
    const sun = new THREE.DirectionalLight(0xffffff, 2.1);
    sun.position.set(-2.2, 1.4, 2.4);
    scene.add(sun);

    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    let raf = 0;
    let last = 0;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      if (document.hidden || t - last < 33) return; // ~30 fps is plenty for a 64px globe
      const dt = last ? (t - last) / 1000 : 0;
      last = t;
      if (!reduce) earth.rotation.y += dt * 0.25;
      atmoMat.uniforms.strength.value += ((activeRef.current ? 0.6 : 0.35) - atmoMat.uniforms.strength.value) * 0.08;
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      renderer.dispose();
      map.dispose();
      bump.dispose();
      mount.removeChild(renderer.domElement);
    };
  }, [size]);

  return <div ref={mountRef} style={{ width: size, height: size }} className="pointer-events-none" />;
}
