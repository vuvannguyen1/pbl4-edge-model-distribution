// Tổng quan hệ thống: link + QR cho client, KPI, tỉ lệ chunk peer/server, cảnh báo, cấu hình,
// request gần nhất.
import { Panel, api, emptyState, fmt, h, icon, stackedBar, toast } from "../../shared/js/ui.js";
import {
  bandwidthText,
  card,
  pageHead,
  progressBar,
  qrCode,
  requestLink,
  requestStatus,
  shared,
  variantLink,
} from "./common.js";

// ---------------------------------------------------------------- link cho client

let chosenLink = null; // link đang hiện QR (giữ qua các lượt vẽ lại)

function copyButton(url) {
  if (!navigator.clipboard || !window.isSecureContext) return null; // http://<IP>: trình duyệt chặn clipboard
  return h(
    "button",
    {
      class: "btn btn-ghost btn-sm",
      type: "button",
      onclick: () => navigator.clipboard.writeText(url).then(() => toast("Đã chép link.")),
    },
    "Chép",
  );
}

function renderLinks(data, redraw) {
  const links = data.links;
  if (!links.length) return emptyState("Không xác định được địa chỉ IP của máy server.");
  if (!links.some((l) => l.url === chosenLink)) chosenLink = links[0].url;
  const chosen = links.find((l) => l.url === chosenLink);
  return h(
    "div",
    { class: "link-panel" },
    h(
      "figure",
      { class: "qr-box" },
      qrCode(chosen.url, `Mã QR của ${chosen.url}`) || h("p", { class: "muted" }, "Không vẽ được mã QR."),
      h("figcaption", { class: "small muted" }, "Quét bằng camera điện thoại"),
    ),
    h(
      "div",
      { class: "stack" },
      h("p", { class: "link-main mono wrap-anywhere" }, chosen.url),
      h("p", { class: "small" }, chosen.note),
      h(
        "ul",
        { class: "link-list" },
        links.map((l) =>
          h(
            "li",
            {},
            h(
              "button",
              {
                class: `btn btn-sm ${l.url === chosenLink ? "btn-primary" : "btn-secondary"}`,
                type: "button",
                "aria-pressed": String(l.url === chosenLink),
                onclick: () => {
                  chosenLink = l.url;
                  redraw();
                },
              },
              l.kind === "https" ? icon("camera") : icon("image"),
              l.kind === "https" ? "HTTPS" : "HTTP",
            ),
            h("span", { class: "mono small wrap-anywhere" }, l.url),
            copyButton(l.url),
          ),
        ),
      ),
      data.https_port
        ? null
        : h(
            "p",
            { class: "state is-warning small" },
            icon("warning"),
            "Chưa có link HTTPS nên điện thoại chỉ chụp được từng ảnh. Bật server bằng python -m server.serve để có camera trực tiếp.",
          ),
      h("p", { class: "small muted" }, "Điện thoại/laptop phải cùng Wi-Fi hoặc hotspot với máy server. Mở link là tự đăng ký, xin model và dùng camera; không cần cài gì."),
    ),
  );
}

function kpi(iconName, label, value, note) {
  return h(
    "div",
    { class: "card kpi" },
    h("span", { class: "kpi-label" }, icon(iconName), label),
    h("span", { class: "kpi-value" }, value),
    note ? h("span", { class: "kpi-note" }, note) : null,
  );
}

// Chưa có thiết bị/lượt xin nào thì ghi "Chưa có dữ liệu" thay cho số 0 dễ hiểu nhầm.
const NO_DATA = () => h("span", { class: "kpi-empty" }, "Chưa có dữ liệu");

function renderKpis(s) {
  const ready = s.variants.by_status.ready || 0;
  const req = s.requests.by_status;
  const hasDevices = s.devices.registered > 0;
  return h(
    "div",
    { class: "kpi-grid" },
    kpi("stack", "Variant sẵn sàng", s.variants.total ? `${ready}/${s.variants.total}` : NO_DATA(), "trạng thái ready trong Registry"),
    kpi(
      "devices",
      "Thiết bị đã đăng ký",
      hasDevices ? fmt.int(s.devices.registered) : NO_DATA(),
      hasDevices
        ? `${fmt.int(s.devices.browsers)} trình duyệt (${fmt.int(s.devices.online_browsers)} đang mở trang) · ${fmt.int(s.devices.python_clients)} client Python`
        : "Mở link ở ô bên trên trên điện thoại để bắt đầu",
    ),
    kpi(
      "share-network",
      "Peer đang chia sẻ",
      hasDevices ? fmt.int(s.devices.sharing_peers) : NO_DATA(),
      `có model đủ + đang mở trang (WebRTC) hoặc mở cổng HTTP · ${fmt.int(s.p2p_webrtc.offers)} kết nối WebRTC`,
    ),
    kpi(
      "arrows-down-up",
      "Lượt xin model",
      s.requests.total ? fmt.int(s.requests.total) : NO_DATA(),
      s.requests.total ? `${req.downloading || 0} đang tải · ${req.complete || 0} xong · ${req.planned || 0} từ web` : "kể từ khi server khởi động",
    ),
  );
}

function renderChunks(s) {
  const actual = s.chunks_actual;
  const planned = s.chunks_planned;
  const actualBar = stackedBar(
    [
      { label: "Server", value: actual.server, cls: "src-server" },
      { label: "Peer", value: actual.peer, cls: "src-peer" },
    ],
    { label: "Nguồn chunk thực tế" },
  );
  const plannedBar = stackedBar(
    [
      { label: "Server", value: planned.server, cls: "src-server" },
      { label: "Peer", value: planned.peer, cls: "src-peer" },
    ],
    { label: "Nguồn chunk theo kế hoạch" },
  );
  return h(
    "div",
    { class: "stack" },
    h("h3", {}, "Thực tế (số đo agent gửi về)"),
    actualBar ||
      emptyState(
        "Chưa có số đo nào.",
        "Số liệu có sau khi một client tải xong model và chạy nhận diện lần đầu (mở link /app ở ô bên trên).",
        null,
      ),
    actual.reports ? h("p", { class: "small muted" }, `Từ ${actual.reports} lần tải đã báo cáo.`) : null,
    h("h3", {}, "Theo kế hoạch (request còn trong RAM)"),
    plannedBar || h("p", { class: "muted" }, "Chưa có plan nào kể từ khi server khởi động."),
  );
}

function renderAlerts(s) {
  if (!s.alerts.length) {
    return h(
      "p",
      { class: "badge is-success", style: "white-space:normal" },
      icon("check-circle"),
      "Không có cảnh báo: hash, peer và Registry đều ổn.",
    );
  }
  return h(
    "ul",
    { class: "alert-list" },
    s.alerts.map((a) =>
      h("li", { class: `is-${a.level}` }, icon(a.level === "danger" ? "x-circle" : "warning"), fmt.text(a.message)),
    ),
  );
}

function renderServer(s) {
  const cfg = s.server;
  return h(
    "dl",
    { class: "dl-grid" },
    h("dt", {}, "Protocol"),
    h("dd", { class: "mono" }, cfg.protocol_version),
    h("dt", {}, "Selector"),
    h("dd", { class: "mono" }, cfg.selector_version),
    h("dt", {}, "Giới hạn tốc độ"),
    h("dd", {}, cfg.rate_limit_kbps ? fmt.kbps(cfg.rate_limit_kbps) : "không giới hạn"),
    h("dt", {}, "P2P"),
    h("dd", {}, cfg.peer_enabled ? "bật" : "tắt"),
    h("dt", {}, "Ngưỡng latency (SLO)"),
    h("dd", {}, `${fmt.ms(cfg.latency_slo_ms)} (client không khai báo chế độ dùng)`),
    h("dt", {}, "Link client"),
    h("dd", {}, cfg.https_port ? `/app/ · HTTPS cổng ${cfg.https_port}` : "/app/ · chưa bật HTTPS"),
    h("dt", {}, "Tốc độ đo được"),
    h("dd", {}, speeds(s.class_speeds)),
  );
}

function speeds(classSpeeds) {
  const entries = Object.entries(classSpeeds || {});
  if (!entries.length) return h("span", { class: "muted" }, "chưa có; Selector dùng số ước lượng theo loại máy");
  return h(
    "ul",
    { class: "plain-list small" },
    entries.map(([cls, v]) => h("li", {}, h("span", { class: "mono" }, cls), ` · ${fmt.num(v.ms_per_gflop)} ms/GFLOP (${v.samples} lần đo)`)),
  );
}

function renderRecent(data) {
  const rows = data.requests.slice(0, 6);
  if (!rows.length) {
    return emptyState(
      "Chưa có lượt xin model nào.",
      "Mở link ở ô trên cùng trên điện thoại, hoặc dùng “Xin model thử” ở màn Quyết định chọn model.",
    );
  }
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "data" },
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          ["Request", "Thiết bị", "Variant", "Băng thông", "Tiến độ", "Trạng thái"].map((t) =>
            h("th", { scope: "col" }, t),
          ),
        ),
      ),
      h(
        "tbody",
        {},
        rows.map((r) =>
          h(
            "tr",
            {},
            h("td", {}, requestLink(r.request_id)),
            h("td", { class: "mono" }, r.device_id),
            h("td", {}, variantLink(r.variant_id)),
            h("td", { class: "num" }, bandwidthText(r)),
            h("td", {}, progressBar(r.received_count, r.chunk_count)),
            h("td", {}, requestStatus(r.status)),
          ),
        ),
      ),
    ),
  );
}

export default {
  title: "Tổng quan",
  mount(main) {
    const linksCard = card("Link cho điện thoại / laptop", "Mở link (hoặc quét mã QR) để xin model và phát hiện vật thể bằng camera");
    const kpis = h("div");
    const chunks = card("Nguồn chunk", "Tỉ lệ miếng model lấy từ server so với từ peer (P2P)");
    const alerts = card("Cảnh báo", "Peer gửi sai, chunk hỏng, request treo, Registry lỗi");
    const server = card("Cấu hình server");
    const recent = card(
      "Lượt xin model gần nhất",
      null,
      h("a", { href: "#/transfers" }, "Xem tất cả"),
    );
    main.append(
      pageHead(
        "Tổng quan hệ thống",
        "Phân phối model phát hiện vật thể (ONNX) tới điện thoại, laptop và thiết bị edge, tự cập nhật mỗi 2 giây.",
      ),
      linksCard.el,
      h("div", { class: "section-gap" }, kpis),
      h("div", { class: "grid-3-1 section-gap" }, chunks.el, h("div", { class: "stack" }, alerts.el, server.el)),
      h("div", { class: "section-gap" }, recent.el),
    );
    const summary = shared(() => api("/admin-api/summary"));
    const linksPanel = new Panel(linksCard.body, {
      load: () => api("/admin-api/links"),
      render: (data) => renderLinks(data, () => linksPanel.redraw()),
    });
    return {
      panels: [
        linksPanel,
        new Panel(kpis, { load: summary, render: renderKpis }),
        new Panel(chunks.body, { load: summary, render: renderChunks }),
        new Panel(alerts.body, { load: summary, render: renderAlerts }),
        new Panel(server.body, { load: summary, render: renderServer }),
        new Panel(recent.body, { load: () => api("/admin-api/requests?limit=6"), render: renderRecent }),
      ],
    };
  },
};
