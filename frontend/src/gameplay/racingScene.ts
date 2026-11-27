import * as THREE from 'three';
import { TRACK } from './racing.mjs';
import type { RaceObservation } from './racing.mjs';
import {fitTrackOverview,followCameraPose,selectFollowTarget} from './racingCamera.mjs';
import type {CameraOptions} from './racingCamera.mjs';

/** Original geometry-only artwork. Rendering never advances or scores the game. */
export function createRacingScene(canvas: HTMLCanvasElement) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#8ac5cd');
  const chaseFog = new THREE.Fog('#8ac5cd', 95, 280);
  scene.fog = chaseFog;
  scene.add(new THREE.HemisphereLight('#def6ff', '#425947', 2.4));
  const sun = new THREE.DirectionalLight('#fff2d0', 3.1);
  sun.position.set(-35, 80, 35); sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  sun.shadow.bias = -.0015; sun.shadow.normalBias = .15;
  Object.assign(sun.shadow.camera, { left: -80, right: 80, top: 80, bottom: -80, far: 180 });
  scene.add(sun);
  const followCamera = new THREE.PerspectiveCamera(52, 1, .1, 500);
  const overviewCamera = new THREE.OrthographicCamera(-80, 80, 60, -60, .1, 500);
  overviewCamera.up.set(0, 0, -1);
  const materials: THREE.Material[] = [];
  function material(color: string, roughness = .8) {
    const m = new THREE.MeshStandardMaterial({ color, roughness }); materials.push(m); return m;
  }
  const sand = material('#d6ba82'), grass = material('#71916a'), asphalt = material('#293740');
  const pale = material('#e2ece6'), cyan = material('#5ee7dd'), red = material('#e97853');
  const dark = material('#182831'), tires = material('#132028'), trunk = material('#8c6946');
  const foliage = [material('#315f57'), material('#456e59'), material('#588361')];
  function mesh(geometry: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D = scene) {
    const item = new THREE.Mesh(geometry, mat); item.position.set(x, y, z);
    item.castShadow = true; item.receiveShadow = true; parent.add(item); return item;
  }
  mesh(new THREE.PlaneGeometry(700, 700), material('#438b97', .35), 0, -1.5, 0).rotation.x = -Math.PI / 2;
  mesh(new THREE.CylinderGeometry(75, 79, 2, 96), sand, 0, -1.1, 0).receiveShadow = false;
  mesh(new THREE.CylinderGeometry(69, 72, 1.4, 96), grass, 0, -.7, 0).receiveShadow = false;
  const points = TRACK.centerline;
  function roadStrip(inner: number, outer: number, mat: THREE.Material, y: number) {
    const vertices: number[] = [], indices: number[] = [];
    points.forEach((p, i) => {
      const prev = points[(i + points.length - 1) % points.length], next = points[(i + 1) % points.length];
      const dx = next.x - prev.x, dz = next.z - prev.z, length = Math.hypot(dx, dz);
      for (const offset of [inner, outer]) vertices.push(p.x - dz / length * offset, y, p.z + dx / length * offset);
      const a = i * 2, b = ((i + 1) % points.length) * 2;
      indices.push(a, b, a + 1, b, b + 1, a + 1);
    });
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3)); geometry.setIndex(indices); geometry.computeVertexNormals();
    // Both sides avoid relying on triangulation winding at the seam.
    if (mat instanceof THREE.MeshStandardMaterial) mat.side = THREE.DoubleSide;
    mesh(geometry, mat, 0, 0, 0).castShadow = false;
  }
  roadStrip(-6, 6, asphalt, .05); roadStrip(-6.45, -6, pale, .07); roadStrip(6, 6.45, pale, .07);
  for (let i = 0; i < points.length; i += 3) {
    const p = points[i], q = points[(i + 1) % points.length];
    const angle = Math.atan2(q.z - p.z, q.x - p.x);
    const dash = mesh(new THREE.BoxGeometry(1.5, .02, .12), pale, p.x, .09, p.z); dash.rotation.y = -angle;
    if (i % 6 === 0) for (const side of [-1, 1]) {
      const x = p.x - Math.sin(angle) * 7.8 * side, z = p.z + Math.cos(angle) * 7.8 * side;
      mesh(new THREE.BoxGeometry(.35, .8, .35), i % 12 === 0 ? cyan : red, x, .4, z);
    }
  }
  // Seeded-looking fixed art, independent of physics and benchmark seeds.
  for (let i = 0; i < 65; i++) {
    const angle = i * 2.399963, radius = 57 + (i * 17 % 10), x = Math.cos(angle) * radius, z = Math.sin(angle) * radius;
    // Keep non-colliding decoration clear of the drivable road and shoulder.
    const onRoad = points.some((p, j) => {
      const q = points[(j + 1) % points.length], dx = q.x - p.x, dz = q.z - p.z;
      const t = Math.max(0, Math.min(1, ((x - p.x) * dx + (z - p.z) * dz) / (dx * dx + dz * dz)));
      return Math.hypot(x - p.x - t * dx, z - p.z - t * dz) < 10;
    });
    if (onRoad) continue;
    const height = 2.6 + i % 4;
    mesh(new THREE.CylinderGeometry(.18, .25, 1.2, 5), trunk, x, .6, z);
    mesh(new THREE.ConeGeometry(1.5, height, 6), foliage[i % 3], x, 1.2 + height / 2, z);
  }
  for (let i = 0; i < 9; i++) {
    const angle = i * .7, radius = 17 + i % 3 * 3;
    mesh(new THREE.DodecahedronGeometry(1 + i % 3, 0), sand, Math.cos(angle) * radius, .5, Math.sin(angle) * radius);
  }
  const start = points[0], next = points[1], heading = Math.atan2(next.z - start.z, next.x - start.x);
  const gate = new THREE.Group(); gate.position.set(start.x, 0, start.z); gate.rotation.y = -heading; scene.add(gate);
  for (const side of [-1, 1]) mesh(new THREE.BoxGeometry(.6, 7, .6), dark, 0, 3.5, side * 7, gate);
  mesh(new THREE.BoxGeometry(1.1, 1.2, 15), dark, 0, 7, 0, gate);
  const label = document.createElement('canvas'); label.width = 1024; label.height = 128;
  const ctx = label.getContext('2d')!; ctx.fillStyle = '#182831'; ctx.fillRect(0, 0, 1024, 128); ctx.fillStyle = '#9ef2dd'; ctx.font = 'bold 65px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('TIDAL  /  RING', 512, 87);
  const texture = new THREE.CanvasTexture(label), bannerMaterial = new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide }); materials.push(bannerMaterial);
  const banner = mesh(new THREE.PlaneGeometry(12, 1.5), bannerMaterial, .6, 7.1, 0, gate); banner.rotation.y = Math.PI / 2;
  for (let row = 0; row < 2; row++) for (let col = 0; col < 16; col++) mesh(new THREE.BoxGeometry(.7, .025, .75), (row + col) % 2 ? pale : dark, row * .7, .1, -5.6 + col * .75, gate);
  const car = new THREE.Group(); scene.add(car);
  mesh(new THREE.BoxGeometry(3.2, .65, 1.5), red, 0, .65, 0, car);
  mesh(new THREE.BoxGeometry(1.4, .6, 1.2), dark, -.2, 1.2, 0, car);
  mesh(new THREE.BoxGeometry(.9, .04, 1.22), cyan, -.25, 1.52, 0, car);
  mesh(new THREE.BoxGeometry(.15, .12, 1.25), pale, 1.6, .7, 0, car);
  mesh(new THREE.BoxGeometry(.35, .15, 1.9), dark, -1.35, 1.2, 0, car);
  for (const x of [-1, 1]) for (const z of [-.85, .85]) mesh(new THREE.CylinderGeometry(.4, .4, .3, 12), tires, x, .4, z, car).rotation.x = Math.PI / 2;
  // Spectator only: no reference to the human state or collision simulation.
  const ghostCar = car.clone(); ghostCar.visible = false; scene.add(ghostCar);
  ghostCar.traverse(object => {
    if (object instanceof THREE.Mesh) {
      const ghostMaterial = new THREE.MeshBasicMaterial({ color: '#d7a3ff', transparent: true, opacity: .48, depthWrite: false });
      object.material = ghostMaterial; materials.push(ghostMaterial); object.castShadow = false; object.receiveShadow = false;
    }
  });
  const resize = () => {
    const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
    renderer.setSize(width, height, false);
    followCamera.aspect = width / height; followCamera.updateProjectionMatrix();
    const fit = fitTrackOverview(TRACK.centerline, TRACK.width / 2 + 3, width, height);
    Object.assign(overviewCamera, {left: -fit.halfWidth, right: fit.halfWidth, top: fit.halfHeight, bottom: -fit.halfHeight});
    overviewCamera.position.set(fit.centerX, 180, fit.centerZ);
    overviewCamera.lookAt(fit.centerX, 0, fit.centerZ); overviewCamera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize); observer.observe(canvas); resize();
  let previousMode = '', previousTarget = -1;
  return {
    render(state: RaceObservation, overview = false, ghost?: RaceObservation, options?: CameraOptions) {
      ghostCar.visible = !!ghost;
      if (ghost) { ghostCar.position.set(ghost.x, .18, ghost.z); ghostCar.rotation.y = -ghost.heading; }
      car.position.set(state.x, .12, state.z); car.rotation.y = -state.heading;
      const mode = options?.mode ?? (overview ? 'overview' : 'follow');
      const targetIndex = options?.target ?? 0;
      let camera: THREE.Camera = overviewCamera;
      scene.fog = mode === 'overview' ? null : chaseFog;
      if (mode === 'follow') {
        const target = selectFollowTarget([state, ghost], targetIndex) ?? state;
        const pose = followCameraPose(target);
        const position = new THREE.Vector3(pose.position.x, pose.position.y, pose.position.z);
        followCamera.position.lerp(position, previousMode === mode && previousTarget === targetIndex ? .09 : 1);
        followCamera.lookAt(pose.lookAt.x, pose.lookAt.y, pose.lookAt.z);
        camera = followCamera;
      }
      previousMode = mode; previousTarget = targetIndex;
      renderer.render(scene, camera);
    },
    dispose() { observer.disconnect(); scene.traverse(object => { if (object instanceof THREE.Mesh) object.geometry.dispose(); }); materials.forEach(m => m.dispose()); texture.dispose(); renderer.dispose(); }
  };
}
