// Tổng quan hệ thống: KPI, tỉ lệ chunk peer/server, cảnh báo, cấu hình, request gần nhất.
import { Panel, api, emptyState, fmt, h, icon, stackedBar } from "../../shared/js/ui.js";
import {
  bandwidthText,
  card,
  pageHead,
  progressBar,
  requestLink,
  requestStatus,
  shared,
  variantLink,
} from "./common.js";

function kpi(iconName, label, value, note) {
  return h(
    "div",
    { class: "card kpi" },
    h("span", { class: "kpi-label" }, icon(iconName), label),
    h("span", { class: "kpi-value" }, value),
    note ? h("span", { class: "kpi-note" }, note) : null,
  );
}

function renderKpis(s) {
  const ready = s.variants.by_status.ready || 0;
  const req = s.requests.by_status;
  return h(
    "div",
    { class: "kpi-grid" },
    kpi("stack", "Variant sẵn sàng", `${ready}/${s.variants.total}`, "trạng thái ready trong Registry"),
    kpi(
      "devices",
      "Thiết bị đã đăng ký",
      fmt.int(s.devices.registered),
      s.devices.registered ? `${fmt.int(s.devices.active)} đang hoạt động (liên lạc ≤ 30 giây hoặc đang chia sẻ)` : "Chưa có thiết bị nào",
    ),
    kpi("share-network", "Peer đang chia sẻ", fmt.int(s.devices.sharing_peers), "có model đủ + mở cổng peer"),
    kpi(
      "arrows-down-up",
      "Lượt xin model",
      fmt.int(s.requests.total),
      `${req.downloading || 0} đang tải · ${req.complete || 0} xong · ${req.planned || 0} từ web`,
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
        "Số liệu có sau khi một client tải xong model và gửi feedback.",
        "python -m client.agent --server http://<IP>:8000 --device-id laptopA",
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
    h("dd", {}, fmt.ms(cfg.latency_slo_ms)),
  );
}

function renderRecent(data) {
  const rows = data.requests.slice(0, 6);
  if (!rows.length) {
    return emptyState(
      "Chưa có lượt xin model nào.",
      "Chạy client agent hoặc dùng “Xin model thử” ở màn Quyết định chọn model.",
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
        "Trạng thái phân phối model ONNX tới thiết bị edge, tự cập nhật mỗi 2 giây.",
      ),
      kpis,
      h("div", { class: "grid-3-1 section-gap" }, chunks.el, h("div", { class: "stack" }, alerts.el, server.el)),
      h("div", { class: "section-gap" }, recent.el),
    );
    const summary = shared(() => api("/admin-api/summary"));
    return {
      panels: [
        new Panel(kpis, { load: summary, render: renderKpis }),
        new Panel(chunks.body, { load: summary, render: renderChunks }),
        new Panel(alerts.body, { load: summary, render: renderAlerts }),
        new Panel(server.body, { load: summary, render: renderServer }),
        new Panel(recent.body, { load: () => api("/admin-api/requests?limit=6"), render: renderRecent }),
      ],
    };
  },
};
