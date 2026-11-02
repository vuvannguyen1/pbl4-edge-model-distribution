// Registry model: danh sách variant (kích thước, độ chính xác, số đo thực tế) và chi tiết.
import {
  Panel,
  api,
  badge,
  barChart,
  emptyState,
  fmt,
  h,
  sortableTable,
} from "../../shared/js/ui.js";
import { card, pageHead, shared, variantLink } from "./common.js";

function statusBadge(v) {
  if (v.distributable) return badge("success", "ready");
  if (v.status === "ready") return badge("danger", "ready nhưng lỗi metadata");
  return badge("warning", v.status || "không rõ");
}

function listView(main) {
  const table = card("Các phiên bản trong Registry", "Đọc trực tiếp registry/model_registry.json");
  const size = card("Kích thước file", "Càng nhỏ càng tải nhanh trên mạng yếu");
  const acc = card("Độ chính xác top-1", "Số công bố của ONNX Model Zoo, chưa tự đo lại");
  main.append(
    pageHead(
      "Registry model",
      "Mỗi variant là một bản nén của một họ model. Selector chọn trong các bản ready dựa trên băng thông, hạn tải và dung lượng.",
    ),
    table.el,
    h("div", { class: "grid-2 section-gap" }, size.el, acc.el),
  );
  const sort = { sortKey: "size_bytes", sortDir: "asc" };
  const load = shared(() => api("/admin-api/variants"));
  const tablePanel = new Panel(table.body, {
    load,
    render(data) {
      if (!data.variants.length) {
        return emptyState(
          "Registry chưa có variant nào.",
          "Tải model và sinh Registry trên máy server:",
          "python tools\\download_models.py && python tools\\build_registry.py",
        );
      }
      return sortableTable({
        state: sort,
        rows: data.variants,
        columns: [
          { key: "variant_id", label: "Variant", render: (v) => variantLink(v.variant_id) },
          { key: "family", label: "Họ model" },
          { key: "precision", label: "Precision", render: (v) => h("span", { class: "tag" }, v.precision) },
          { key: "size_bytes", label: "Kích thước", align: "right", render: (v) => fmt.mb(v.size_bytes) },
          { key: "chunk_count", label: "Số miếng", align: "right" },
          { key: "top1_accuracy", label: "Top-1", align: "right", render: (v) => fmt.pct(v.top1_accuracy) },
          { key: "latency_ms_median", label: "Latency đo được", align: "right", render: (v) => (v.metrics_count ? `${fmt.ms(v.latency_ms_median)} (${v.metrics_count} lần)` : h("span", { class: "muted" }, "chưa đo")) },
          { key: "devices_complete", label: "Thiết bị có", align: "right" },
          { key: "status", label: "Trạng thái", render: statusBadge },
        ],
      });
    },
  });
  sort.onChange = () => tablePanel.redraw();
  const chart = (key, format, summary) => (data) =>
    barChart({
      rows: data.variants.map((v) => ({ label: v.variant_id, value: v[key] })),
      format,
      summary: summary(data.variants),
    }) || emptyState("Chưa có dữ liệu.");
  return {
    panels: [
      tablePanel,
      new Panel(size.body, {
        load,
        render: chart("size_bytes", fmt.mb, (vs) => `${vs.length} variant, sắp từ lớn đến nhỏ.`),
      }),
      new Panel(acc.body, {
        load,
        render: chart("top1_accuracy", fmt.pct, () => "Top-1 trên ImageNet validation, sắp từ cao đến thấp."),
      }),
    ],
  };
}

function detailView(main, variantId) {
  const info = card("Thông tin artifact");
  const cardEl = card("Model card", "metadata/model_cards");
  const chunks = card("Danh sách miếng (chunk)", "Mỗi miếng 1 MiB, có SHA-256 riêng để client kiểm tra");
  main.append(
    pageHead(variantId, "Chi tiết một variant trong Registry.", { href: "#/models", text: "Registry model" }),
    h("div", { class: "grid-2" }, info.el, cardEl.el),
    h("div", { class: "section-gap" }, chunks.el),
  );
  const load = shared(() => api(`/admin-api/variants/${encodeURIComponent(variantId)}`));
  const row = (label, value) => [h("dt", {}, label), h("dd", {}, value)];
  return {
    panels: [
      new Panel(info.body, {
        load,
        render: (v) =>
          h(
            "dl",
            { class: "dl-grid" },
            row("Trạng thái", statusBadge(v)),
            row("Họ / phiên bản", `${v.family} · v${v.version}`),
            row("Precision", v.precision),
            row("File", h("span", { class: "mono" }, v.file)),
            row("Kích thước", `${fmt.mb(v.size_bytes)} (${fmt.int(v.size_bytes)} byte)`),
            row("SHA-256", h("span", { class: "mono small wrap-anywhere" }, v.sha256)),
            row("Input", h("span", { class: "mono" }, `${JSON.stringify(v.input_shape)} · ${v.output_contract}`)),
            row("Runtime", (v.supported_runtimes || []).join(", ")),
            row("Top-1", fmt.pct(v.top1_accuracy)),
            row("Thiết bị đã có", fmt.int(v.devices_complete)),
            row(
              "Số đo thực tế",
              v.metrics_count
                ? `${v.metrics_count} lần · tải ${fmt.ms(v.download_ms_median)} · nạp ${fmt.ms(v.load_ms_median)} · latency ${fmt.ms(v.latency_ms_median)} · RAM ${fmt.num(v.peak_ram_mb_median)} MB (trung vị)`
                : "chưa có",
            ),
            v.warnings.length ? row("Cảnh báo", v.warnings.join("; ")) : null,
          ),
      }),
      new Panel(cardEl.body, {
        load,
        render: (v) =>
          v.model_card
            ? h(
                "dl",
                { class: "dl-grid" },
                row("Task", v.model_card.task?.id),
                row("Nguồn", v.model_card.provenance?.source),
                row("Đánh giá", v.model_card.quality?.evaluation_dataset),
                row("Top-5", fmt.pct(v.model_card.quality?.top5_accuracy)),
                row("Ghi chú số liệu", v.model_card.quality?.source),
                row("Vòng đời", v.model_card.lifecycle?.status),
              )
            : emptyState("Không có model card cho variant này."),
      }),
      new Panel(chunks.body, {
        load,
        render: (v) =>
          h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              { class: "data" },
              h("thead", {}, h("tr", {}, ["#", "Offset", "Kích thước", "SHA-256"].map((t) => h("th", { scope: "col" }, t)))),
              h(
                "tbody",
                {},
                v.chunks.map((c) =>
                  h(
                    "tr",
                    {},
                    h("td", { class: "num" }, c.index),
                    h("td", { class: "num" }, fmt.int(c.offset)),
                    h("td", { class: "num" }, fmt.int(c.size_bytes)),
                    h("td", { class: "mono small" }, fmt.short(c.sha256, 24)),
                  ),
                ),
              ),
            ),
          ),
      }),
    ],
  };
}

export default {
  title: "Registry model",
  mount(main, params) {
    return params[0] ? detailView(main, params[0]) : listView(main);
  },
};
