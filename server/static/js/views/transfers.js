// Truyền model: tiến độ từng request, lưới chunk theo nguồn server/peer.
import {
  Panel,
  api,
  badge,
  chunkGrid,
  emptyState,
  fmt,
  h,
  sortableTable,
  stackedBar,
} from "../../shared/js/ui.js";
import {
  bandwidthText,
  card,
  pageHead,
  progressBar,
  requestLink,
  requestStatus,
  shared,
  sourceSplit,
  variantLink,
} from "./common.js";

function listView(main) {
  const list = card("Các lượt tải", "Tiến độ theo các miếng thiết bị báo qua POST /api/have");
  list.body.classList.add("flush");
  main.append(
    pageHead(
      "Truyền model",
      "Model được chia thành miếng 1 MiB. Thiết bị tải song song 4 miếng, một phần từ peer cùng lớp và phần còn lại từ server; miếng peer lỗi thì tự lấy lại từ server.",
    ),
    list.el,
  );
  const sort = { sortKey: null, sortDir: "asc" };
  const panel = new Panel(list.body, {
    load: () => api("/admin-api/requests?limit=100"),
    render(data) {
      if (!data.requests.length) {
        return emptyState(
          "Chưa có lượt tải nào.",
          "Chạy client agent để thiết bị xin và tải model:",
          "python -m client.agent --server http://<IP>:8000 --device-id laptopA",
        );
      }
      return sortableTable({
        caption: "Mới nhất ở trên. Bấm mã request để xem lưới miếng.",
        state: sort,
        rows: data.requests,
        columns: [
          { key: "request_id", label: "Request", render: (r) => requestLink(r.request_id) },
          { key: "device_id", label: "Thiết bị", render: (r) => h("span", { class: "mono" }, r.device_id) },
          { key: "variant_id", label: "Variant", render: (r) => variantLink(r.variant_id) },
          { key: "received_count", label: "Tiến độ", render: (r) => progressBar(r.received_count, r.chunk_count) },
          { key: "plan", label: "Nguồn theo plan", sortable: false, render: (r) => sourceSplit(r.plan_counts) },
          { key: "server_bandwidth_kbps", label: "Tốc độ server đo được", align: "right", render: (r) => fmt.kbps(r.server_bandwidth_kbps) },
          { key: "bad_count", label: "Miếng lỗi", align: "right", render: (r) => (r.bad_count ? badge("danger", String(r.bad_count)) : "0") },
          { key: "status", label: "Trạng thái", render: (r) => requestStatus(r.status) },
        ],
      });
    },
  });
  sort.onChange = () => panel.redraw();
  return { panels: [panel] };
}

function elapsed(r) {
  const start = Date.parse(r.created_at);
  const end = Date.parse(r.completed_at || r.updated_at);
  return Number.isNaN(start) || Number.isNaN(end) ? null : Math.max(0, (end - start) / 1000);
}

function detailView(main, rid) {
  const grid = card("Lưới miếng", "Mỗi ô là một miếng 1 MiB; viền là nguồn theo plan, ô tô đầy là đã nhận");
  const info = card("Thông tin lượt tải");
  const sources = card("Nguồn theo plan");
  main.append(
    pageHead(`Lượt tải ${rid}`, null, { href: "#/transfers", text: "Truyền model" }),
    h("div", { class: "grid-3-1" }, grid.el, h("div", { class: "stack" }, info.el, sources.el)),
  );
  const load = shared(() => api(`/admin-api/requests/${encodeURIComponent(rid)}`));
  const row = (label, value) => [h("dt", {}, label), h("dd", {}, value)];
  return {
    panels: [
      new Panel(grid.body, {
        load,
        render: (r) =>
          r.plan.length
            ? h(
                "div",
                { class: "stack" },
                progressBar(r.received_count, r.chunk_count),
                chunkGrid(r.plan, { label: `Lưới miếng của ${r.request_id}` }),
              )
            : emptyState(
                "Plan rỗng: thiết bị đã có đủ model này.",
                "Server đã ghi nhận thiết bị tải xong variant này trước đó, nên không cần tải lại.",
              ),
      }),
      new Panel(info.body, {
        load,
        render: (r) =>
          h(
            "dl",
            { class: "dl-grid" },
            row("Trạng thái", requestStatus(r.status)),
            row("Thiết bị", h("span", { class: "mono" }, `${r.device_id} (${r.device_class || "?"})`)),
            row("Variant", variantLink(r.variant_id)),
            row("Băng thông khai báo", bandwidthText(r)),
            row("Tốc độ server đo được", fmt.kbps(r.server_bandwidth_kbps)),
            row("Hạn / ước tính", `${fmt.s(r.deadline_s)} / ${fmt.s(r.estimated_download_s)}`),
            row("Bắt đầu", fmt.time(r.created_at)),
            row(r.completed_at ? "Hoàn tất" : "Cập nhật cuối", fmt.time(r.completed_at || r.updated_at)),
            row("Thời gian", fmt.s(elapsed(r))),
            row("Miếng lỗi", r.bad_count ? badge("danger", `${r.bad_count} miếng`) : "0"),
            row("Chế độ dùng", r.mode ? `${r.mode === "live" ? "Camera trực tiếp" : "Ảnh chụp"} · ngân sách ${fmt.ms(r.budget_ms)}` : "không khai báo"),
            row("Chạy trên", r.runtime_family === "onnxruntime-web" ? "Trình duyệt (onnxruntime-web)" : "Client Python (onnxruntime)"),
            r.metrics
              ? row(
                  "Số đo client gửi",
                  `suy luận ${fmt.ms(r.metrics.latency_ms_median)}${r.metrics.fps ? ` · ${fmt.num(r.metrics.fps)} khung/giây` : ""} · ${r.metrics.chunks_from_peer} miếng từ peer, ${r.metrics.chunks_from_server} từ server`,
                )
              : null,
          ),
      }),
      new Panel(sources.body, {
        load,
        render(r) {
          const peers = [...new Set(r.plan.filter((p) => p.source === "peer").map((p) => p.origin))];
          const bar = stackedBar(
            [
              { label: "Server", value: r.plan_counts.server, cls: "src-server" },
              { label: "Peer", value: r.plan_counts.peer, cls: "src-peer" },
            ],
            { label: "Nguồn theo plan" },
          );
          return h(
            "div",
            { class: "stack" },
            bar || h("p", { class: "muted" }, "Plan rỗng."),
            peers.length
              ? h(
                  "ul",
                  { class: "alert-list" },
                  peers.map((origin) => {
                    const banned = r.plan.some((p) => p.origin === origin && p.origin_banned);
                    const kind = origin.startsWith("webrtc://") ? "WebRTC (trình duyệt)" : "HTTP (client Python)";
                    return h("li", { class: banned ? "is-danger" : "is-neutral" }, h("span", { class: "mono" }, origin), ` · ${kind}`, banned ? " · đã bị cấm (gửi sai hash)" : "");
                  }),
                )
              : h("p", { class: "small muted" }, "Không có peer nào cùng lớp đã có đủ model lúc lập plan."),
          );
        },
      }),
    ],
  };
}

export default {
  title: "Truyền model",
  mount(main, params) {
    return params[0] ? detailView(main, params[0]) : listView(main);
  },
};
