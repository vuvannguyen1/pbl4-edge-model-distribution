// Giao diện client /app: mở link là xin được model phát hiện vật thể và chạy trên camera, không
// cần cài Python hay gõ lệnh. Các bước giống client.agent theo Protocol 1.0:
//   /api/register -> /api/probe -> /api/request -> tải miếng (server + peer WebRTC, kiểm SHA-256)
//   -> /api/have -> chạy model trên máy -> /api/feedback (số đo + đánh giá của người dùng).
// Ảnh/khung hình camera không bao giờ gửi lên server. Dữ liệu hiển thị đi qua textContent (h()).
import { ApiError, api, badge, chunkGrid, clear, fmt, h, icon, toast } from "/ui/shared/js/ui.js";
import { Detector } from "./detector.js";
import { COCO_LABELS_VI, webSupported } from "./labels.js";
import { announce, deviceInfo, download, probe, register, reportHave, requestModel } from "./net.js";
import { P2P } from "./p2p.js";
import { sha256Hex } from "./sha256.js";
import * as store from "./store.js";

const $ = (id) => document.getElementById(id);
const TABS = ["model", "camera", "feedback"];
const METRIC_RUNS = 10; // số khung đầu tiên dùng tính latency/FPS gửi server
const PHOTO_MAX_SIDE = 1280;
const BIG_MODEL_BYTES = 100 * 1048576;
const LIVE_FPS = 5;
const DEVICE_ID_RE = /^[A-Za-z0-9_-]{1,32}$/;
const KEY_DEVICE = "pbl4.app.deviceId";
const KEY_ACTIVE = "pbl4.app.activeVariant";
const KEY_MODE = "pbl4.app.mode";
const BOX_COLORS = ["#1d4ed8", "#c2410c", "#15803d", "#7e22ce", "#b91c1c", "#0f766e", "#a16207", "#be185d"];
const VERDICT = { positive: ["success", "Tích cực"], neutral: ["warning", "Trung tính"], negative: ["danger", "Tiêu cực"] };
const MODE_HINT = {
  live: `Ưu tiên tốc độ: server chọn model chạy được ít nhất ${LIVE_FPS} khung hình/giây trên máy này.`,
  photo: "Ưu tiên độ chính xác: server chọn model tốt nhất chạy một ảnh trong khoảng 2,5 giây.",
};

const state = {
  deviceId: null,
  deviceClass: null,
  bandwidth: null,
  registered: false,
  variants: [], // /admin-api/variants (task object_detection)
  local: [], // meta model trong IndexedDB
  bytes: new Map(), // variant_id -> Uint8Array, để chia sẻ P2P không phải đọc lại IndexedDB
  detector: null,
  busy: false,
  requestId: null,
  metrics: null, // số đo lượt tải gần nhất, gửi sau METRIC_RUNS lượt nhận diện
  latencies: [],
  last: null, // {boxes, at, source, width, height} lượt nhận diện gần nhất
  live: false,
  stream: null,
  facing: "environment",
  photo: null, // canvas ảnh đang xem
  fps: null,
  lastFrameAt: 0,
  onlyClass: "",
  p2p: null,
};

// ---------------------------------------------------------------- tiện ích

function lsGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function lsSet(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // trình duyệt chặn lưu trữ: chỉ mất phần ghi nhớ giữa các lần mở
  }
}

function initDeviceId() {
  const fromUrl = new URLSearchParams(location.search).get("device");
  if (fromUrl && DEVICE_ID_RE.test(fromUrl)) {
    lsSet(KEY_DEVICE, fromUrl);
    return fromUrl;
  }
  const saved = lsGet(KEY_DEVICE);
  if (saved && DEVICE_ID_RE.test(saved)) return saved;
  const info = deviceInfo();
  const prefix = info.platform === "ios_browser" ? "iphone" : info.platform === "android_browser" ? "android" : "web";
  const id = `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
  lsSet(KEY_DEVICE, id);
  return id;
}

function setConn(ok, text) {
  clear(
    $("conn"),
    ok ? badge("success", text || "Đã kết nối server", "cloud-check") : badge("danger", text || "Mất kết nối server", "cloud-slash"),
  );
}

// Gọi API; mất mạng thì cập nhật huy hiệu kết nối
async function guard(promise) {
  try {
    const data = await promise;
    setConn(true);
    return data;
  } catch (err) {
    if (err instanceof ApiError && err.kind === "network") setConn(false);
    throw err;
  }
}

function setBusy(button, busy, text) {
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
  if (text) button.querySelector("span").textContent = text;
}

const currentMode = () => document.querySelector('input[name="mode"]:checked')?.value || "live";

// ---------------------------------------------------------------- tab

function showTab(name) {
  const tab = TABS.includes(name) ? name : "model";
  for (const panel of document.querySelectorAll("[data-panel]")) panel.hidden = panel.dataset.panel !== tab;
  for (const link of document.querySelectorAll("[data-tab]")) {
    if (link.dataset.tab === tab) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (tab !== "camera") stopCamera(); // rời tab Camera thì tắt camera cho đỡ pin
}

function goTab(name) {
  if (location.hash === `#${name}`) showTab(name);
  else location.hash = name;
}

// ---------------------------------------------------------------- các bước xin model

const STEPS = [
  ["register", "Đăng ký thiết bị"],
  ["probe", "Đo băng thông"],
  ["request", "Xin model"],
  ["download", "Tải từng miếng, kiểm SHA-256"],
  ["save", "Lưu vào máy"],
  ["load", "Nạp model"],
];

class Steps {
  constructor(container) {
    this.container = container;
    this.items = Object.fromEntries(STEPS.map(([key, label]) => [key, { label, status: "pending", detail: "" }]));
    this.render();
  }

  set(key, status, detail = "") {
    Object.assign(this.items[key], { status, detail });
    this.render();
  }

  render() {
    const ICON = { pending: "minus-circle", current: "hourglass", done: "check-circle", skip: "minus-circle", error: "x-circle" };
    clear(
      this.container,
      h(
        "ol",
        { class: "steps" },
        STEPS.map(([key]) => {
          const item = this.items[key];
          return h(
            "li",
            { class: `is-${item.status}`, "aria-current": item.status === "current" ? "step" : null },
            icon(ICON[item.status]),
            h("span", {}, item.label),
            item.detail ? h("span", { class: "muted small" }, `· ${item.detail}`) : null,
          );
        }),
      ),
    );
  }

  fail(message) {
    const current = Object.entries(this.items).find(([, v]) => v.status === "current");
    if (current) this.set(current[0], "error", message);
  }
}

async function ensureRegistered(steps) {
  steps?.set("register", "current");
  const reg = await guard(register(state.deviceId));
  state.registered = true;
  state.deviceClass = reg.device_class;
  setConn(true, `Đã kết nối · ${reg.device_class}`);
  state.p2p?.start(); // đã đăng ký: nhận tin signaling, làm peer cho máy cùng lớp
  renderDeviceInfo();
  steps?.set("register", "done", reg.device_class);
  return reg;
}

async function verifiedLocal(variantId) {
  const meta = state.local.find((m) => m.variant_id === variantId);
  if (!meta) return null;
  const raw = state.bytes.get(variantId) || (await store.loadBytes(variantId));
  const bytes = raw ? new Uint8Array(raw) : null;
  if (bytes && bytes.byteLength === meta.size_bytes && (await sha256Hex(bytes)) === meta.sha256) {
    return { meta, bytes };
  }
  await store.deleteModel(variantId);
  state.bytes.delete(variantId);
  await refreshLocal();
  toast(`${variantId} trên máy bị hỏng (sai SHA-256), đã xoá để tải lại.`, "warning");
  return null;
}

async function onRequestClick() {
  if (state.busy) return;
  state.busy = true;
  const button = $("request-btn");
  setBusy(button, true, "Đang xin model…");
  $("progress").hidden = false;
  $("reason").hidden = true;
  clear($("download-view"));
  clear($("notes"));
  clear($("suggestion"));
  const steps = new Steps($("steps"));
  try {
    await runRequest(steps, $("preferred").value || null);
  } catch (err) {
    const message = err instanceof ApiError && err.code === "NO_VARIANT_FITS" ? `Không có model nào vừa: ${err.message}` : err.message;
    steps.fail(message);
    toast(message, "danger");
  } finally {
    state.busy = false;
    setBusy(button, false, "Xin model");
  }
}

async function runRequest(steps, preferred) {
  const mode = currentMode();
  const deadline = Number($("deadline").value) > 0 ? Number($("deadline").value) : 60;

  // Máy đã có đúng model được chọn: dùng ngay, không hỏi server (như client.agent)
  if (preferred && !$("refresh").checked) {
    const local = await verifiedLocal(preferred);
    if (local) {
      for (const key of ["register", "probe", "request", "download", "save"]) steps.set(key, "skip", "đã có trên máy");
      steps.set("load", "current");
      await activate(local.bytes, local.meta.variant);
      steps.set("load", "done", fmt.ms(state.detector.loadMs));
      afterReady();
      return;
    }
  }

  await ensureRegistered(steps);

  steps.set("probe", "current");
  const bandwidth = await guard(probe());
  state.bandwidth = bandwidth;
  renderDeviceInfo();
  steps.set("probe", "done", fmt.kbps(bandwidth));

  steps.set("request", "current");
  const ask = async (have) =>
    guard(
      requestModel({
        device_id: state.deviceId,
        bandwidth_kbps: bandwidth,
        deadline_s: deadline,
        free_storage_mb: await store.freeStorageMb(),
        preferred_variant: preferred,
        have_variants: have,
        mode,
        target_fps: mode === "live" ? LIVE_FPS : null,
      }),
    );
  let resp = await ask(state.local.map((m) => m.variant_id));
  let variant = resp.variant;
  if (!webSupported(variant)) {
    throw new Error(`Server chọn ${variant.id} nhưng trình duyệt chưa chạy được model này. Chọn model khác trong Tuỳ chọn nâng cao.`);
  }
  state.requestId = resp.request_id;
  steps.set("request", "done", `${variant.id} · ${fmt.mb(variant.size_bytes)}`);
  renderReason(resp.reason);

  const sameLocal = state.local.some((m) => m.variant_id === variant.id && m.sha256 === variant.sha256);
  const local = sameLocal ? await verifiedLocal(variant.id) : null;
  if (!local && !resp.plan.length) {
    // server nghĩ máy đã có nhưng kho trình duyệt đã mất: xin lại, không khai báo have
    resp = await ask([]);
    variant = resp.variant;
    state.requestId = resp.request_id;
  }

  let bytes;
  if (local) {
    bytes = local.bytes;
    steps.set("download", "skip", "đã có trên máy, không tải lại");
    steps.set("save", "skip");
    await reportHave(state.deviceId, resp, Array.from({ length: variant.chunk_count }, (_, i) => i), true, bandwidth);
    state.metrics = null;
  } else {
    const result = await runDownload(resp, bandwidth, steps);
    bytes = result.bytes;
    steps.set("save", "current");
    try {
      await store.saveModel(
        { variant_id: variant.id, variant, size_bytes: variant.size_bytes, sha256: variant.sha256, saved_at: new Date().toISOString() },
        bytes.buffer,
      );
      steps.set("save", "done", "lần sau mở link dùng ngay");
    } catch {
      steps.set("save", "error", "không lưu được (thiếu dung lượng?), lần sau phải tải lại");
    }
    await refreshLocal();
    state.metrics = {
      request_id: resp.request_id,
      variant_id: variant.id,
      download_ms: result.downloadMs,
      chunks_from_peer: result.fromPeer,
      chunks_from_server: result.fromServer,
    };
  }

  steps.set("load", "current");
  await activate(bytes, variant);
  steps.set("load", "done", `${fmt.ms(state.detector.loadMs)} · ${state.detector.threads} luồng · onnxruntime-web từ ${state.detector.ortSource}`);
  afterReady();
}

// Câu lý do của server: "<chọn ...>; loại A vì ..., B vì .... <câu P2P nếu có>"
// -> câu chính hiện luôn, danh sách model bị loại để trong mục thu gọn
function renderReason(reason) {
  const match = /; loại (.*?)\.(?=\s+\S+ cùng lớp|\s*$)/.exec(reason);
  const main = match ? reason.slice(0, match.index) + reason.slice(match.index + match[0].length) : reason;
  clear(
    $("reason"),
    h("strong", {}, "Lý do server chọn: "),
    fmt.text(main.trim()),
    match
      ? h(
          "details",
          { class: "disclosure section-gap" },
          h("summary", {}, icon("caret-right"), "Vì sao không chọn model khác"),
          h("p", { class: "section-gap" }, fmt.text(`Loại ${match[1]}.`)),
        )
      : null,
  );
  $("reason").hidden = false;
}

async function runDownload(resp, bandwidth, steps) {
  const { variant, plan } = resp;
  const peers = [...new Set(plan.filter((p) => p.source === "peer").map((p) => p.url.split("/")[2]))];
  steps.set("download", "current", `0/${plan.length} miếng${peers.length ? ` · P2P từ ${peers.join(", ")}` : ""}`);
  const bar = h("span", { style: "width:0%" });
  const progress = h("div", { class: "progress", role: "progressbar", "aria-label": "Tiến độ tải model", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, bar);
  const grid = h("div");
  clear($("download-view"), progress, grid);
  let lastDraw = 0;
  const result = await download({
    deviceId: state.deviceId,
    resp,
    bandwidth,
    p2p: state.p2p,
    onProgress: (items, got, notes) => {
      const pct = Math.round((got / items.length) * 100);
      bar.style.width = `${pct}%`;
      progress.setAttribute("aria-valuenow", String(pct));
      if (performance.now() - lastDraw > 120 || got === items.length) {
        lastDraw = performance.now();
        clear(grid, chunkGrid(items, { label: "Miếng model" }));
        steps.set("download", "current", `${got}/${items.length} miếng`);
        clear($("notes"), notes.map((n) => h("li", {}, n)));
      }
    },
  });
  progress.classList.add("is-done");
  if ((await sha256Hex(result.bytes)) !== variant.sha256) {
    throw new Error("Ghép đủ miếng nhưng SHA-256 cả file không khớp Registry. Thử xin lại.");
  }
  const parts = [`${result.fromServer} miếng từ server`];
  if (result.fromPeer) parts.push(`${result.fromPeer} từ máy khác (P2P)`);
  steps.set("download", "done", `${fmt.ms(result.downloadMs)} · ${parts.join(", ")}`);
  const have = await reportHave(state.deviceId, resp, plan.map((p) => p.index), true, bandwidth, result.badChunks);
  if (have?.action === "switch") showSuggestion(have.new_variant_id, have.reason);
  return result;
}

function afterReady() {
  toast(`Đã sẵn sàng: ${state.detector.variantId}. Bật camera để phát hiện vật thể.`);
  goTab("camera");
}

async function activate(bytes, variant) {
  const detector = await Detector.create(bytes, variant);
  state.detector?.dispose();
  state.detector = detector;
  state.bytes.set(variant.id, bytes);
  state.latencies = [];
  lsSet(KEY_ACTIVE, variant.id);
  renderModelStatus();
  renderDetectEnabled();
  renderDeviceInfo();
  announceLocal();
  if (state.photo) await detectPhoto(state.photo);
}

// Báo server các model đang có, để máy cùng loại tải P2P từ máy này
async function announceLocal() {
  if (!state.registered || !state.local.length) return;
  try {
    await announce(state.deviceId, state.local);
  } catch {
    // server cũ hoặc mất kết nối: chỉ mất phần chia sẻ P2P
  }
}

// ---------------------------------------------------------------- gợi ý đổi model

function showSuggestion(variantId, reason) {
  if (!variantId) return;
  const variant = state.variants.find((v) => v.variant_id === variantId) || { id: variantId };
  const usable = webSupported(variant);
  clear(
    $("suggestion"),
    h(
      "div",
      { class: "state is-warning" },
      h("p", { class: "state-title" }, icon("lightning"), `Server đề xuất đổi sang ${variantId}`),
      reason ? h("p", {}, fmt.text(reason)) : null,
      usable
        ? h(
            "button",
            {
              class: "btn btn-secondary btn-sm",
              type: "button",
              onclick: () => {
                $("preferred").value = variantId;
                goTab("model");
                onRequestClick();
              },
            },
            icon("download-simple"),
            "Tải bản đề xuất",
          )
        : h("p", { class: "small muted" }, "Trình duyệt chưa chạy được model này."),
    ),
  );
  toast(`Server đề xuất đổi sang ${variantId} (xem tab Model).`, "warning");
}

async function sendMetrics(boxes) {
  const m = state.metrics;
  if (!m || !state.detector || m.variant_id !== state.detector.variantId) return;
  state.metrics = null;
  const sorted = [...state.latencies].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  const median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  const top = boxes[0];
  try {
    const resp = await guard(
      api("/api/feedback", {
        method: "POST",
        body: {
          device_id: state.deviceId,
          request_id: m.request_id,
          variant_id: m.variant_id,
          download_ms: m.download_ms,
          chunks_from_peer: m.chunks_from_peer,
          chunks_from_server: m.chunks_from_server,
          load_ms: state.detector.loadMs,
          latency_ms_median: median,
          runs: sorted.length,
          peak_ram_mb: null,
          top1_label: top ? top.label : null,
          top1_confidence: top ? top.score : null,
          fps: state.live && state.fps ? state.fps : null,
          detections: boxes.length,
        },
      }),
    );
    if (resp.action === "switch") showSuggestion(resp.new_variant_id, resp.reason);
  } catch {
    // server tắt: bỏ qua số đo, như client.agent
  }
}

// ---------------------------------------------------------------- nhận diện

const threshold = () => Number($("threshold").value);
const visible = (boxes) => (state.onlyClass === "" ? boxes : boxes.filter((b) => String(b.cls) === state.onlyClass));

function drawBoxes(ctx, width, height, boxes) {
  // canvas theo pixel ảnh gốc nhưng hiện thu nhỏ theo bề ngang màn hình: nét và chữ tính theo
  // cỡ hiển thị để vẫn đọc được trên điện thoại (chữ ~14 px, nét ~3 px trên màn)
  const shown = $("stage").clientWidth || width;
  const scale = Math.max(1, width / shown);
  const line = Math.max(2, Math.round(3 * scale));
  const fontPx = Math.round(Math.max(14 * scale, Math.max(width, height) / 42));
  ctx.font = `600 ${fontPx}px "Fira Sans", system-ui, sans-serif`;
  ctx.textBaseline = "top";
  for (const b of boxes) {
    const color = BOX_COLORS[b.cls % BOX_COLORS.length];
    ctx.lineWidth = line;
    ctx.strokeStyle = color;
    ctx.strokeRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
    const text = `${b.labelVi} ${Math.round(b.score * 100)}%`;
    const tw = ctx.measureText(text).width + fontPx * 0.6;
    const th = fontPx * 1.35;
    const ty = b.y1 - th >= 0 ? b.y1 - th : b.y1;
    ctx.fillStyle = color;
    ctx.fillRect(b.x1 - line / 2, ty, tw, th);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(text, b.x1 + fontPx * 0.3 - line / 2, ty + fontPx * 0.18);
  }
}

function drawFrame(background, width, height, boxes) {
  const canvas = $("view");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  if (background) ctx.drawImage(background, 0, 0, width, height);
  drawBoxes(ctx, width, height, boxes);
  $("stage").classList.remove("is-empty");
}

function summaryText(boxes) {
  if (!boxes.length) return "Không thấy vật thể nào trên ngưỡng tin cậy.";
  const counts = new Map();
  for (const b of boxes) counts.set(b.labelVi, (counts.get(b.labelVi) || 0) + 1);
  return `${boxes.length} vật thể: ${[...counts].map(([name, n]) => (n > 1 ? `${name} ×${n}` : name)).join(", ")}`;
}

function renderResults(result, boxes, mode) {
  const parts = [state.detector.variantId, `${fmt.num(result.inferMs, 0)} ms`];
  if (mode === "live" && state.fps) parts.push(`${fmt.num(state.fps)} khung/giây`);
  $("det-stats").textContent = parts.join(" · ");
  const summary = summaryText(boxes);
  // camera: chỉ cập nhật khi nội dung đổi, tránh trình đọc màn hình đọc liên tục
  if ($("det-summary").textContent === summary) return;
  $("det-summary").textContent = summary;
  clear(
    $("det-list"),
    boxes.slice(0, 12).map((b) =>
      h(
        "li",
        {},
        h("span", { class: "swatch", style: `background:${BOX_COLORS[b.cls % BOX_COLORS.length]}` }),
        h("span", {}, b.labelVi),
        h("span", { class: "muted small" }, b.label),
        h("span", { class: "num" }, fmt.pct(b.score)),
      ),
    ),
  );
}

function afterDetect(result, mode, source, width, height) {
  const boxes = visible(result.boxes);
  state.last = { boxes, at: new Date().toISOString(), source, width, height, mode };
  if (state.metrics && state.latencies.length < METRIC_RUNS) state.latencies.push(result.inferMs);
  renderResults(result, boxes, mode);
  $("freeze-btn").disabled = false;
  if (state.metrics && (state.latencies.length >= METRIC_RUNS || mode === "photo")) sendMetrics(boxes);
  return boxes;
}

async function liveLoop() {
  if (!state.live) return;
  const video = $("video");
  if (video.readyState >= 2 && video.videoWidth) {
    try {
      const w = video.videoWidth;
      const hgt = video.videoHeight;
      const result = await state.detector.detect(video, w, hgt, threshold());
      if (!state.live) return;
      const now = performance.now();
      if (state.lastFrameAt) {
        const fps = 1000 / (now - state.lastFrameAt);
        state.fps = state.fps ? state.fps * 0.8 + fps * 0.2 : fps;
      }
      state.lastFrameAt = now;
      const boxes = afterDetect(result, "live", video, w, hgt);
      drawFrame(null, w, hgt, boxes);
    } catch (err) {
      stopCamera();
      toast(`Lỗi khi nhận diện: ${err.message}`, "danger");
      return;
    }
  }
  requestAnimationFrame(liveLoop);
}

async function startCamera() {
  if (!state.detector) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
    toast("Trang này không được dùng camera trực tiếp (cần HTTPS). Dùng nút Chụp ảnh.", "warning");
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: state.facing }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  } catch (err) {
    const denied = err.name === "NotAllowedError";
    toast(denied ? "Bạn chưa cho phép dùng camera. Mở quyền camera cho trang này trong cài đặt trình duyệt." : `Không mở được camera: ${err.message}`, "danger");
    return;
  }
  const video = $("video");
  video.srcObject = state.stream;
  await video.play().catch(() => {});
  Object.assign(state, { live: true, photo: null, fps: null, lastFrameAt: 0 });
  $("stage").classList.add("is-live");
  renderCameraButtons();
  requestAnimationFrame(liveLoop);
}

function stopCamera() {
  if (!state.live && !state.stream) return;
  state.live = false;
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  $("video").srcObject = null;
  $("stage").classList.remove("is-live");
  if (!state.photo) $("stage").classList.add("is-empty");
  $("det-stats").textContent = "";
  renderCameraButtons();
}

async function flipCamera() {
  state.facing = state.facing === "environment" ? "user" : "environment";
  stopCamera();
  await startCamera();
}

async function loadPhoto(file) {
  if (!file || !state.detector) return;
  if (!file.type.startsWith("image/")) {
    toast("File không phải ảnh.", "warning");
    return;
  }
  stopCamera();
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    state.photo = canvas;
    await detectPhoto(canvas);
  } catch (err) {
    toast(`Không đọc được ảnh: ${err.message}`, "danger");
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function detectPhoto(canvas) {
  $("det-stats").textContent = "Đang nhận diện…";
  const result = await state.detector.detect(canvas, canvas.width, canvas.height, threshold());
  const boxes = afterDetect(result, "photo", canvas, canvas.width, canvas.height);
  drawFrame(canvas, canvas.width, canvas.height, boxes);
}

// Chụp khung hiện tại (camera hoặc ảnh) kèm hộp sang tab Đánh giá
function freezeFrame() {
  const last = state.last;
  if (!last) return;
  const canvas = $("snapshot");
  canvas.width = last.width;
  canvas.height = last.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(last.source, 0, 0, last.width, last.height);
  drawBoxes(ctx, last.width, last.height, last.boxes);
  state.frozen = { ...last, source: null };
  $("snap-summary").textContent = `${state.detector.variantId} · ${summaryText(last.boxes)}`;
  $("snap").hidden = false;
  $("snap-empty").hidden = true;
  $("feedback-form").hidden = false;
  clear($("feedback-result"));
  goTab("feedback");
}

// ---------------------------------------------------------------- đánh giá

async function sendFeedback(event) {
  event.preventDefault();
  const frozen = state.frozen;
  if (!state.detector || !frozen) return;
  const data = new FormData(event.target);
  const rating = Number(data.get("rating"));
  const correct = data.get("correct");
  if (!rating || !correct) {
    toast("Chọn điểm và cho biết model nhận đúng hay sai.", "warning");
    return;
  }
  const boxes = frozen.boxes;
  const prediction = boxes.length ? boxes.slice(0, 5).map((b) => `${b.label} ${Math.round(b.score * 100)}%`).join(", ").slice(0, 200) : null;
  const text = (name) => String(data.get(name) || "").trim() || null;
  const button = $("feedback-btn");
  setBusy(button, true, "Đang gửi…");
  try {
    const resp = await guard(
      api("/api/feedback", {
        method: "POST",
        body: {
          feedback_id: `fb-${state.deviceId}-${Date.now().toString(36)}`,
          device_id: state.deviceId,
          variant_id: state.detector.variantId,
          rating,
          correct: correct === "yes",
          prediction,
          confidence: boxes.length ? boxes[0].score : null,
          expected_label: text("expected")?.slice(0, 200) ?? null,
          comment: text("comment")?.slice(0, 1000) ?? null,
          request_id: state.requestId,
          detected_at: frozen.at,
          created_at: new Date().toISOString(),
        },
      }),
    );
    const ev = resp.evaluation;
    const [kind, label] = VERDICT[ev.verdict] || ["neutral", ev.verdict];
    clear(
      $("feedback-result"),
      h("p", { class: "row" }, badge("success", "Server đã nhận"), badge(kind, `Đánh giá: ${label}`)),
      h("p", { class: "small muted" }, fmt.text(ev.reason)),
    );
    event.target.reset();
  } catch (err) {
    toast(`Chưa gửi được đánh giá: ${err.message}`, "danger");
  } finally {
    setBusy(button, false, "Gửi đánh giá");
  }
}

// ---------------------------------------------------------------- hiển thị

function renderDeviceInfo() {
  const info = deviceInfo();
  const d = state.detector;
  const rows = [
    ["Mã thiết bị", h("span", { class: "mono" }, state.deviceId)],
    ["Lớp thiết bị", state.deviceClass ? h("span", { class: "mono" }, state.deviceClass) : "chưa đăng ký"],
    ["Loại máy", `${info.platform === "web_desktop" ? "Máy tính" : "Điện thoại"} · ${info.cpu_arch} · RAM ~${fmt.int(info.ram_mb / 1024)} GB`],
    ["Chạy model", `onnxruntime-web · ${d ? d.threads : info.threads} luồng${info.threads > 1 ? "" : " (mở bản HTTPS để chạy đa luồng)"}`],
    ["Băng thông", state.bandwidth ? fmt.kbps(state.bandwidth) : "đo khi xin model"],
    ["Camera trực tiếp", info.camera ? badge("success", "Dùng được") : badge("warning", "Cần mở bản HTTPS")],
    ["Chia sẻ P2P", info.webrtc ? "WebRTC với máy cùng lớp khi trang đang mở" : "Trình duyệt không hỗ trợ WebRTC"],
  ];
  clear($("device-info"), rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function renderModelStatus() {
  const d = state.detector;
  clear(
    $("model-status"),
    d
      ? h("p", { class: "row" }, badge("success", `Đang dùng ${d.variantId}`, "cube"), h("span", { class: "muted small" }, `nạp ${fmt.ms(d.loadMs)}`))
      : h("p", { class: "row" }, badge("neutral", "Máy chưa có model"), h("span", { class: "muted small" }, "Bấm Xin model để server chọn và gửi model phù hợp.")),
  );
  if (!state.local.length) {
    clear($("local-list"), h("p", { class: "muted" }, "Chưa có model nào."));
    return;
  }
  clear(
    $("local-list"),
    h(
      "ul",
      { class: "model-list" },
      state.local.map((m) =>
        h(
          "li",
          {},
          h("span", { class: "mono" }, m.variant_id),
          h("span", { class: "muted small" }, fmt.mb(m.size_bytes)),
          d?.variantId === m.variant_id
            ? badge("success", "đang dùng")
            : h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => useLocal(m.variant_id) }, "Dùng"),
          h(
            "button",
            {
              class: "btn btn-ghost btn-sm",
              type: "button",
              "aria-label": `Xoá ${m.variant_id} khỏi máy`,
              onclick: async () => {
                await store.deleteModel(m.variant_id);
                state.bytes.delete(m.variant_id);
                if (lsGet(KEY_ACTIVE) === m.variant_id) lsSet(KEY_ACTIVE, null);
                await refreshLocal();
              },
            },
            icon("x"),
            "Xoá",
          ),
        ),
      ),
    ),
  );
}

function renderVariantOptions() {
  const select = $("preferred");
  const current = select.value;
  const localIds = new Set(state.local.map((m) => m.variant_id));
  clear(
    select,
    h("option", { value: "" }, "Để server tự chọn theo cấu hình và băng thông"),
    state.variants.map((v) => {
      const usable = webSupported(v);
      const notes = [fmt.mb(v.size_bytes)];
      if (v.map50_95 != null) notes.push(`mAP ${fmt.pct(v.map50_95)}`);
      if (localIds.has(v.variant_id)) notes.push("đã có trên máy");
      if (!usable) notes.push("chỉ client Python");
      return h("option", { value: v.variant_id, disabled: !usable }, `${v.variant_id} · ${notes.join(" · ")}`);
    }),
  );
  if ([...select.options].some((o) => o.value === current && !o.disabled)) select.value = current;
  renderPreferredHint();
}

function renderPreferredHint() {
  const v = state.variants.find((x) => x.variant_id === $("preferred").value);
  $("preferred-hint").textContent =
    v && v.size_bytes > BIG_MODEL_BYTES
      ? `Model ${fmt.mb(v.size_bytes)}: tải lâu và có thể làm trình duyệt hết bộ nhớ.`
      : "Chọn sẵn một model: máy đã có thì dùng ngay, chưa có thì xin đúng model đó (bỏ qua bộ chọn tự động).";
}

function renderDetectEnabled() {
  const ready = !!state.detector;
  $("detect-empty").hidden = ready;
  $("detect-tools").hidden = !ready;
  for (const id of ["photo", "pick"]) $(id).disabled = !ready;
  renderCameraButtons();
}

function renderCameraButtons() {
  const btn = $("camera-btn");
  btn.querySelector("span").textContent = state.live ? "Tắt camera" : "Bật camera";
  btn.classList.toggle("btn-primary", !state.live);
  btn.classList.toggle("btn-secondary", state.live);
  $("flip-btn").hidden = !state.live || !deviceInfo().mobile;
}

function renderShare(status) {
  if (!status || (!status.connected && !status.served)) {
    clear($("share"));
    return;
  }
  clear($("share"), badge("info", `Chia sẻ P2P: ${status.peers} máy · ${status.served} miếng`, "share-network"));
}

async function renderHttpsHint() {
  if (window.isSecureContext) return;
  $("camera-btn").hidden = true;
  let httpsUrl = null;
  try {
    const links = await api("links.json");
    if (links.https_port) httpsUrl = `https://${location.hostname}:${links.https_port}${location.pathname}${location.search}`;
  } catch {
    // server không chạy bằng server.serve: không có cổng HTTPS
  }
  clear(
    $("https-hint"),
    h(
      "div",
      { class: "card" },
      h(
        "div",
        { class: "state is-warning" },
        h("p", { class: "state-title" }, icon("camera"), "Camera trực tiếp cần kết nối HTTPS"),
        h("p", {}, "Trang đang mở qua HTTP nên trình duyệt chỉ cho chụp từng ảnh. Nút Chụp ảnh / Chọn ảnh vẫn dùng được."),
        httpsUrl
          ? [
              h("a", { class: "btn btn-primary btn-sm", href: httpsUrl }, icon("camera"), "Mở bản HTTPS (camera trực tiếp)"),
              h("p", { class: "small muted" }, "Lần đầu trình duyệt báo kết nối không riêng tư vì server dùng chứng chỉ tự ký: chọn Nâng cao → Tiếp tục. Bản HTTPS lưu model riêng nên sẽ tải lại một lần."),
            ]
          : h("p", { class: "small muted" }, "Người chạy server bật bằng lệnh python -m server.serve để có link HTTPS."),
      ),
    ),
  );
  $("https-hint").hidden = false;
}

function renderClassFilter() {
  const select = $("only-class");
  const options = COCO_LABELS_VI.map((name, i) => [name, i]).sort((a, b) => a[0].localeCompare(b[0], "vi"));
  clear(select, h("option", { value: "" }, "Mọi vật thể"), options.map(([name, i]) => h("option", { value: String(i) }, name)));
}

// ---------------------------------------------------------------- khởi động

async function refreshLocal() {
  try {
    state.local = await store.listModels();
  } catch {
    state.local = [];
  }
  renderModelStatus();
  renderVariantOptions();
}

async function useLocal(variantId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const local = await verifiedLocal(variantId);
    if (!local) return;
    if (!webSupported(local.meta.variant)) throw new Error(`Trình duyệt chưa chạy được ${variantId}.`);
    await activate(local.bytes, local.meta.variant);
    state.metrics = null;
    toast(`Dùng ${variantId} có sẵn trên máy.`);
  } catch (err) {
    toast(`Không nạp được ${variantId}: ${err.message}`, "danger");
  } finally {
    state.busy = false;
  }
}

async function loadVariants() {
  try {
    const data = await guard(api("/admin-api/variants"));
    state.variants = data.variants.filter((v) => v.task_id === "object_detection" && v.distributable);
    renderVariantOptions();
  } catch {
    // vẫn xin model được, chỉ thiếu danh sách chọn tay
  }
}

async function getBytesForPeer(variantId) {
  if (state.bytes.has(variantId)) return state.bytes.get(variantId);
  const raw = await store.loadBytes(variantId);
  if (!raw) return null;
  const bytes = new Uint8Array(raw);
  state.bytes.set(variantId, bytes);
  return bytes;
}

function bindEvents() {
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  $("request-btn").addEventListener("click", onRequestClick);
  $("preferred").addEventListener("change", renderPreferredHint);
  for (const radio of document.querySelectorAll('input[name="mode"]')) {
    radio.addEventListener("change", () => {
      lsSet(KEY_MODE, currentMode());
      $("mode-hint").textContent = MODE_HINT[currentMode()];
    });
  }
  $("camera-btn").addEventListener("click", () => (state.live ? stopCamera() : startCamera()));
  $("flip-btn").addEventListener("click", flipCamera);
  $("freeze-btn").addEventListener("click", freezeFrame);
  for (const id of ["photo", "pick"]) {
    $(id).addEventListener("change", (e) => {
      loadPhoto(e.target.files[0]);
      e.target.value = "";
    });
  }
  $("threshold").addEventListener("input", () => {
    $("threshold-value").textContent = fmt.pct(threshold());
  });
  $("threshold").addEventListener("change", () => {
    if (state.photo && !state.live) detectPhoto(state.photo);
  });
  $("only-class").addEventListener("change", (e) => {
    state.onlyClass = e.target.value;
    if (state.photo && !state.live) detectPhoto(state.photo);
  });
  $("feedback-form").addEventListener("submit", sendFeedback);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopCamera();
  });
  window.addEventListener("pagehide", () => state.p2p?.goodbye());
}

async function init() {
  state.deviceId = initDeviceId();
  $("device-id").textContent = state.deviceId;
  document.title = `${state.deviceId} · PBL4 Phát hiện vật thể`;
  const savedMode = lsGet(KEY_MODE);
  const modeRadio = document.querySelector(`input[name="mode"][value="${savedMode}"]`);
  if (modeRadio) modeRadio.checked = true;
  $("mode-hint").textContent = MODE_HINT[currentMode()];
  $("threshold-value").textContent = fmt.pct(threshold());
  bindEvents();
  renderClassFilter();
  renderDeviceInfo();
  renderDetectEnabled();
  showTab(location.hash.slice(1));
  renderHttpsHint();
  await refreshLocal();
  loadVariants();

  try {
    await ensureRegistered(null);
  } catch {
    setConn(false);
  }
  if (deviceInfo().webrtc) {
    state.p2p = new P2P({ deviceId: state.deviceId, getBytes: getBytesForPeer, onChange: renderShare });
    if (state.registered) state.p2p.start();
  }

  // Lần trước đã dùng model nào thì nạp lại ngay từ kho trên máy (không cần server)
  const active = lsGet(KEY_ACTIVE);
  if (active && state.local.some((m) => m.variant_id === active)) await useLocal(active);
  else announceLocal();
}

init();
