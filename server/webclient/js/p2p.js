// P2P giữa các trình duyệt cùng lớp thiết bị qua WebRTC DataChannel.
//
// Trình duyệt không mở cổng được như client Python, nên:
// - Signaling: server chuyển tiếp offer/answer (POST /api/signal, long-poll GET /api/signal/<id>).
//   Long-poll chạy suốt khi trang mở, nên server cũng biết máy này còn online để giao làm peer.
// - Dữ liệu: miếng model đi thẳng giữa hai máy trong LAN (không cần STUN/TURN), người nhận vẫn
//   kiểm SHA-256 từng miếng như miếng từ server; peer lỗi thì lấy lại miếng từ server.
//
// Giao thức trên DataChannel "chunks":
//   người xin  -> {op:"get", req, variant, index, offset, size}
//   người gửi  -> {op:"chunk", req, size} rồi các khối nhị phân đủ `size` byte, hoặc {op:"error", req, message}
// Người gửi xử lý tuần tự từng yêu cầu nên các khối của hai miếng không bị xen nhau.

const ICE_WAIT_MS = 2500;
const CONNECT_TIMEOUT_MS = 5000;
const CHUNK_TIMEOUT_MS = 30000;
const PIECE_BYTES = 64 * 1024;
const BUFFER_HIGH = 1024 * 1024;
const POLL_WAIT_S = 20;
const RETRY_MS = 2000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const randomId = () => Math.random().toString(36).slice(2, 12);

function iceComplete(pc) {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    const timer = setTimeout(resolve, ICE_WAIT_MS);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(timer);
        resolve();
      }
    });
  });
}

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

export class P2P {
  // getBytes(variantId) -> Promise<Uint8Array|null>: byte model trong kho của máy này
  // getName() -> tên người dùng đặt cho máy, gửi kèm offer để máy kia biết đang gửi cho ai
  constructor({ deviceId, getName, getBytes, onChange }) {
    this.deviceId = deviceId;
    this.getName = getName;
    this.getBytes = getBytes;
    this.onChange = onChange || (() => {});
    this.running = false;
    this.waiters = new Map(); // session -> resolve(answer)
    this.serving = new Map(); // session -> {peer, pc, channel}
    this.served = 0; // số miếng đã gửi cho máy khác
    this.peers = new Set(); // các máy từng tải từ máy này
    this.receiver = null; // tên máy nhận gần nhất
    this.variant = null; // model vừa gửi
  }

  // ------------------------------------------------------------ signaling

  start() {
    if (this.running) return;
    this.running = true;
    this.loop();
  }

  stop() {
    this.running = false;
  }

  // Đóng trang: báo server ngay để máy khác không được giao miếng từ máy này nữa
  goodbye() {
    this.running = false;
    try {
      fetch("/api/signal", {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from_device: this.deviceId, to_device: this.deviceId, type: "bye", session: "offline" }),
      });
    } catch {
      // trang đang đóng
    }
  }

  async loop() {
    while (this.running) {
      try {
        const resp = await fetch(`/api/signal/${encodeURIComponent(this.deviceId)}?wait=${POLL_WAIT_S}`, { cache: "no-store" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const { messages } = await resp.json();
        for (const msg of messages) this.handle(msg).catch(() => {});
      } catch {
        await sleep(RETRY_MS); // server tắt hoặc chưa đăng ký: thử lại sau
      }
    }
  }

  async send(message) {
    const resp = await fetch("/api/signal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_device: this.deviceId, ...message }),
    });
    if (!resp.ok) throw new Error(`Signaling lỗi HTTP ${resp.status}`);
    return resp.json();
  }

  async handle(msg) {
    if (msg.type === "offer") await this.answerOffer(msg);
    else if (msg.type === "answer") this.waiters.get(msg.session)?.(msg.data);
    else if (msg.type === "bye") this.closeServing(msg.session);
  }

  // ------------------------------------------------------------ máy gửi (đã có model)

  async answerOffer(msg) {
    const pc = new RTCPeerConnection({ iceServers: [] });
    const peerName = msg.data.name || msg.from_device;
    this.serving.set(msg.session, { peer: peerName, pc, channel: null });
    pc.ondatachannel = (event) => this.serveChannel(msg.session, peerName, event.channel);
    pc.onconnectionstatechange = () => {
      if (["failed", "closed", "disconnected"].includes(pc.connectionState)) this.closeServing(msg.session);
    };
    await pc.setRemoteDescription(msg.data.sdp);
    await pc.setLocalDescription(await pc.createAnswer());
    await iceComplete(pc);
    await this.send({ to_device: msg.from_device, type: "answer", session: msg.session, data: { sdp: pc.localDescription.toJSON() } });
  }

  serveChannel(session, peer, channel) {
    channel.binaryType = "arraybuffer";
    channel.bufferedAmountLowThreshold = BUFFER_HIGH / 4;
    const entry = this.serving.get(session);
    if (entry) entry.channel = channel;
    let queue = Promise.resolve();
    channel.onmessage = (event) => {
      const req = JSON.parse(event.data);
      if (req.op === "get") queue = queue.then(() => this.sendChunk(channel, peer, req)).catch(() => {});
    };
    channel.onopen = () => {
      this.peers.add(peer);
      this.onChange(this.status());
    };
    channel.onclose = () => this.closeServing(session);
  }

  async sendChunk(channel, peer, req) {
    const bytes = await this.getBytes(req.variant);
    if (!bytes || req.offset + req.size > bytes.length) {
      channel.send(JSON.stringify({ op: "error", req: req.req, message: "Máy này không còn model đó" }));
      return;
    }
    channel.send(JSON.stringify({ op: "chunk", req: req.req, size: req.size }));
    for (let pos = 0; pos < req.size; pos += PIECE_BYTES) {
      if (channel.bufferedAmount > BUFFER_HIGH) {
        await new Promise((resolve) => channel.addEventListener("bufferedamountlow", resolve, { once: true }));
      }
      const start = req.offset + pos;
      channel.send(bytes.subarray(start, start + Math.min(PIECE_BYTES, req.size - pos)));
    }
    this.served += 1;
    this.receiver = peer;
    this.variant = req.variant;
    this.onChange(this.status());
  }

  closeServing(session) {
    const entry = this.serving.get(session);
    if (!entry) return;
    this.serving.delete(session);
    try {
      entry.pc.close();
    } catch {
      // đã đóng
    }
    this.onChange(this.status());
  }

  status() {
    return {
      connected: this.serving.size,
      peers: this.peers.size,
      served: this.served,
      receiver: this.receiver,
      variant: this.variant,
    };
  }

  // ------------------------------------------------------------ máy nhận (đang tải model)

  // Mở DataChannel tới `peerId`; ném lỗi nếu không nối được trong CONNECT_TIMEOUT_MS
  async connect(peerId) {
    const session = randomId();
    const pc = new RTCPeerConnection({ iceServers: [] });
    const channel = pc.createDataChannel("chunks", { ordered: true });
    channel.binaryType = "arraybuffer";
    const opened = new Promise((resolve, reject) => {
      channel.onopen = resolve;
      channel.onerror = () => reject(new Error("DataChannel lỗi"));
    });
    const answered = new Promise((resolve) => this.waiters.set(session, resolve));
    try {
      await pc.setLocalDescription(await pc.createOffer());
      await iceComplete(pc);
      const sent = await this.send({ to_device: peerId, type: "offer", session, data: { sdp: pc.localDescription.toJSON(), name: this.getName() } });
      if (!sent.delivered) throw new Error(`${peerId} không còn mở trang`);
      const answer = await withTimeout(answered, CONNECT_TIMEOUT_MS, `${peerId} không trả lời`);
      await pc.setRemoteDescription(answer.sdp);
      await withTimeout(opened, CONNECT_TIMEOUT_MS, `không nối được tới ${peerId} (mạng chặn kết nối trực tiếp?)`);
    } catch (err) {
      pc.close();
      throw err;
    } finally {
      this.waiters.delete(session);
    }
    return new PeerLink(peerId, session, pc, channel, this);
  }
}

class PeerLink {
  constructor(peerId, session, pc, channel, p2p) {
    this.peerId = peerId;
    this.session = session;
    this.pc = pc;
    this.channel = channel;
    this.p2p = p2p;
    this.nextReq = 1;
    this.pending = new Map(); // req -> {resolve, reject, size}
    this.current = null; // miếng đang nhận: {req, buf, got}
    channel.onmessage = (event) => this.onMessage(event.data);
    channel.onclose = () => this.failAll(new Error(`${peerId} đã ngắt kết nối`));
  }

  onMessage(data) {
    if (typeof data === "string") {
      const msg = JSON.parse(data);
      const waiter = this.pending.get(msg.req);
      if (!waiter) return;
      if (msg.op === "error") {
        this.pending.delete(msg.req);
        waiter.reject(new Error(msg.message));
      } else if (msg.op === "chunk") {
        this.current = { req: msg.req, buf: new Uint8Array(msg.size), got: 0 };
      }
      return;
    }
    const cur = this.current;
    if (!cur) return;
    const piece = new Uint8Array(data);
    cur.buf.set(piece, cur.got);
    cur.got += piece.length;
    if (cur.got >= cur.buf.length) {
      this.current = null;
      const waiter = this.pending.get(cur.req);
      this.pending.delete(cur.req);
      waiter?.resolve(cur.buf);
    }
  }

  get(variant, item) {
    const req = this.nextReq++;
    const result = new Promise((resolve, reject) => this.pending.set(req, { resolve, reject }));
    this.channel.send(JSON.stringify({ op: "get", req, variant, index: item.index, offset: item.offset, size: item.size_bytes }));
    return withTimeout(result, CHUNK_TIMEOUT_MS, `${this.peerId} gửi miếng ${item.index} quá lâu`).catch((err) => {
      this.pending.delete(req);
      throw err;
    });
  }

  failAll(err) {
    for (const waiter of this.pending.values()) waiter.reject(err);
    this.pending.clear();
  }

  close() {
    this.p2p.send({ to_device: this.peerId, type: "bye", session: this.session }).catch(() => {});
    this.failAll(new Error("đã đóng"));
    try {
      this.pc.close();
    } catch {
      // đã đóng
    }
  }
}
