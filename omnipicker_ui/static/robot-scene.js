import * as THREE from 'three';
import {Gizmos} from './gizmos.js';
import {OrbitControls} from './vendor/OrbitControls.js';
import {STLLoader} from './vendor/STLLoader.js';

const DEG = Math.PI / 180;
const geometries = new Map();
let manifestPromise;

async function geometry(url) {
  if (!geometries.has(url)) geometries.set(url, new STLLoader().loadAsync(url).then(mesh => {
    mesh.computeBoundingBox(); mesh.computeVertexNormals(); return mesh;
  }));
  return geometries.get(url);
}
function origin(object, value) {
  object.position.fromArray(value.xyz);
  object.quaternion.setFromEuler(new THREE.Euler(...value.rpy, 'ZYX'));
}

class Rig {
  constructor(spec, meshes, ghost) {
    this.spec = spec; this.root = new THREE.Group(); this.links = new Map(); this.joints = new Map();
    this.ghost = ghost;
    const material = ghost
      ? new THREE.MeshBasicMaterial({color: 0x56ceff, transparent: true, opacity: .16, depthWrite: false, depthTest: false, side: THREE.DoubleSide})
      : new THREE.MeshStandardMaterial({color: spec.name === 'OmniPicker' ? 0x3d464b : 0xc9d2d8, metalness: .48, roughness: .4});
    for (const link of spec.links) {
      const frame = new THREE.Group(); frame.name = link.name;
      if (link.mesh) {
        const visual = new THREE.Group(); origin(visual, link.origin);
        const mesh = new THREE.Mesh(meshes.get(link.mesh), material);
        mesh.castShadow = !ghost; mesh.receiveShadow = !ghost;
        mesh.renderOrder = ghost ? 10 : 0;
        visual.add(mesh);
        if (ghost) {
          const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 35), new THREE.LineBasicMaterial({color: 0x69ddff, transparent: true, opacity: .28, depthTest: false, depthWrite: false}));
          edges.renderOrder = 11; visual.add(edges);
        }
        frame.add(visual);
      }
      this.links.set(link.name, frame);
    }
    for (const joint of spec.joints) {
      const anchor = new THREE.Group(); origin(anchor, joint.origin);
      const pivot = new THREE.Group(); anchor.add(pivot);
      this.links.get(joint.parent).add(anchor); pivot.add(this.links.get(joint.child));
      this.joints.set(joint.name, {pivot, spec: joint});
    }
    this.root.add(this.links.get(spec.root));
  }
  arm(angles) {
    this.spec.joints.filter(joint => joint.type === 'revolute').forEach((joint, index) => {
      this.joints.get(joint.name).pivot.quaternion.setFromAxisAngle(new THREE.Vector3(...joint.axis).normalize(), angles[index] * DEG);
    });
  }
  gripper(ratio) {
    const driven = THREE.MathUtils.clamp(ratio, 0, 1) * this.spec.lower;
    const values = new Map([[this.spec.driven, driven]]);
    const value = name => {
      if (values.has(name)) return values.get(name);
      const mimic = this.joints.get(name)?.spec.mimic;
      const result = mimic ? value(mimic.joint)*mimic.multiplier + mimic.offset : 0;
      values.set(name, result); return result;
    };
    for (const [name, joint] of this.joints) {
      joint.pivot.quaternion.setFromAxisAngle(new THREE.Vector3(...joint.spec.axis).normalize(), value(name));
    }
  }
  fingersVisible(visible) {
    for (const joint of this.spec.joints) {
      if (joint.parent === this.spec.root) this.links.get(joint.child).visible = visible;
    }
  }
}

export class RobotScene {
  constructor(container, {gripperOnly = false, model = 'FR3WMS'} = {}) {
    this.container = container; this.gripperOnly = gripperOnly; this.model = model;
    this.ready = false; this.state = null; this.target = null; this.targetGrip = .5;
    this.measured = null; this.lastAngles = null; this.frame = 0;
    this.camera = new THREE.PerspectiveCamera(38, 1, .002, 30); this.camera.up.set(0, 0, 1);
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color(0x172029);
    this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: false, powerPreference: 'high-performance'});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.4;
    container.append(this.renderer.domElement);
    this.renderer.domElement.setAttribute('aria-label', 'STL 로봇 3D 뷰 · 우클릭 회전');
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.dampingFactor = .09;
    this.controls.mouseButtons = {LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE};
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = gripperOnly ? .055 : .12; this.controls.maxDistance = 5;
    this.renderer.domElement.addEventListener('contextmenu', event => event.preventDefault());
    const hemi = new THREE.HemisphereLight(0xe5f2ff, 0x304337, 2.3); this.scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, 3.2); key.position.set(.7, -.9, 1.9); key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048); key.shadow.camera.left = -1.2; key.shadow.camera.right = 1.2;
    key.shadow.camera.top = 1.2; key.shadow.camera.bottom = -1.2; key.shadow.camera.near = .05; key.shadow.camera.far = 5;
    key.shadow.bias = -.00015; key.shadow.normalBias = .002; this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x8ec9e9, 1.6); rim.position.set(-1.5, 1, 1); this.scene.add(rim);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(8, 8), new THREE.MeshStandardMaterial({color: 0x19252e, roughness: .98, metalness: .02}));
    floor.position.z = -.002; floor.receiveShadow = true; this.scene.add(floor);
    const grid = new THREE.GridHelper(4, 40, 0x3f5866, 0x263b47); grid.rotation.x = Math.PI/2; grid.position.z = -.001; this.scene.add(grid);
    this.baseAxes = new THREE.AxesHelper(gripperOnly ? .045 : .12);this.baseAxes.visible=gripperOnly;this.scene.add(this.baseAxes);
    this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(container);
    this.home(); this.resize();
    this.load().catch(error => { this.error = error.message; container.dispatchEvent(new CustomEvent('modelerror', {detail: error.message})); });
    this.animate();
  }
  resize() {
    const {width, height} = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.camera.aspect = width/height; this.camera.updateProjectionMatrix(); this.renderer.setSize(width, height, false);
  }
  async load() {
    manifestPromise ||= fetch('/static/models/models.json').then(response => {
      if (!response.ok) throw Error('모델 파일 없음'); return response.json();
    });
    const manifest = await manifestPromise;
    const arm = manifest.arms[this.model]; if (!arm) throw Error('지원 모델 없음');
    const grip = manifest.gripper;
    const sources = [...(this.gripperOnly ? [] : arm.links), ...grip.links].map(link => link.mesh).filter(Boolean);
    const data = new Map(await Promise.all(sources.map(async source => [source, await geometry(source)])));
    this.currentGrip = new Rig(grip, data, false); this.goalGrip = new Rig(grip, data, true);
    if (this.gripperOnly) {
      this.scene.add(this.currentGrip.root, this.goalGrip.root);
    } else {
      this.currentArm = new Rig(arm, data, false); this.goalArm = new Rig(arm, data, true);
      this.currentArm.links.get(arm.flange).add(this.currentGrip.root);
      this.goalArm.links.get(arm.flange).add(this.goalGrip.root);
      this.scene.add(this.currentArm.root, this.goalArm.root);
      this.gizmos=new Gizmos(this);this.setMode(this.mode||'joint');
    }
    this.ready = true;
    this.container.dataset.loaded = 'true';
    this.container.dispatchEvent(new CustomEvent('modelloaded', {detail: {model: this.model, meshes: data.size}}));
    this.apply();
    this.home();
  }
  update(state, target, targetGrip) {
    const firstLive = !this.state?.robot?.joints && Array.isArray(state?.robot?.joints);
    this.state = state; this.target = target?.slice() || null;
    if (Number.isFinite(targetGrip)) this.targetGrip = targetGrip;
    this.apply();
    if (firstLive && this.ready) this.home();
  }
  apply() {
    if (!this.ready) return;
    const joints = this.state?.robot?.joints;
    const actualGrip = this.state?.gripper?.actual_position;
    if (this.currentArm) {
      this.currentArm.root.visible = Array.isArray(joints);
      this.currentGrip.root.visible = Array.isArray(joints);
      this.goalArm.root.visible = Array.isArray(this.target) || Array.isArray(joints);
      if (Array.isArray(joints) && !this.lastAngles) {
        this.lastAngles = joints.slice(); this.currentArm.arm(joints);
      }
      if (this.target || joints) this.goalArm.arm(this.target || joints);
    }
    this.currentGrip.fingersVisible(Number.isFinite(actualGrip));
    if (Number.isFinite(actualGrip)) this.currentGrip.gripper(actualGrip/255);
    this.goalGrip.gripper(this.targetGrip);
    this.goalGrip.root.visible = !this.goalArm || this.goalArm.root.visible;
  }
  setMode(mode) { this.mode=mode;this.gizmos?.setMode(mode); }
  tcp(offset) { this.gizmos?.tcp(offset); }
  draft(reference,delta) { this.gizmos?.draft(reference,delta); }
  mount(xyz, rpy) {
    if (!this.ready) return;
    for (const rig of [this.currentGrip, this.goalGrip]) origin(rig.root, {xyz, rpy});
  }
  home() {
    if (this.ready) {
      this.scene.updateMatrixWorld(true);
      const box = new THREE.Box3();
      for (const rig of this.gripperOnly ? [this.currentGrip,this.goalGrip] : [this.currentArm,this.goalArm]) {
        if (rig.root.visible) box.expandByObject(rig.root);
      }
      const centre = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const verticalFov = THREE.MathUtils.degToRad(this.camera.fov);
      const distance = Math.max(size.z, size.x, size.y) / (2*Math.tan(verticalFov/2)) * 1.35 / Math.min(1,this.camera.aspect);
      this.controls.target.copy(centre);
      this.camera.position.copy(centre).add(new THREE.Vector3(1,-1.4,.85).normalize().multiplyScalar(Math.max(this.gripperOnly ? .22 : .6,distance)));
    } else if (this.gripperOnly) { this.camera.position.set(.22, -.27, .2); this.controls.target.set(0, 0, .075); }
    else { this.camera.position.set(1.15, -1.45, 1.1); this.controls.target.set(.12, 0, .38); }
    this.controls.update();
  }
  focusGripper() {
    if (!this.ready) return;
    this.scene.updateMatrixWorld(true);
    const position = new THREE.Vector3(); this.goalGrip.root.getWorldPosition(position);
    const offset = new THREE.Vector3(.22, -.26, .16);
    this.controls.target.copy(position).add(new THREE.Vector3(0, 0, .04));
    this.camera.position.copy(this.controls.target).add(offset); this.controls.update();
  }
  animate() {
    this.frame = requestAnimationFrame(() => this.animate());
    if (this.ready && this.currentArm && this.state?.robot?.joints) {
      const angles = this.state.robot.joints;
      this.lastAngles ||= angles.slice();
      this.lastAngles = this.lastAngles.map((value, index) => THREE.MathUtils.lerp(value, angles[index], .35));
      this.currentArm.arm(this.lastAngles);
    }
    if(!this.gizmos?.drag)this.controls.update(); this.renderer.render(this.scene, this.camera);
  }
  dispose() {
    cancelAnimationFrame(this.frame); this.gizmos?.dispose();this.observer.disconnect(); this.controls.dispose(); this.renderer.dispose();
  }
}
