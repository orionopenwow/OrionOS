import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { CoreState } from '@orion/shared-types';
import { colors } from '../theme/colors';
import { readLevel } from '../hooks/useMicAnalyser';

interface Props {
  state: CoreState;
  analyser?: AnalyserNode | null;
  size?: number;
}

export default function CosmicCore3D({ state, analyser, size = 420 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelRef = useRef<THREE.Group | null>(null);
  const mixerRef = useRef<THREE.AnimationMixer | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const analyserRef = useRef(analyser);
  analyserRef.current = analyser;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // 1. Scene, Camera, Renderer
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    camera.position.set(0, 0, 4.8);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    renderer.setSize(size, size);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.35;
    container.replaceChildren(renderer.domElement);

    // 2. Lighting - Cosmic Glow
    const ambientLight = new THREE.AmbientLight(0xffffff, 1.2);
    scene.add(ambientLight);

    const cyanLight = new THREE.PointLight(0x00e5ff, 5.0, 15);
    cyanLight.position.set(-3, 2, 3);
    scene.add(cyanLight);

    const purpleLight = new THREE.PointLight(0x9d8ec7, 6.0, 15);
    purpleLight.position.set(3, -2, 3);
    scene.add(purpleLight);

    const centerGlow = new THREE.PointLight(0x7b5cff, 3.0, 10);
    centerGlow.position.set(0, 0, 1);
    scene.add(centerGlow);

    // 3. Load Orion1.glb
    const loader = new GLTFLoader();
    let isDisposed = false;

    loader.load(
      '/Orion1.glb',
      (gltf) => {
        if (isDisposed) return;
        const model = gltf.scene;

        // Auto-center and normalize model bounds
        const box = new THREE.Box3().setFromObject(model);
        const center = box.getCenter(new THREE.Vector3());
        const boxSize = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(boxSize.x, boxSize.y, boxSize.z);
        const scale = 2.4 / (maxDim || 1);
        model.scale.setScalar(scale);
        model.position.sub(center.multiplyScalar(scale));

        // Enable self-illumination & enhance materials
        model.traverse((child) => {
          if ((child as THREE.Mesh).isMesh) {
            const mesh = child as THREE.Mesh;
            if (mesh.material) {
              const mat = mesh.material as THREE.MeshStandardMaterial;
              mat.roughness = Math.min(mat.roughness, 0.4);
              mat.metalness = Math.max(mat.metalness, 0.2);
              mat.envMapIntensity = 1.5;
            }
          }
        });

        scene.add(model);
        modelRef.current = model;

        // Animations if present in GLB
        if (gltf.animations && gltf.animations.length > 0) {
          const mixer = new THREE.AnimationMixer(model);
          gltf.animations.forEach((clip) => mixer.clipAction(clip).play());
          mixerRef.current = mixer;
        }
      },
      undefined,
      (err) => console.error('Failed to load Orion1.glb:', err)
    );

    // 4. Render & Animation Loop
    const clock = new THREE.Clock();
    let reqId = 0;
    const freqBuf = new Uint8Array(128);

    const animate = () => {
      reqId = requestAnimationFrame(animate);
      const delta = clock.getDelta();
      const elapsed = clock.getElapsedTime();

      // Audio reactive level
      let audioAmp = 0;
      if (analyserRef.current) {
        audioAmp = Math.min(1, readLevel(analyserRef.current, freqBuf) * 3.0);
      }

      if (mixerRef.current) {
        mixerRef.current.update(delta * (stateRef.current === 'processing' ? 2.0 : 1.0));
      }

      if (modelRef.current) {
        // Continuous organic rotation
        const rotSpeed = stateRef.current === 'processing' ? 1.5 : stateRef.current === 'listening' ? 0.9 : 0.4;
        modelRef.current.rotation.y += delta * rotSpeed;
        modelRef.current.rotation.x = Math.sin(elapsed * 0.8) * 0.08;
        modelRef.current.rotation.z = Math.cos(elapsed * 0.6) * 0.06;

        // Breathing & voice reactivity
        const basePulse = Math.sin(elapsed * (stateRef.current === 'processing' ? 6 : 2)) * 0.03;
        const reactiveScale = 1.0 + basePulse + audioAmp * 0.18;
        modelRef.current.scale.setScalar((2.4 / 1) * reactiveScale * 0.4);
      }

      // Dynamic light pulsing
      cyanLight.intensity = 4.0 + Math.sin(elapsed * 3) * 1.5 + audioAmp * 4.0;
      purpleLight.intensity = 5.0 + Math.cos(elapsed * 2.5) * 1.5 + audioAmp * 4.0;

      renderer.render(scene, camera);
    };

    animate();

    return () => {
      isDisposed = true;
      cancelAnimationFrame(reqId);
      renderer.dispose();
      scene.clear();
      container.replaceChildren();
    };
  }, [size]);

  return (
    <div className="relative flex items-center justify-center select-none" style={{ width: size, height: size }}>
      {/* Background radial cosmic aura */}
      <div
        className="absolute inset-[-10%] rounded-full blur-3xl pointer-events-none"
        style={{
          background: `radial-gradient(circle, ${colors.purple}55 0%, ${colors.cyan}25 45%, transparent 70%)`,
        }}
      />

      {/* 3D WebGL Canvas Container */}
      <div ref={containerRef} className="relative z-10 w-full h-full flex items-center justify-center" />
    </div>
  );
}
