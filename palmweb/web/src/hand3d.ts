// 3D 손 장면: 실사 텍스처 손 위에 관객의 손금을 그리고, 손금에서 시작한 붉은 핏줄(Blender에서 모델링한 커브)이
// 피부 밑에서 단계에 따라 드러난다.
// 손 메시·관절·핏줄·텍스처는 tools/prepare_hand_blend.py 가 만든다 (모델 공간: +y 손가락, +z 손바닥 쪽, 엄지 왼쪽).
//
// 그리는 순서 (프레임마다):
//   1) 핏줄 패스: 핏줄 튜브 + 손 뒷면(검정, 멀리 있는 핏줄을 가리는 용도)만 별도 렌더 타깃에 그린다 (레이어 1)
//   2) 본 패스: 실사 피부가 화면 좌표로 그 타깃을 읽어 "피부 밑에 비치는 핏줄"로 섞는다 (선명한 층 + 번진 층).
//      손금 선, 별 배경, 블룸.
// 핏줄은 점마다 도착 시간(손금에서 퍼지는 순서)이 있어 현재 덮인 비율까지만 자라고, 박동마다 밝기 파동(펄스)이 손금에서 핏줄 끝으로 흐른다.
// (박동에 맞춰 손 자체가 커졌다 작아지던 연출은 2026-11-07에 뺐다. 펄스 파동은 유지.)
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { type LineKind, type PalmLineSet, type Point } from "./protocol";
import { PalmProjector } from "./projection";
import { COVERAGE, SpreadField, type Seed } from "./spread";

// ---- 팔레트 ----
const BACKGROUND = "#04050c";
const PALM_LINE_COLOR = new THREE.Color(0.92, 0.56, 0.16);                        // 황금: "당신의 선" (밝은 피부 위에서 보이게 진하게)
const VEIN_DEEP = new THREE.Color(0.55, 0.03, 0.07);        // 핏줄 깊은 곳 (어두운 혈색)
const VEIN_BRIGHT = new THREE.Color(1.0, 0.17, 0.11);       // 핏줄 끝·박동 (밝은 빨강)
// ---- 핏줄 튜브 (모델링한 커브) ----
const VEIN_SIDES = [10, 8, 6];                 // 층별 튜브 단면 꼭짓점 수 (줄기·손가락 가지·잔가지)
const VEIN_RADIUS_SCALE = 1.35;                // 모델 반지름 배율 (잔가지가 1px 아래로 내려가지 않게)
const VEIN_RADIUS_MIN = 0.0013;                // v3 의 가는 가지(반지름 중앙값 0.0009)가 너무 굵어지지 않게
const VEIN_STRENGTH = 0.55;                    // 피부 밑으로 비치는 붉은 빛 세기 (어둡게 하는 쪽이 주, 빛은 보조)
const VEIN_DARKEN = 0.9;                       // 핏줄 자리를 어둡게 하는 정도
const SKIN_ROUGHNESS = 1.45;                   // 거칠기 지도 배율 (지도 평균 0.43 → 광이 많아 올린다)
const SKIN_ENV = 0.18;
const COVERAGE_TAU = 0.25;                     // 단계 전환 보간 (초)
const STAGE_TAU = 0.6;                         // 크기·펄스 속도가 단계를 따라가는 시간
const PULSE_SPEED = 1.4;                       // 펄스 파동 속도: 초당 도착시간 단위

// ---- 손금 선 ----
const DRAW_MS = 2000;
const MINOR_DELAY = 500;
const LINE_WIDTH: Record<LineKind, number> = { heart: 3, head: 3, life: 3, fate: 2.4, minor: 1.6 };

// ---- 카메라: 긴 렌즈(FOV 20)로 멀리서, 살짝 3/4 각도 ----
const FOV = 20;
const LOOK_AT = new THREE.Vector3(0, -0.4, 0);      // 화면 아래 30%는 자막 자리라 손을 위쪽에
const CAMERA_HOME = new THREE.Vector3(0, -0.4, 11.6);
const REST_YAW = 0.22;                              // 결과 화면의 기본 각도 (라디안)
const HALF_WIDTH_MIN = 1.2;                         // 세로 화면(휴대폰)에서도 엄지까지 들어오게 카메라를 더 물린다
const RETURN_AFTER_MS = 20000;                      // 관객이 돌려 본 뒤 이 시간 지나면 정면으로 돌아온다

interface DrawnLine { line: Line2; kind: LineKind; segments: number; color: THREE.Color; alpha: number; }
/** 개발 패널에서 바꿀 수 있는 값 */
export interface Look { roughness: number; envMap: number; keyLight: number; veinStrength: number; veinDarken: number; }
interface VeinData { veins: { t: number; n: string; p: number[] }[]; }

function resample(points: Point[], step: number): Point[] {
  const out: Point[] = [points[0]];
  let carry = 0;
  for (let i = 1; i < points.length; i++) {
    const [ax, ay] = points[i - 1], [bx, by] = points[i];
    const d = Math.hypot(bx - ax, by - ay);
    let t = step - carry;
    while (t <= d) { out.push([ax + ((bx - ax) * t) / d, ay + ((by - ay) * t) / d]); t += step; }
    carry = d - (t - step);
  }
  out.push(points[points.length - 1]);
  return out;
}

/** 펄스 파형 (0~1 위상 → 0~1). 쿵(크게) · 쿵(작게) 두 번. 핏줄 밝기에만 쓴다. */
function heartbeat(phase: number) {
  const g = (c: number, w: number) => Math.exp(-((phase - c) ** 2) / w);
  return Math.min(1, g(0, 0.0022) + g(1, 0.0022) + 0.55 * g(0.2, 0.0035));
}

export class HandScene {
  readonly renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 200);
  private composer: EffectComposer | null = null;
  private bloom: UnrealBloomPass | null = null;
  private controls: OrbitControls;
  private root = new THREE.Group();      // 좌우 반전·회전·크기(성장+고동)를 맡는다
  private linesGroup = new THREE.Group();
  private projector: PalmProjector | null = null;
  private joints: THREE.Vector3[] = [];   // 모델 관절 21개 (MediaPipe 순서)
  private lines: DrawnLine[] = [];
  private field: SpreadField | null = null;
  private handMesh: THREE.Mesh | null = null;
  private stars: THREE.Points | null = null;
  private veinData: VeinData | null = null;
  private veinMesh: THREE.Mesh | null = null;
  private veinRT: THREE.WebGLRenderTarget;
  private skinUniforms = { uVeinTex: { value: null as THREE.Texture | null }, uRes: { value: new THREE.Vector2(1, 1) }, uVeinStrength: { value: VEIN_STRENGTH }, uVeinDarken: { value: VEIN_DARKEN } };
  private skinMat: THREE.MeshStandardMaterial | null = null;
  private keyLight: THREE.DirectionalLight | null = null;
  private veinMat: THREE.ShaderMaterial | null = null;
  private uniforms = {
    uCoverage: { value: 0 }, uTime: { value: 0 }, uStage: { value: 1 }, uBeat: { value: 0 },
    uPulse: { value: -1 },   // 박동 파동의 위치 (도착시간 단위). 음수면 없음
  };
  private arrival = new Float32Array(0);
  private stage = 1;
  private stageSmooth = 1;
  private coverage = 0;
  private targetCoverage = 0;
  private mirror = 1;
  private lastFrame = 0;
  private t0 = 0;
  private lastInteractAt = -Infinity;
  private home = CAMERA_HOME.clone();   // 화면 비율에 따라 z가 달라진다
  private ready: Promise<void>;
  idle = true;       // 천천히 흔들리는 대기 동작
  attract = false;   // 대기 화면: 손금 없이 더 크게 천천히 돈다
  onInteract: (() => void) | null = null;   // 관객이 손을 돌리면 (무입력 타이머를 미루기 위해)
  private beatPhase = 0;            // 펄스 주기 위상 (0~1). 단계가 높을수록 빨라진다
  private beat = 0;                 // 펄스 순간의 핏줄 밝기 (손 크기에는 쓰지 않는다)
  private pulseStart = -Infinity;   // 마지막 펄스 시각 → 파동이 손금에서 출발한다
  private fade = 1;          // 손금·핏줄 불투명도 (90초 무입력 뒤 서서히 사라짐)
  private fadeTarget = 1;
  veinStats: Record<string, number> = {};   // 디버그

  constructor(private canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.85;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    // 전시 중 그래픽 장치를 잃으면(드라이버 재시작 등) 페이지를 새로 고쳐 복구한다
    canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); setTimeout(() => location.reload(), 2000); });
    this.scene.background = new THREE.Color(BACKGROUND);
    this.camera.position.copy(CAMERA_HOME);
    this.camera.lookAt(LOOK_AT);

    // 피부의 반사용 환경광 + 3점 조명 (따뜻한 키, 차가운 필, 뒤에서 림)
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    const key = new THREE.DirectionalLight("#fff0e2", 0.7);
    key.position.set(-3, 4, 5);
    this.keyLight = key;
    const fill = new THREE.DirectionalLight("#8aa0c8", 0.22);
    fill.position.set(4, -1, 3);
    const rim = new THREE.DirectionalLight("#cfe0ff", 0.65);
    rim.position.set(1.5, 3.5, -5);
    this.scene.add(key, fill, rim);

    // 관객이 직접 돌려 본다 (마우스 드래그 · 손가락 드래그). 확대·이동은 막아 구도를 지킨다
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.copy(LOOK_AT);
    this.controls.enablePan = false;
    this.controls.enableZoom = false;
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.rotateSpeed = 0.7;
    this.controls.minPolarAngle = 0.25;
    this.controls.maxPolarAngle = Math.PI - 0.25;
    this.controls.addEventListener("start", () => { this.lastInteractAt = performance.now(); this.onInteract?.(); });
    this.controls.addEventListener("change", () => { this.lastInteractAt = performance.now(); });

    this.root.add(this.linesGroup);
    this.scene.add(this.root);
    this.scene.add(this.makeStars());

    // 핏줄 패스용 렌더 타깃 (MSAA, 밉맵: 피부에 번진 느낌은 낮은 밉을 읽는다)
    this.veinRT = new THREE.WebGLRenderTarget(2, 2, {
      samples: 4, generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter,
    });
    this.skinUniforms.uVeinTex.value = this.veinRT.texture;

    // 후처리: MSAA 4x + 블룸(가장 밝은 점만). ?fx=0 으로 끈다 (느린 기기용)
    if (new URLSearchParams(location.search).get("fx") !== "0") {
      const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
      const target = new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType, samples: 4 });
      this.composer = new EffectComposer(this.renderer, target);
      this.composer.addPass(new RenderPass(this.scene, this.camera));
      this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.3, 0.3, 1.15);
      this.composer.addPass(this.bloom);
      this.composer.addPass(new OutputPass());
    }
    this.ready = this.load();
  }

  private async load() {
    const tex = new THREE.TextureLoader();
    const loadTex = (url: string, srgb = false) => tex.loadAsync(url).then((t) => {
      t.flipY = false;   // GLB 의 UV 는 glTF 규약(원점 왼쪽 위)
      t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
      t.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
      return t;
    });
    const [gltf, joints, veins, albedo, normal, roughMetal] = await Promise.all([
      new GLTFLoader().loadAsync("/models/hand.glb"),
      fetch("/models/hand.joints.json").then((r) => r.json() as Promise<{ landmarks: number[][] }>),
      fetch("/models/veins.json").then((r) => r.json() as Promise<VeinData>),
      loadTex("/textures/hand_albedo.jpg", true),
      loadTex("/textures/hand_normal.png"),
      loadTex("/textures/hand_roughmetal.png"),
    ]);
    let mesh: THREE.Mesh | null = null;
    gltf.scene.traverse((o) => { if (!mesh && (o as THREE.Mesh).isMesh) mesh = o as THREE.Mesh; });
    if (!mesh) throw new Error("손 모델에 메시가 없습니다");
    const m = mesh as THREE.Mesh;
    const geo = m.geometry;
    geo.computeVertexNormals();
    geo.setAttribute("arrival", new THREE.BufferAttribute(new Float32Array(geo.getAttribute("position").count).fill(1), 1));
    this.field = new SpreadField(geo);
    this.veinData = veins;

    // 본 패스: 실사 피부. 핏줄 패스: 손 뒷면(검정)이 멀리 있는 핏줄을 가린다
    const skin = new THREE.Mesh(geo, this.skinMaterial(albedo, normal, roughMetal));
    skin.renderOrder = 1;
    const occluder = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x000000, side: THREE.BackSide }));
    occluder.layers.set(1);
    for (const o of [skin, occluder]) { o.frustumCulled = false; this.root.add(o); }
    this.handMesh = skin;
    this.joints = joints.landmarks.map(([x, y, z]) => new THREE.Vector3(x, y, z));
    this.projector = new PalmProjector(skin, this.joints);
  }

  // ---- 재질 ----

  /** 실사 피부(알베도·노멀·거칠기). 화면 좌표로 핏줄 패스를 읽어 피부 밑에 비치는 핏줄을 섞는다. */
  private skinMaterial(albedo: THREE.Texture, normal: THREE.Texture, roughMetal: THREE.Texture) {
    const mat = new THREE.MeshStandardMaterial({
      map: albedo, normalMap: normal, normalScale: new THREE.Vector2(1, 1),
      roughnessMap: roughMetal, metalnessMap: roughMetal, roughness: SKIN_ROUGHNESS, metalness: 1,
      envMapIntensity: SKIN_ENV,
    });
    this.skinMat = mat;
    const u = { ...this.uniforms, ...this.skinUniforms, uDeep: { value: VEIN_DEEP } };
    mat.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, u);
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", `#include <common>
uniform sampler2D uVeinTex; uniform vec2 uRes; uniform float uVeinStrength; uniform float uVeinDarken; uniform float uCoverage; uniform float uBeat; uniform vec3 uDeep;`)
        .replace("#include <color_fragment>", `#include <color_fragment>
vec2 suv = gl_FragCoord.xy / uRes;
vec3 veinSharp = textureLod(uVeinTex, suv, 0.0).rgb;
vec3 veinSoft = textureLod(uVeinTex, suv, 2.0).rgb;
vec3 veinLight = veinSharp * 0.7 + veinSoft * 1.1;
float veinMax = max(veinLight.r, max(veinLight.g, veinLight.b));
float veinMask = clamp(veinMax * 2.2, 0.0, 1.0);
// 핏줄 자리의 피부를 '고른 핏줄 색'으로 물들인다 (피부 밑에 있는 핏줄). 색상은 핏줄 패스의 색 그대로, 세기는 어둡게 슬라이더
vec3 veinHue = veinLight / max(veinMax, 1e-4);
diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * veinHue * 0.9, veinMask * uVeinDarken);`)
        .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
totalEmissiveRadiance += veinLight * uVeinStrength;
// 퍼진 만큼 손 전체가 안에서 아주 약하게 붉어진다 (손전등을 댄 손처럼). 박동마다 조금 더
float fresSkin = pow(1.0 - saturate(dot(normal, normalize(vViewPosition))), 2.0);
totalEmissiveRadiance += uDeep * uCoverage * (0.08 + 0.06 * uBeat) * (0.4 + 0.6 * fresSkin);`);
    };
    return mat;
  }

  /** 핏줄 튜브 재질 (핏줄 패스 전용). 도착 시간으로 자라게 하고, 맨 앞은 뾰족하게, 깊은 곳은 어둡게, 박동 파동은 환하게. */
  private veinMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: { ...this.uniforms, uDeep: { value: VEIN_DEEP }, uBright: { value: VEIN_BRIGHT }, uFade: { value: 1 } },
      side: THREE.DoubleSide,
      vertexShader: /* glsl */ `
        attribute float aRadius, aArr, aDepth;
        uniform float uCoverage;
        varying float vArr, vDepth, vShade;
        void main() {
          float taper = clamp((uCoverage - aArr) / 0.05, 0.0, 1.0);          // 퍼지는 맨 앞은 가늘어진다
          vec3 p = position + normal * aRadius * taper;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          vec3 nv = normalize(normalMatrix * normal);
          vShade = 0.7 + 0.3 * max(0.0, nv.z);                                 // 둥근 음영
          vArr = aArr; vDepth = aDepth;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */ `
        uniform float uCoverage, uBeat, uPulse, uFade;
        uniform vec3 uDeep, uBright;
        varying float vArr, vDepth, vShade;
        void main() {
          if (vArr > uCoverage) discard;
          float front = exp(-pow(max(0.0, uCoverage - vArr) / 0.05, 2.0));
          float pulse = uPulse < 0.0 ? 0.0 : exp(-pow((vArr - uPulse) / 0.07, 2.0));
          float bright = clamp(0.2 + 0.35 * uCoverage + 0.7 * front + pulse, 0.0, 1.0);
          vec3 col = mix(uDeep, uBright, bright) * vShade;
          float deep = clamp(vDepth / 0.12, 0.0, 1.0);                         // 깊은 핏줄은 어둡다
          col *= mix(1.0, 0.7, deep);
          col *= (1.0 + 0.8 * pulse + 0.4 * front + 0.15 * uBeat) * uFade;
          gl_FragColor = vec4(col, 1.0);
        }`,
    });
  }

  /** 별 배경: 멀리 떨어진 구 위에 흩뿌린 작은 점들 (실사 손이 주인공이라 적고 어둡게). */
  private makeStars() {
    const N = 1400;
    const pos = new Float32Array(N * 3), size = new Float32Array(N), phase = new Float32Array(N), col = new Float32Array(N * 3);
    const tints = [[1, 1, 1], [0.75, 0.85, 1], [1, 0.9, 0.75], [0.8, 0.9, 1]];
    for (let i = 0; i < N; i++) {
      const v = new THREE.Vector3().randomDirection().multiplyScalar(60 + Math.random() * 30);
      pos.set([v.x, v.y, v.z], i * 3);
      const r = Math.random();
      size[i] = r < 0.88 ? 0.5 + Math.random() * 0.8 : r < 0.985 ? 1.4 + Math.random() * 1.0 : 2.8 + Math.random() * 1.4;
      phase[i] = Math.random();
      col.set(tints[Math.floor(Math.random() * tints.length)], i * 3);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    geo.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
    const mat = new THREE.ShaderMaterial({
      uniforms: { uTime: this.uniforms.uTime, uPixelRatio: { value: this.renderer.getPixelRatio() } },
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: /* glsl */ `
        attribute float aSize; attribute float aPhase; attribute vec3 aColor;
        uniform float uTime, uPixelRatio;
        varying float vA; varying vec3 vC;
        void main() {
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          float tw = 0.6 + 0.4 * sin(uTime * (0.7 + aPhase * 2.3) + aPhase * 40.0);
          vA = tw; vC = aColor;
          gl_PointSize = aSize * (0.75 + 0.25 * tw) * uPixelRatio * (90.0 / -mv.z) * 1.6;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */ `
        varying float vA; varying vec3 vC;
        void main() {
          float r = length(gl_PointCoord - 0.5);
          float a = smoothstep(0.5, 0.05, r); a *= a;
          gl_FragColor = vec4(vC * vA * 0.8, a);
        }`,
    });
    this.stars = new THREE.Points(geo, mat);
    this.stars.frustumCulled = false;
    return this.stars;
  }

  // ---- 상태 ----

  /** 개발 패널: 색을 바꾼다 (hex). 핏줄 깊은 곳·끝, 손금. */
  setColors(c: { veinDeep?: string; veinBright?: string; palmLine?: string }) {
    if (c.veinDeep) VEIN_DEEP.set(c.veinDeep);
    if (c.veinBright) VEIN_BRIGHT.set(c.veinBright);
    if (c.palmLine) {
      PALM_LINE_COLOR.set(c.palmLine);
      for (const d of this.lines) d.color.copy(PALM_LINE_COLOR);
    }
  }
  getColors() {
    const hex = (x: THREE.Color) => "#" + new THREE.Color(Math.min(1, x.r), Math.min(1, x.g), Math.min(1, x.b)).getHexString();
    return { veinDeep: hex(VEIN_DEEP), veinBright: hex(VEIN_BRIGHT), palmLine: hex(PALM_LINE_COLOR) };
  }
  /** 개발 패널: 재질·조명 값. */
  setLook(l: Partial<Look>) {
    if (this.skinMat) {
      if (l.roughness !== undefined) this.skinMat.roughness = l.roughness;
      if (l.envMap !== undefined) this.skinMat.envMapIntensity = l.envMap;
    }
    if (l.keyLight !== undefined && this.keyLight) this.keyLight.intensity = l.keyLight;
    if (l.veinStrength !== undefined) this.skinUniforms.uVeinStrength.value = l.veinStrength;
    if (l.veinDarken !== undefined) this.skinUniforms.uVeinDarken.value = l.veinDarken;
  }
  getLook(): Look & { textures: Record<string, string> } {
    const m = this.skinMat;
    const size = (t: THREE.Texture | null) => (t && t.image ? `${(t.image as HTMLImageElement).width}×${(t.image as HTMLImageElement).height}` : "없음");
    return {
      roughness: m?.roughness ?? SKIN_ROUGHNESS, envMap: m?.envMapIntensity ?? SKIN_ENV, keyLight: this.keyLight?.intensity ?? 0,
      veinStrength: this.skinUniforms.uVeinStrength.value, veinDarken: this.skinUniforms.uVeinDarken.value,
      textures: { 알베도: size(m?.map ?? null), 노멀: size(m?.normalMap ?? null), 거칠기: size(m?.roughnessMap ?? null) },
    };
  }

  /** 마지막 관객의 핏줄을 거두고 손금을 서서히 지운다. 다 사라지면 resolve. */
  /** 지금 보이는 손만 잘라 JPEG data URL 로 돌려준다 ("여기서 멈춤" 제출용).
   *  손 메시의 경계 상자 8개 꼭짓점을 화면에 투영해 손이 차지하는 영역을 구하고, 여백을 조금 두어 원본 해상도로 잘라 낸다.
   *  (예전에는 화면 전체를 640px 로 줄여 보냈더니 대시보드에서 와이드하고 흐렸다.) */
  snapshot(maxSide = 1200, quality = 0.9): string {
    this.render();   // 드로잉 버퍼는 같은 작업 안에서만 유효하다 (preserveDrawingBuffer 없음)
    const c = this.canvas;
    let x0 = 0, y0 = 0, x1 = c.width, y1 = c.height;
    if (this.handMesh) {
      this.root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(this.handMesh);
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (let i = 0; i < 8; i++) {
        const v = new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).project(this.camera);
        const px = (v.x + 1) / 2 * c.width, py = (1 - v.y) / 2 * c.height;
        minX = Math.min(minX, px); maxX = Math.max(maxX, px); minY = Math.min(minY, py); maxY = Math.max(maxY, py);
      }
      if (isFinite(minX) && maxX > minX && maxY > minY) {
        const pad = 0.06 * Math.max(maxX - minX, maxY - minY);
        x0 = Math.max(0, Math.floor(minX - pad)); y0 = Math.max(0, Math.floor(minY - pad));
        x1 = Math.min(c.width, Math.ceil(maxX + pad)); y1 = Math.min(c.height, Math.ceil(maxY + pad));
      }
    }
    const w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
    const k = Math.min(1, maxSide / Math.max(w, h));
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(w * k));
    out.height = Math.max(1, Math.round(h * k));
    out.getContext("2d")!.drawImage(c, x0, y0, w, h, 0, 0, out.width, out.height);
    return out.toDataURL("image/jpeg", quality);
  }

  fadeOut(ms = 3000): Promise<void> {
    this.setStage(1);
    this.fadeTarget = 0;
    return new Promise((r) => setTimeout(() => { this.clearLines(); this.fade = this.fadeTarget = 1; r(); }, ms));
  }

  /** 단계(1~10): 핏줄이 덮을 비율을 정한다. 실제 화면은 render()에서 부드럽게 따라간다. */
  setStage(k: number) {
    this.stage = Math.max(1, Math.min(COVERAGE.length, Math.round(k)));
    this.targetCoverage = COVERAGE[this.stage - 1];
  }

  /** 카메라를 정면으로 되돌린다 (다음 관객을 위해). */
  resetView() {
    this.camera.position.copy(this.home);
    this.controls.target.copy(LOOK_AT);
    this.controls.update();
    this.lastInteractAt = -Infinity;
  }

  /** 손금 선 점들 → 확산 씨앗 → 도착 시간 필드 → 핏줄 튜브(도착 시간 붙임). */
  private buildSpread(seeds: Seed[]) {
    const f = this.field, m = this.handMesh;
    if (!f || !m || !seeds.length) return { ms: 0 };
    const t0 = performance.now();
    const { arrival, seedVertices } = f.compute(seeds);
    (m.geometry.getAttribute("arrival") as THREE.BufferAttribute).copyArray(f.expand(arrival)).needsUpdate = true;
    this.arrival = arrival;
    const veins = this.buildVeinMesh();
    return { ms: Math.round(performance.now() - t0), seedVertices: seedVertices.length, ...veins };
  }

  /** 모델링한 핏줄 커브 → 튜브 메시. 점마다 가장 가까운 손 정점의 도착 시간을 달고, 커브 위에서 손금에 가장 가까운
   *  점부터 양쪽으로 줄지 않게 보정한다 → 손금에서 자라 나간다. 깊이 = 표면에서의 거리 (깊을수록 어둡게). */
  private buildVeinMesh() {
    const f = this.field!, data = this.veinData;
    if (!data) return { veins: 0 };
    const arr = this.arrival, surf = f.positions;
    let nv = 0, ni = 0;
    const items = data.veins.map((v) => ({ t: v.t, pts: v.p.length / 4, p: v.p }));
    for (const it of items) { const s = VEIN_SIDES[it.t]; nv += it.pts * s; ni += (it.pts - 1) * s * 6; }
    const position = new Float32Array(nv * 3), normal = new Float32Array(nv * 3);
    const aRadius = new Float32Array(nv), aArr = new Float32Array(nv), aDepth = new Float32Array(nv);
    const index = new Uint32Array(ni);
    let vo = 0, io = 0;
    const tmp = new THREE.Vector3(), tan = new THREE.Vector3(), n1 = new THREE.Vector3(), n2 = new THREE.Vector3(), dir = new THREE.Vector3();
    let maxA = 0;
    for (const it of items) {
      const s = VEIN_SIDES[it.t], p = it.p, n = it.pts;
      // 점마다 도착 시간·깊이
      const a = new Float32Array(n), d = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        tmp.set(p[4 * i], p[4 * i + 1], p[4 * i + 2]);
        const v = f.nearestVertex(tmp);
        a[i] = v >= 0 ? arr[v] : 1;
        d[i] = v >= 0 ? Math.hypot(tmp.x - surf[3 * v], tmp.y - surf[3 * v + 1], tmp.z - surf[3 * v + 2]) : 0.1;
      }
      let m = 0;
      for (let i = 1; i < n; i++) if (a[i] < a[m]) m = i;
      for (let i = m + 1; i < n; i++) a[i] = Math.max(a[i], a[i - 1]);
      for (let i = m - 1; i >= 0; i--) a[i] = Math.max(a[i], a[i + 1]);
      // 평행 이동 프레임으로 단면을 세운다
      const base = vo;
      for (let i = 0; i < n; i++) {
        const i0 = Math.max(0, i - 1), i1 = Math.min(n - 1, i + 1);
        tan.set(p[4 * i1] - p[4 * i0], p[4 * i1 + 1] - p[4 * i0 + 1], p[4 * i1 + 2] - p[4 * i0 + 2]).normalize();
        if (i === 0) {
          n1.set(0, 0, 1); if (Math.abs(tan.dot(n1)) > 0.9) n1.set(1, 0, 0);
        }
        n1.addScaledVector(tan, -n1.dot(tan)).normalize();
        n2.crossVectors(tan, n1);
        const r = Math.max(p[4 * i + 3] * VEIN_RADIUS_SCALE, VEIN_RADIUS_MIN);
        for (let k = 0; k < s; k++) {
          const th = (2 * Math.PI * k) / s;
          dir.copy(n1).multiplyScalar(Math.cos(th)).addScaledVector(n2, Math.sin(th));
          position[3 * vo] = p[4 * i]; position[3 * vo + 1] = p[4 * i + 1]; position[3 * vo + 2] = p[4 * i + 2];
          normal[3 * vo] = dir.x; normal[3 * vo + 1] = dir.y; normal[3 * vo + 2] = dir.z;
          aRadius[vo] = r; aArr[vo] = a[i]; aDepth[vo] = d[i];
          vo++;
        }
        if (a[i] > maxA) maxA = a[i];
      }
      for (let i = 0; i < n - 1; i++) for (let k = 0; k < s; k++) {
        const A = base + i * s + k, B = base + i * s + ((k + 1) % s), C = A + s, D = B + s;
        index[io++] = A; index[io++] = B; index[io++] = C;
        index[io++] = B; index[io++] = D; index[io++] = C;
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(position, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(normal, 3));
    geo.setAttribute("aRadius", new THREE.BufferAttribute(aRadius, 1));
    geo.setAttribute("aArr", new THREE.BufferAttribute(aArr, 1));
    geo.setAttribute("aDepth", new THREE.BufferAttribute(aDepth, 1));
    geo.setIndex(new THREE.BufferAttribute(index, 1));
    this.veinMat = this.veinMaterial();
    const mesh = new THREE.Mesh(geo, this.veinMat);
    mesh.frustumCulled = false;
    mesh.layers.set(1);
    this.root.add(mesh);
    this.veinMesh = mesh;
    this.veinStats = { veins: items.length, verts: nv, tris: ni / 3, maxArrival: +maxA.toFixed(3) };
    return { veins: items.length, verts: nv };
  }

  /** 디버그: 단계별로 덮이는 정점 비율을 손 부위별로 센다. */
  debugSpread() {
    const m = this.handMesh;
    if (!m) return null;
    const arr = m.geometry.getAttribute("arrival") as THREE.BufferAttribute;
    const pos = m.geometry.getAttribute("position") as THREE.BufferAttribute;
    const zones: Record<string, number[]> = { thumb: [], fingers: [], palm: [], wrist: [] };
    for (let i = 0; i < arr.count; i++) {
      const x = pos.getX(i), y = pos.getY(i), a = arr.getX(i);
      const zone = y > 0.35 ? "fingers" : y < -0.6 ? "wrist" : x < -0.35 ? "thumb" : "palm";
      zones[zone].push(a);
    }
    const median = (v: number[]) => { const s = [...v].sort((p, q) => p - q); return +s[Math.floor(s.length / 2)].toFixed(3); };
    return Object.fromEntries(Object.entries(zones).map(([k, v]) => [k, median(v)]));
  }

  /** 손금 하나를 손 표면에 투영해 그리기 시작한다. */
  setPalm(palm: PalmLineSet) { return this.setPalms([palm]); }

  /** 손금 여러 개(집단 손)를 한 손에 겹쳐 올린다. 핏줄은 모든 손금에서 동시에 퍼진다.
   *  선은 겹칠수록 진해지도록 반투명하게, 가늘게 그린다. */
  async setPalms(palms: PalmLineSet[]) {
    await this.ready;
    this.clearLines();
    const collective = palms.length > 1;
    const widthScale = collective ? 0.7 : 1;
    const alpha = collective ? Math.max(0.32, 1.2 / Math.sqrt(palms.length)) : 0.95;
    // 투영은 광선을 장면 좌표로 쏘므로, 손의 흔들림 회전·거울 반전·성장 크기를 풀어 둔 상태에서 계산해야 한다.
    this.root.rotation.set(0, 0, 0);
    this.root.scale.set(1, 1, 1);
    this.root.updateMatrixWorld(true);
    const proj = this.projector!;
    const lineLen = (pts: Point[]) => pts.reduce((L, p, i) => (i ? L + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) : 0), 0);
    const seeds: Seed[] = [];
    let dropped = 0;
    for (const palm of palms) {
    const tps = proj.fit(palm.landmarks);
    const ordered = [...palm.lines].sort((a, b) => Number(a.kind === "minor") - Number(b.kind === "minor"));
    const maxMajor = Math.max(1e-6, ...ordered.filter((l) => l.kind !== "minor").map((l) => lineLen(l.points)));
    for (const l of ordered) {
      const pts: number[] = [];
      // 씨앗 속도: 길고 진한 선일수록 그 주변이 먼저 물든다 (명세서 4.8절). 잔선은 절반
      const rel = Math.min(lineLen(l.points) / maxMajor, 1);
      const speed = (0.6 + 0.8 * l.strength) * (0.7 + 0.6 * rel) * (l.kind === "minor" ? 0.5 : 1);
      for (const p of resample(l.points, 0.008)) {
        const s = proj.project(tps, p);
        if (s) { pts.push(s.pos.x, s.pos.y, s.pos.z); seeds.push({ pos: s.pos, speed }); }
        else dropped++;
      }
      if (pts.length < 6) continue;
      const geo = new LineGeometry();
      geo.setPositions(pts);
      const mat = new LineMaterial({
        color: PALM_LINE_COLOR, linewidth: LINE_WIDTH[l.kind] * widthScale, worldUnits: false,
        transparent: true, opacity: alpha * (l.kind === "minor" ? 0.58 : 1),
        // 손을 거울 반전(scale.x = -1)하면 Three.js가 앞뒷면 판정을 뒤집는데,
        // 굵은 선은 셰이더가 화면 기준으로 사각형을 만들어 뒤집히지 않으므로 뒷면으로 지워진다.
        side: THREE.DoubleSide,
      });
      mat.resolution.set(this.canvas.clientWidth, this.canvas.clientHeight);
      const line = new Line2(geo, mat);
      line.renderOrder = 2;
      const segments = pts.length / 3 - 1;
      geo.instanceCount = 0;
      this.linesGroup.add(line);
      this.lines.push({ line, kind: l.kind, segments, color: PALM_LINE_COLOR.clone(), alpha: alpha * (l.kind === "minor" ? 0.58 : 1) });
    }
    }
    const spread = this.buildSpread(seeds);
    this.setStage(1);
    this.coverage = 0;
    this.stageSmooth = 1;
    this.fade = this.fadeTarget = 1;
    // 화면 미리보기와 같은 방향: 정면화 때 좌우를 뒤집은 손은 다시 뒤집어 보여준다
    this.mirror = palms.length === 1 && palms[0].mirrored ? -1 : 1;   // 집단 손은 뒤집지 않는다
    this.t0 = performance.now();
    return { palms: palms.length, lines: this.lines.length, dropped, seeds: seeds.length, spread };
  }

  clearLines() {
    for (const d of this.lines) { d.line.geometry.dispose(); (d.line.material as LineMaterial).dispose(); }
    this.linesGroup.clear();
    this.lines = [];
    if (this.veinMesh) { this.root.remove(this.veinMesh); this.veinMesh.geometry.dispose(); (this.veinMesh.material as THREE.ShaderMaterial).dispose(); this.veinMesh = null; }
    this.coverage = this.targetCoverage = 0;
    this.stage = 1;
    const arr = this.handMesh?.geometry.getAttribute("arrival") as THREE.BufferAttribute | undefined;
    if (arr) { (arr.array as Float32Array).fill(1); arr.needsUpdate = true; }
  }

  /** 디버그용: 투영된 선 점들 (모델 공간). */
  debugLines() {
    return this.lines.map((d) => {
      const a = (d.line.geometry as LineGeometry).attributes.instanceStart as THREE.InterleavedBufferAttribute;
      const zs: number[] = [];
      for (let i = 0; i < a.count; i++) zs.push(a.getZ(i));
      return { kind: d.kind, segments: d.segments, zMin: Math.min(...zs), zMax: Math.max(...zs) };
    });
  }

  render(now = performance.now()) {
    const c = this.canvas;
    const w = c.clientWidth, h = c.clientHeight;
    const pr = this.renderer.getPixelRatio();
    if (w && h && (c.width !== Math.round(w * pr) || c.height !== Math.round(h * pr))) {
      this.renderer.setSize(w, h, false);
      this.composer?.setSize(w, h);
      const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
      this.veinRT.setSize(size.x, size.y);
      this.skinUniforms.uRes.value.copy(size);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      // 좁은 화면이면 손 폭이 들어올 만큼 카메라를 뒤로
      const halfTan = Math.tan((this.camera.fov / 2) * Math.PI / 180);
      this.home.z = Math.max(CAMERA_HOME.z, HALF_WIDTH_MIN / (halfTan * this.camera.aspect));
      if (this.lastInteractAt === -Infinity) this.camera.position.copy(this.home);
      for (const d of this.lines) (d.line.material as LineMaterial).resolution.set(w, h);
    }
    const dt = this.lastFrame ? Math.min((now - this.lastFrame) / 1000, 0.1) : 0;
    this.lastFrame = now;
    const t = now / 1000;

    // 단계를 부드럽게 따라가는 값 → 크기·펄스 속도. 손 크기는 박동 없이 단계에 따라 조금씩만 커진다
    this.stageSmooth += (this.stage - this.stageSmooth) * (1 - Math.exp(-dt / STAGE_TAU));
    const k = (this.stageSmooth - 1) / (COVERAGE.length - 1);          // 0~1
    const hasLines = this.lines.length > 0;
    const bpm = hasLines ? 48 + 84 * k : 36;                            // 펄스 주기: 1단계 48 → 10단계 132
    const prevPhase = this.beatPhase;
    this.beatPhase = (this.beatPhase + dt * bpm / 60) % 1;
    if (this.beatPhase < prevPhase) this.pulseStart = now;             // 새 펄스 → 파동이 손금에서 출발
    this.beat = heartbeat(this.beatPhase) * (hasLines ? 0.35 + 0.65 * k : 0.25);
    const s = 1 + (hasLines ? 0.007 * (this.stageSmooth - 1) : 0);     // 10단계에서 약 6% 커진다

    // 손바닥을 살짝 3/4 각도에서 보고, 천천히 흔들어 입체감을 준다
    const sway = this.attract ? Math.sin(t * 0.25) * 0.75 : this.idle ? Math.sin(t * 0.35) * 0.12 : 0;
    this.root.rotation.set(-0.12, (this.attract ? 0 : REST_YAW) + sway, 0);
    this.root.scale.set(this.mirror * s, s, s);
    if (this.stars) this.stars.rotation.y = t * 0.004;

    // 관객이 돌려 본 뒤 한참 지나면 정면으로 천천히 돌아온다
    if (now - this.lastInteractAt > RETURN_AFTER_MS && this.camera.position.distanceToSquared(this.home) > 1e-4) {
      this.camera.position.lerp(this.home, 1 - Math.exp(-dt / 1.2));
    }
    this.controls.update();

    const el = now - this.t0;
    for (const d of this.lines) {
      const delay = d.kind === "minor" ? MINOR_DELAY : 0;
      const kk = Math.max(0, Math.min((el - 300 - delay) / DRAW_MS, 1));
      (d.line.geometry as LineGeometry).instanceCount = Math.round(d.segments * (1 - (1 - kk) ** 3));
    }
    this.updateSpread(now, dt);
    this.updateFade(dt);

    // 1) 핏줄 패스: 레이어 1(핏줄 튜브 + 뒷면 가림)만 렌더 타깃에
    const bg = this.scene.background;
    this.scene.background = null;
    this.camera.layers.set(1);
    this.renderer.setRenderTarget(this.veinRT);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.clear();
    this.renderer.render(this.scene, this.camera);
    this.renderer.setRenderTarget(null);
    this.camera.layers.set(0);
    this.scene.background = bg;
    // 2) 본 패스
    if (this.composer) this.composer.render(); else this.renderer.render(this.scene, this.camera);
  }

  private updateFade(dt: number) {
    if (this.fade === this.fadeTarget) return;
    this.fade += Math.sign(this.fadeTarget - this.fade) * dt / 2.5;   // 2.5초에 걸쳐
    this.fade = Math.max(0, Math.min(1, this.fade));
    for (const d of this.lines) {
      const m = d.line.material as LineMaterial;
      m.opacity = d.alpha * this.fade;
    }
    if (this.veinMesh) (this.veinMesh.material as THREE.ShaderMaterial).uniforms.uFade.value = this.fade;
  }

  private updateSpread(now: number, dt: number) {
    // 단계가 바뀌면 덮인 비율이 지수적으로 따라가 핏줄이 자라는(거둬지는) 것처럼 보인다
    this.coverage += (this.targetCoverage - this.coverage) * (1 - Math.exp(-dt / COVERAGE_TAU));
    if (Math.abs(this.targetCoverage - this.coverage) < 1e-4) this.coverage = this.targetCoverage;
    // 손금이 다 그려진 뒤부터 퍼진다
    const drawn = Math.min(Math.max((now - this.t0 - 300) / 2000, 0), 1);
    const c = this.coverage * (drawn >= 1 ? 1 : 0);
    const u = this.uniforms;
    u.uCoverage.value = c;
    u.uTime.value = now / 1000;
    u.uStage.value = this.stageSmooth;
    u.uBeat.value = this.beat;
    // 펄스 파동: 손금(도착시간 0)에서 출발해 덮인 곳 끝까지 간다
    const pulse = (now - this.pulseStart) / 1000 * PULSE_SPEED;
    u.uPulse.value = pulse <= 1.25 ? pulse : -1;
    // 손금 색은 단계와 관계없이 고른 색 그대로 (10단계 백열 연출은 뺐다)
    for (const d of this.lines) (d.line.material as LineMaterial).color.copy(d.color);
  }
}
