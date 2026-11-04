// Các bước Protocol 1.0 của client (như client.agent) và tải model theo plan: tối đa 4 miếng
// song song, kiểm SHA-256 từng miếng, miếng peer lấy qua WebRTC, lỗi thì lấy lại từ server.
import { ApiError, api } from "/ui/shared/js/ui.js";
import { ORT_VERSION } from "./detector.js";
import { sha256Hex } from "./sha256.js";

const PARALLEL = 4;
const SERVER_ATTEMPTS = 3;
const CHUNK_TIMEOUT_MS = 60000;
const HAVE_INTERVAL_MS = 1500;
const PROBE_KB = 256;
const MAX_THREADS = 4;

// ---------------------------------------------------------------- thiết bị

export function deviceInfo() {
  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const android = /Android/.test(ua);
  const mobile = ios || android;
  // web_desktop tách lớp với client Python "windows"/"linux": hai loại P2P khác nhau
  const platform = ios ? "ios_browser" : android ? "android_browser" : "web_desktop";
  const cpuArch = mobile || /arm|aarch64/i.test(navigator.platform || "") ? "arm64" : "x86_64";
  // navigator.deviceMemory chỉ có trên Chrome (GB, tối đa 8); Safari không cho biết RAM
  const ramMb = navigator.deviceMemory ? Math.round(navigator.deviceMemory * 1024) : mobile ? 4096 : 8192;
  const threads = self.crossOriginIsolated ? Math.max(1, Math.min(MAX_THREADS, navigator.hardwareConcurrency || 1)) : 1;
  return {
    platform,
    cpu_arch: cpuArch,
    ram_mb: ramMb,
    runtime: `onnxruntime-web ${ORT_VERSION}`,
    threads,
    camera: !!navigator.mediaDevices?.getUserMedia && window.isSecureContext,
    webrtc: typeof RTCPeerConnection !== "undefined",
    mobile,
  };
}

export async function register(deviceId, displayName) {
  const info = deviceInfo();
  return api("/api/register", {
    method: "POST",
    body: {
      protocol_version: "1.0",
      device_id: deviceId,
      platform: info.platform,
      cpu_arch: info.cpu_arch,
      ram_mb: info.ram_mb,
      runtime: info.runtime,
      peer_port: null, // trình duyệt không mở cổng; chia sẻ qua WebRTC
      display_name: displayName,
      capabilities: {
        threads: info.threads,
        webgpu: !!navigator.gpu,
        camera: info.camera,
        p2p: info.webrtc ? "webrtc" : "none",
      },
    },
  });
}

export async function probe() {
  const start = performance.now();
  const resp = await fetch(`/api/probe?size_kb=${PROBE_KB}`, { cache: "no-store" });
  if (!resp.ok) throw new ApiError("http", `Đo băng thông lỗi HTTP ${resp.status}.`, resp.status);
  const bytes = (await resp.arrayBuffer()).byteLength;
  const seconds = Math.max((performance.now() - start) / 1000, 0.001);
  return Math.round((bytes * 8) / 1000 / seconds);
}

export function requestModel(body) {
  return api("/api/request", { method: "POST", timeoutMs: 15000, body: { task: "object_detection", ...body } });
}

export async function announce(deviceId, models) {
  if (!models.length) return null;
  return api("/api/announce", {
    method: "POST",
    body: { device_id: deviceId, variants: models.map((m) => ({ variant_id: m.variant_id, sha256: m.sha256 })) },
  });
}

export async function reportHave(deviceId, resp, chunks, complete, bandwidth, badChunks = []) {
  try {
    return await api("/api/have", {
      method: "POST",
      body: {
        device_id: deviceId,
        request_id: resp.request_id,
        variant_id: resp.variant.id,
        chunks: [...chunks].sort((a, b) => a - b),
        complete,
        bandwidth_kbps: bandwidth || null,
        bad_chunks: badChunks,
      },
    });
  } catch {
    return null; // báo tiến độ lỗi không làm hỏng lượt tải
  }
}

// ---------------------------------------------------------------- tải miếng

async function fetchServerChunk(url) {
  // Server ghi URL theo Host nó thấy (cổng HTTP khi trang mở qua cổng HTTPS): lấy cùng origin
  const path = new URL(url, location.href).pathname;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), CHUNK_TIMEOUT_MS);
  try {
    const resp = await fetch(path, { signal: ctrl.signal, cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return new Uint8Array(await resp.arrayBuffer());
  } finally {
    clearTimeout(timer);
  }
}

const peerOf = (url) => (url.startsWith("webrtc://") ? url.slice("webrtc://".length).split("/")[0] : null);

// onProgress(items, received, note): items = [{index, source, received, bad, peer}] cho lưới miếng
export async function download({ deviceId, resp, bandwidth, p2p, onProgress }) {
  const { variant, plan } = resp;
  const out = new Uint8Array(variant.size_bytes);
  const items = plan.map((p) => ({ index: p.index, source: p.source, received: false, bad: false, peer: peerOf(p.url) }));
  const received = [];
  let pendingBad = [];
  let fromPeer = 0;
  let fromServer = 0;
  const peerCounts = {}; // peerId -> số miếng thực nhận, để báo "nhận model từ máy ..."
  let next = 0;
  let failed = null;
  let lastHave = 0;
  const notes = [];
  const links = new Map(); // peerId -> Promise<PeerLink | null>
  const start = performance.now();

  // Mỗi peer nối một lần; không nối được thì mọi miếng của peer đó lấy từ server
  function linkTo(peer) {
    if (!links.has(peer)) {
      links.set(
        peer,
        (p2p ? p2p.connect(peer) : Promise.reject(new Error("trình duyệt không hỗ trợ WebRTC"))).catch((err) => {
          notes.push(`Peer ${peer}: ${err.message}; lấy các miếng đó từ server.`);
          onProgress(items, received.length, notes);
          return null;
        }),
      );
    }
    return links.get(peer);
  }

  async function fromPeerLink(item, pos) {
    const link = await linkTo(items[pos].peer);
    if (!link) return null;
    try {
      return await link.get(variant.id, item);
    } catch (err) {
      notes.push(`Miếng ${item.index} từ ${link.peerId} lỗi (${err.message}); lấy lại từ server.`);
      return null;
    }
  }

  async function verify(data, item, url) {
    if (data.length === item.size_bytes && (await sha256Hex(data)) === item.sha256) return true;
    pendingBad.push({ index: item.index, source_url: url });
    return false;
  }

  async function worker() {
    while (next < plan.length && !failed) {
      const pos = next++;
      const item = plan[pos];
      let ok = false;
      let lastError = null;
      if (item.source === "peer" && items[pos].peer) {
        const data = await fromPeerLink(item, pos);
        if (data && (await verify(data, item, item.url))) {
          out.set(data, item.offset);
          fromPeer++;
          peerCounts[items[pos].peer] = (peerCounts[items[pos].peer] || 0) + 1;
          ok = true;
        } else if (data) {
          items[pos].bad = true;
          notes.push(`Miếng ${item.index} từ ${items[pos].peer} sai SHA-256; đã báo server, lấy lại từ server.`);
        }
        if (!ok) items[pos].source = "server"; // lưới miếng hiện nguồn thật
      }
      // miếng từ server, hoặc lấy lại miếng peer lỗi từ fallback_url
      const serverUrl = item.source === "peer" ? item.fallback_url : item.url;
      for (let attempt = 0; !ok && serverUrl && attempt < SERVER_ATTEMPTS; attempt++) {
        try {
          const data = await fetchServerChunk(serverUrl);
          if (await verify(data, item, serverUrl)) {
            out.set(data, item.offset);
            fromServer++;
            ok = true;
          } else {
            lastError = new Error("sai SHA-256");
          }
        } catch (err) {
          lastError = err;
        }
      }
      if (!ok) {
        failed = new Error(`Không tải được miếng ${item.index}: ${lastError?.message || "lỗi mạng"}.`);
        break;
      }
      Object.assign(items[pos], { received: true, bad: false });
      received.push(item.index);
      onProgress(items, received.length, notes);
      if (performance.now() - lastHave > HAVE_INTERVAL_MS && received.length < plan.length) {
        lastHave = performance.now();
        const bad = pendingBad;
        pendingBad = [];
        reportHave(deviceId, resp, received, false, bandwidth, bad);
      }
    }
  }

  try {
    await Promise.all(Array.from({ length: Math.min(PARALLEL, plan.length) }, worker));
  } finally {
    for (const link of links.values()) link.then((l) => l?.close());
  }
  if (failed) {
    await reportHave(deviceId, resp, received, false, bandwidth, pendingBad);
    throw failed;
  }
  return { bytes: out, downloadMs: performance.now() - start, fromPeer, fromServer, peerCounts, badChunks: pendingBad, notes };
}
