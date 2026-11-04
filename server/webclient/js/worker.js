// Web Worker chạy model phát hiện vật thể bằng onnxruntime-web (WASM), tách khỏi luồng giao diện
// để camera và nút bấm không bị giật. Trang gửi điểm ảnh đã thu nhỏ (RGBA) về đúng kích thước
// model cần; worker dựng tensor theo khối `detection`, chạy model rồi giải mã ra hộp vật thể.
//
// Tin nhắn: {type:"load", bytes, variant} -> {type:"loaded", loadMs, ortSource, threads}
//           {type:"detect", id, rgba, width, height, meta, threshold} -> {type:"result", id, boxes, inferMs}
// Lỗi: {type:"error", id?, message}.

import { COCO91_TO_80, COCO_LABELS, COCO_LABELS_VI, detectionSpec } from "./labels.js";

const ORT_VERSION = "1.20.1"; // phải khớp ORT_WEB_VERSION trong tools/download_assets.py
const LOCAL_ORT = new URL("../vendor/onnxruntime-web/", import.meta.url).href;
const CDN_ORT = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/`;
const YOLOX_STRIDES = [8, 16, 32];
const NMS_IOU = 0.45;
const MAX_BOXES = 100;
const MAX_THREADS = 4;

let ort = null;
let ortSource = "";
let session = null;
let spec = null;
let normalize = null;

// Ưu tiên bản trong repo (tools/download_assets.py) để điện thoại không cần Internet
async function loadOrt() {
  if (ort) return;
  for (const [base, source] of [[LOCAL_ORT, "server"], [CDN_ORT, "CDN"]]) {
    try {
      ort = await import(`${base}ort.wasm.min.mjs`);
      ort.env.wasm.wasmPaths = base;
      // Đa luồng cần trang "cross-origin isolated" (HTTPS + header COOP/COEP của /app)
      ort.env.wasm.numThreads = self.crossOriginIsolated
        ? Math.max(1, Math.min(MAX_THREADS, navigator.hardwareConcurrency || 1))
        : 1;
      ortSource = source;
      return;
    } catch {
      ort = null; // thử nguồn tiếp theo
    }
  }
  throw new Error(
    "Không tải được onnxruntime-web: server chưa có bản cục bộ (chạy tools/download_assets.py) và máy không vào được Internet.",
  );
}

// ---------------------------------------------------------------- tensor đầu vào

function channelIndex(color) {
  // vị trí kênh trong RGBA cho từng kênh đầu ra
  return color === "BGR" ? [2, 1, 0] : [0, 1, 2];
}

function buildTensor(rgba, width, height, meta) {
  const fmt = spec.format;
  if (fmt === "ssd_tf") {
    const input = new Uint8Array(width * height * 3);
    for (let i = 0, j = 0; i < width * height; i++) {
      input[j++] = rgba[i * 4];
      input[j++] = rgba[i * 4 + 1];
      input[j++] = rgba[i * 4 + 2];
    }
    return new ort.Tensor("uint8", input, [1, height, width, 3]);
  }

  const order = channelIndex(spec.color || "BGR");
  const mean = normalize?.mean || [0, 0, 0];
  const std = normalize?.std || [1 / 255, 1 / 255, 1 / 255];
  const div = spec.size_divisor || 1;
  const W = Math.ceil(width / div) * div;
  const H = Math.ceil(height / div) * div;
  const plane = W * H;
  const input = new Float32Array(3 * plane); // phần đệm = 0 sau chuẩn hoá
  // Letterbox có pad_value: canvas đã tô sẵn màu đệm, chuẩn hoá cả khung.
  // Không có pad_value (đệm 0 sau chuẩn hoá): chỉ chuẩn hoá vùng ảnh thật.
  const validW = spec.pad_value == null && meta.validW ? meta.validW : width;
  const validH = spec.pad_value == null && meta.validH ? meta.validH : height;
  for (let c = 0; c < 3; c++) {
    const src = order[c];
    const m = mean[c];
    const s = std[c];
    const base = c * plane;
    for (let y = 0; y < validH; y++) {
      for (let x = 0; x < validW; x++) {
        input[base + y * W + x] = (rgba[(y * width + x) * 4 + src] / 255 - m) / s;
      }
    }
  }
  const dims = (spec.layout || "NCHW") === "CHW" ? [3, H, W] : [1, 3, H, W];
  return new ort.Tensor("float32", input, dims);
}

// ---------------------------------------------------------------- giải mã

function iou(a, b) {
  const w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
  const h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
  if (w <= 0 || h <= 0) return 0;
  const inter = w * h;
  return inter / ((a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter);
}

function nms(boxes) {
  boxes.sort((a, b) => b.score - a.score);
  const kept = [];
  for (const box of boxes) {
    if (kept.every((k) => k.cls !== box.cls || iou(k, box) < NMS_IOU)) kept.push(box);
    if (kept.length >= MAX_BOXES) break;
  }
  return kept;
}

function makeBox(x1, y1, x2, y2, score, cls, meta) {
  return {
    x1: Math.max(0, x1),
    y1: Math.max(0, y1),
    x2: Math.min(meta.srcW, x2),
    y2: Math.min(meta.srcH, y2),
    score,
    cls,
    label: COCO_LABELS[cls] ?? `class_${cls}`,
    labelVi: COCO_LABELS_VI[cls] ?? `lớp ${cls}`,
  };
}

function decodeYolox(outputs, meta, threshold) {
  const out = outputs[session.outputNames[0]];
  const o = out.data;
  const C = out.dims[2];
  const [H, W] = spec.input_size;
  const r = meta.r;
  const found = [];
  let anchor = 0;
  for (const stride of YOLOX_STRIDES) {
    const gh = H / stride;
    const gw = W / stride;
    for (let gy = 0; gy < gh; gy++) {
      for (let gx = 0; gx < gw; gx++, anchor++) {
        const base = anchor * C;
        const obj = o[base + 4];
        if (obj < threshold) continue;
        let cls = 0;
        for (let c = 1; c < C - 5; c++) if (o[base + 5 + c] > o[base + 5 + cls]) cls = c;
        const score = obj * o[base + 5 + cls];
        if (score < threshold) continue;
        const cx = (o[base] + gx) * stride;
        const cy = (o[base + 1] + gy) * stride;
        const w = Math.exp(o[base + 2]) * stride;
        const h = Math.exp(o[base + 3]) * stride;
        found.push(makeBox((cx - w / 2) / r, (cy - h / 2) / r, (cx + w / 2) / r, (cy + h / 2) / r, score, cls, meta));
      }
    }
  }
  return nms(found);
}

function decodeSsd(outputs, meta, threshold) {
  const boxes = outputs.detection_boxes.data;
  const classes = outputs.detection_classes.data;
  const scores = outputs.detection_scores.data;
  const count = Math.round(outputs.num_detections.data[0]);
  const found = [];
  for (let i = 0; i < count; i++) {
    const cls = COCO91_TO_80[Math.round(classes[i])] ?? -1;
    if (scores[i] < threshold || cls < 0) continue;
    const y1 = boxes[i * 4];
    const x1 = boxes[i * 4 + 1];
    const y2 = boxes[i * 4 + 2];
    const x2 = boxes[i * 4 + 3];
    found.push(makeBox(x1 * meta.srcW, y1 * meta.srcH, x2 * meta.srcW, y2 * meta.srcH, scores[i], cls, meta));
  }
  return nms(found);
}

function decodeFasterRcnn(outputs, meta, threshold) {
  const [boxesName, labelsName, scoresName] = session.outputNames;
  const boxes = outputs[boxesName].data;
  const labels = outputs[labelsName].data; // BigInt64Array
  const scores = outputs[scoresName].data;
  const offset = spec.class_offset ?? 1;
  const found = [];
  for (let i = 0; i < scores.length; i++) {
    if (scores[i] < threshold) continue;
    const s = meta.scale;
    found.push(makeBox(boxes[i * 4] / s, boxes[i * 4 + 1] / s, boxes[i * 4 + 2] / s, boxes[i * 4 + 3] / s, scores[i], Number(labels[i]) - offset, meta));
  }
  return nms(found);
}

const DECODERS = { yolox: decodeYolox, ssd_tf: decodeSsd, faster_rcnn: decodeFasterRcnn };

// ---------------------------------------------------------------- tin nhắn

async function load(bytes, variant) {
  await loadOrt();
  spec = detectionSpec(variant);
  if (!spec || !DECODERS[spec.format]) throw new Error(`Trình duyệt chưa chạy được ${variant.id}.`);
  normalize = variant.normalize || null;
  const start = performance.now();
  try {
    await session?.release();
  } catch {
    // phiên cũ đã giải phóng
  }
  session = await ort.InferenceSession.create(new Uint8Array(bytes), {
    executionProviders: ["wasm"],
    graphOptimizationLevel: "all",
  });
  return { loadMs: performance.now() - start, ortSource, threads: ort.env.wasm.numThreads };
}

async function detect({ rgba, width, height, meta, threshold }) {
  const feeds = { [session.inputNames[0]]: buildTensor(new Uint8ClampedArray(rgba), width, height, meta) };
  const start = performance.now();
  const outputs = await session.run(feeds);
  const inferMs = performance.now() - start;
  return { boxes: DECODERS[spec.format](outputs, meta, threshold), inferMs };
}

self.onmessage = async (event) => {
  const msg = event.data;
  try {
    if (msg.type === "load") self.postMessage({ type: "loaded", ...(await load(msg.bytes, msg.variant)) });
    else if (msg.type === "detect") self.postMessage({ type: "result", id: msg.id, ...(await detect(msg)) });
  } catch (err) {
    self.postMessage({ type: "error", id: msg.id, message: err?.message || String(err) });
  }
};
