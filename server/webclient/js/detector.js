// Phía trang của bộ phát hiện vật thể: thu nhỏ khung hình (camera/ảnh) về đúng kích thước model
// cần bằng canvas, gửi điểm ảnh sang Web Worker (worker.js) chạy onnxruntime-web, nhận lại hộp.
//
// | resize       | Model                 | Cách đưa ảnh vào                                    |
// |--------------|-----------------------|-----------------------------------------------------|
// | letterbox    | YOLOX                 | thu theo cạnh dài, đặt góc trên trái, tô màu đệm    |
// | stretch      | SSD MobileNet         | kéo giãn về input_size                              |
// | keep_ratio   | Faster R-CNN          | cạnh ngắn = min_side, cạnh dài tối đa max_side      |

import { detectionSpec } from "./labels.js";

export const ORT_VERSION = "1.20.1";

export class Detector {
  static async create(bytes, variant) {
    const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
    const detector = new Detector(worker, variant);
    // bản sao: byte gốc giữ lại để chia sẻ P2P cho máy khác
    const copy = bytes.slice(0).buffer;
    const loaded = await detector.call({ type: "load", bytes: copy, variant }, [copy], "loaded");
    Object.assign(detector, { loadMs: loaded.loadMs, ortSource: loaded.ortSource, threads: loaded.threads });
    // Lượt chạy đầu của WASM chậm hơn hẳn (cấp phát, biên dịch kernel): chạy thử một khung trống
    // để số đo gửi server (latency, FPS) phản ánh tốc độ thật, như warm-up của client Python
    const blank = document.createElement("canvas");
    blank.width = 320;
    blank.height = 240;
    const warm = await detector.detect(blank, blank.width, blank.height, 0.99);
    detector.warmupMs = warm.totalMs;
    return detector;
  }

  constructor(worker, variant) {
    this.worker = worker;
    this.variant = variant;
    this.variantId = variant.id;
    this.spec = detectionSpec(variant);
    this.pending = new Map();
    this.nextId = 1;
    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: true });
    worker.onmessage = (event) => {
      const msg = event.data;
      const key = msg.id ?? msg.type;
      const waiter = this.pending.get(key) ?? (msg.type === "error" ? this.pending.get("loaded") : null);
      if (!waiter) return;
      this.pending.delete(msg.id ?? waiter.key);
      if (msg.type === "error") waiter.reject(new Error(msg.message));
      else waiter.resolve(msg);
    };
    worker.onerror = (event) => {
      for (const waiter of this.pending.values()) waiter.reject(new Error(event.message || "Worker lỗi"));
      this.pending.clear();
    };
  }

  call(message, transfer, key) {
    return new Promise((resolve, reject) => {
      this.pending.set(key, { resolve, reject, key });
      this.worker.postMessage(message, transfer);
    });
  }

  // Vẽ source vào canvas theo cách tiền xử lý của model; trả điểm ảnh + thông tin đổi toạ độ
  prepare(source, srcW, srcH) {
    const spec = this.spec;
    let width;
    let height;
    let drawW;
    let drawH;
    const meta = { srcW, srcH };
    if (spec.resize === "keep_ratio") {
      const scale = Math.min(spec.min_side / Math.min(srcW, srcH), spec.max_side / Math.max(srcW, srcH));
      width = drawW = Math.round(srcW * scale);
      height = drawH = Math.round(srcH * scale);
      meta.scale = scale;
    } else if (spec.resize === "stretch") {
      [height, width] = spec.input_size;
      drawW = width;
      drawH = height;
    } else {
      [height, width] = spec.input_size;
      const r = Math.min(width / srcW, height / srcH);
      drawW = Math.round(srcW * r);
      drawH = Math.round(srcH * r);
      Object.assign(meta, { r, validW: drawW, validH: drawH });
    }
    this.canvas.width = width;
    this.canvas.height = height;
    const pad = spec.pad_value;
    this.ctx.fillStyle = pad == null ? "#000" : `rgb(${pad},${pad},${pad})`;
    this.ctx.fillRect(0, 0, width, height);
    this.ctx.drawImage(source, 0, 0, drawW, drawH);
    const rgba = this.ctx.getImageData(0, 0, width, height).data;
    return { rgba, width, height, meta };
  }

  // Trả {boxes, inferMs, totalMs}; toạ độ hộp theo pixel của source
  async detect(source, srcW, srcH, threshold = 0.4) {
    const start = performance.now();
    const { rgba, width, height, meta } = this.prepare(source, srcW, srcH);
    const id = this.nextId++;
    const buffer = rgba.buffer;
    const result = await this.call({ type: "detect", id, rgba: buffer, width, height, meta, threshold }, [buffer], id);
    return { boxes: result.boxes, inferMs: result.inferMs, totalMs: performance.now() - start };
  }

  dispose() {
    this.worker.terminate();
    for (const waiter of this.pending.values()) waiter.reject(new Error("Đã đổi model"));
    this.pending.clear();
  }
}
