// Registry model: danh sách variant (kích thước, độ chính xác, số đo thực tế) và chi tiết,
// thêm model mới (upload .onnx) và bật/tắt variant.
import {
  Panel,
  api,
  badge,
  barChart,
  clear,
  confirmDialog,
  emptyState,
  fmt,
  h,
  icon,
  postWithProgress,
  sortableTable,
  toast,
} from "../../shared/js/ui.js";
import { card, pageHead, shared, variantLink } from "./common.js";

const VARIANT_ID = /^[a-z0-9_]{1,64}$/;

function statusBadge(v) {
  if (v.distributable) return badge("success", "ready");
  if (v.status === "ready") return badge("danger", "ready nhưng lỗi metadata");
  if (v.status === "disabled") return badge("neutral", "đã tắt", "pause");
  return badge("warning", v.status || "không rõ");
}

// ---------------------------------------------------------------- bật / tắt variant

async function changeStatus(v, next, { lastReady, onDone }) {
  const disabling = next !== "ready";
  const ok = await confirmDialog({
    title: disabling ? `Tắt ${v.variant_id}?` : `Bật lại ${v.variant_id}?`,
    message: disabling
      ? "Selector không chọn variant này cho lượt xin model mới nữa. Thiết bị đang tải dở vẫn tải nốt được, model đã tải về máy vẫn dùng bình thường."
      : "Variant trở lại danh sách ứng viên của Selector ngay, không cần khởi động lại server.",
    note: disabling && lastReady ? "Đây là variant ready cuối cùng: server sẽ từ chối để vẫn còn model phân phối. Bật một variant khác trước." : null,
    confirmText: disabling ? "Tắt variant" : "Bật variant",
    danger: disabling,
  });
  if (!ok) return;
  try {
    const r = await api(`/admin-api/variants/${encodeURIComponent(v.variant_id)}/status`, {
      method: "POST",
      body: { status: next },
      timeoutMs: 15000,
    });
    toast(
      r.distributable
        ? `Đã bật ${r.variant_id}: Selector chọn lại được (${r.ready_count} variant ready).`
        : `Đã tắt ${r.variant_id}: lượt xin mới không chọn variant này (còn ${r.ready_count} variant ready).`,
    );
  } catch (err) {
    toast(err.code === "CONFLICT" ? err.message : `Không đổi được trạng thái: ${err.message}`, "danger");
  }
  onDone();
}

function statusActions(v, { readyCount, onDone }) {
  if (v.status !== "ready" && v.status !== "disabled") return null;
  const disabling = v.status === "ready";
  const lastReady = disabling && v.distributable && readyCount === 1;
  return h(
    "button",
    {
      class: "btn btn-secondary btn-sm",
      type: "button",
      "aria-label": `${disabling ? "Tắt" : "Bật"} ${v.variant_id}`,
      onclick: () => changeStatus(v, disabling ? "disabled" : "ready", { lastReady, onDone }),
    },
    icon(disabling ? "pause" : "play"),
    disabling ? "Tắt" : "Bật",
  );
}

// ---------------------------------------------------------------- thêm model mới

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("Không đọc được file trên máy này."));
    reader.readAsDataURL(file);
  });
}

function suggestId(filename) {
  return filename
    .replace(/\.onnx$/i, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

function field(id, label, control, hint = null, required = false) {
  return h(
    "div",
    { class: "field" },
    h("label", { for: id }, label, required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null),
    control,
    hint ? h("span", { class: "hint", id: `${id}-hint` }, hint) : null,
    h("span", { class: "field-error", id: `${id}-error`, "aria-live": "polite" }),
  );
}

function uploadForm(onDone) {
  const input = (id, attrs = {}) => h("input", { class: "input", id, name: id, ...attrs });
  const file = input("up-file", { type: "file", accept: ".onnx", required: true, "aria-describedby": "up-file-hint" });
  const variant = input("up-variant", { required: true, maxlength: "64", autocomplete: "off", spellcheck: "false", "aria-describedby": "up-variant-hint" });
  const model = input("up-model", { required: true, maxlength: "64", autocomplete: "off", placeholder: "mobilenetv2" });
  const family = input("up-family", { maxlength: "64", autocomplete: "off", placeholder: "MobileNetV2" });
  const precision = h("select", { class: "input", id: "up-precision" }, ["int8", "fp16", "fp32"].map((p) => h("option", { value: p }, p)));
  const level = input("up-level", { type: "number", min: "0", step: "1", value: "1", inputmode: "numeric" });
  const top1 = input("up-top1", { type: "number", min: "0", max: "100", step: "0.1", inputmode: "decimal", placeholder: "70,2" });
  const version = input("up-version", { maxlength: "16", value: "1.0" });
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, icon("upload-simple"), h("span", {}, "Tải lên và kiểm định"));
  const progress = h("div", { "aria-live": "polite" });

  file.addEventListener("change", () => {
    const f = file.files[0];
    if (f && !variant.value) variant.value = suggestId(f.name);
  });

  const setError = (control, message) => {
    control.setAttribute("aria-invalid", message ? "true" : "false");
    clear(document.getElementById(`${control.id}-error`), message ? [icon("warning"), message] : []);
    return message;
  };

  const showPhase = (kind, text, fraction = null) =>
    clear(
      progress,
      h(
        "div",
        { class: "result-box stack" },
        h("p", { class: "row" }, badge(kind, text, kind === "info" ? "hourglass" : undefined)),
        fraction === null
          ? null
          : h(
              "div",
              { class: `progress${fraction >= 1 ? " is-done" : ""}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(fraction * 100)), "aria-label": "Tiến độ tải file lên server" },
              h("span", { style: `width:${fraction * 100}%` }),
            ),
      ),
    );

  const form = h(
    "form",
    { class: "stack", novalidate: true },
    h(
      "div",
      { class: "form-grid" },
      field("up-file", "File ONNX", file, "Server mở thử bằng ONNX Runtime; file hỏng bị từ chối và không lưu lại.", true),
      field("up-variant", "Mã variant", variant, "Chữ thường, số, dấu _ (vd. mobilenetv2_demo). Không trùng tên file của variant khác.", true),
      field("up-model", "Model gốc (model_id)", model, null, true),
      field("up-family", "Họ model", family),
      field("up-precision", "Precision", precision),
      field("up-level", "Mức nén (level)", level),
      field("up-top1", "Top-1 (%)", top1, "Độ chính xác công bố, để Selector so sánh"),
      field("up-version", "Phiên bản", version),
    ),
    h("div", { class: "form-actions" }, submit),
    progress,
  );

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = file.files[0];
    const top1Value = top1.value === "" ? 0 : Number(top1.value);
    const errors = [
      setError(file, !f ? "Chọn một file .onnx." : !/\.onnx$/i.test(f.name) ? "File phải có đuôi .onnx." : null),
      setError(variant, VARIANT_ID.test(variant.value.trim()) ? null : "Chỉ chữ thường, số và dấu _, tối đa 64 ký tự."),
      setError(model, model.value.trim() ? null : "Nhập model gốc."),
      setError(level, Number(level.value) >= 0 && Number.isInteger(Number(level.value)) ? null : "Số nguyên ≥ 0."),
      setError(top1, top1Value >= 0 && top1Value <= 100 ? null : "Từ 0 đến 100."),
    ];
    const firstBad = [file, variant, model, level, top1][errors.findIndex(Boolean)];
    if (firstBad) {
      firstBad.focus();
      return;
    }
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    try {
      showPhase("info", `Đang đọc ${f.name} (${fmt.mb(f.size)})…`);
      const b64 = await readBase64(f);
      const body = {
        file_base64: b64,
        filename: f.name,
        metadata: {
          variant_id: variant.value.trim(),
          model_id: model.value.trim(),
          family: family.value.trim() || model.value.trim(),
          precision: precision.value,
          level: Number(level.value),
          top1_accuracy: top1Value / 100,
          version: version.value.trim() || "1.0",
        },
      };
      const r = await postWithProgress("/admin-api/models/upload", body, {
        onProgress(phase, fraction) {
          if (phase === "uploading") showPhase("info", `Đang tải lên ${fmt.pct(fraction)}`, fraction);
          else showPhase("info", "Đã gửi xong · server đang kiểm định ONNX, băm SHA-256 và chia miếng…", 1);
        },
      });
      clear(
        progress,
        h(
          "div",
          { class: "result-box stack" },
          h("p", { class: "row" }, badge("success", "Sẵn sàng phân phối"), variantLink(r.variant_id)),
          h("p", { class: "small" }, `${r.filename} · ${fmt.mb(r.size_bytes)} · ${fmt.int(r.chunk_count)} miếng · SHA-256 ${fmt.short(r.sha256, 16)}`),
        ),
      );
      toast(`Đã thêm ${r.variant_id} vào Registry.`);
      form.reset();
      version.value = "1.0";
      level.value = "1";
      onDone();
    } catch (err) {
      clear(
        progress,
        h(
          "div",
          { class: "result-box stack", role: "alert" },
          h("p", { class: "row" }, badge("danger", err.code === "CONFLICT" ? "Trùng file" : "Không nhận model")),
          h("p", {}, err.message),
        ),
      );
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
    }
  });
  return form;
}

function listView(main, ctx) {
  const table = card("Các phiên bản trong Registry", "Đọc trực tiếp registry/model_registry.json. Tắt một variant thì Selector bỏ qua nó ngay.");
  const upload = card("Thêm model mới", "Tải file .onnx lên: server kiểm định, chia miếng 1 MiB, ghi Registry và CSDL. Không cần khởi động lại.");
  const size = card("Kích thước file", "Càng nhỏ càng tải nhanh trên mạng yếu");
  const acc = card("Độ chính xác top-1", "Số công bố của ONNX Model Zoo, chưa tự đo lại");
  main.append(
    pageHead(
      "Registry model",
      "Mỗi variant là một bản nén của một họ model. Selector chọn trong các bản ready dựa trên băng thông, hạn tải và dung lượng.",
    ),
    table.el,
    h("div", { class: "grid-2 section-gap" }, size.el, acc.el),
    h("div", { class: "section-gap" }, upload.el),
  );
  const sort = { sortKey: "size_bytes", sortDir: "asc" };
  const load = shared(() => api("/admin-api/variants"));
  const reload = () => {
    load.invalidate();
    ctx?.refresh();
  };
  upload.body.append(uploadForm(reload));
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
      const readyCount = data.variants.filter((v) => v.distributable).length;
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
          { key: "actions", label: "Thao tác", sortable: false, render: (v) => statusActions(v, { readyCount, onDone: reload }) },
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

function detailView(main, variantId, ctx) {
  const info = card("Thông tin artifact");
  const cardEl = card("Model card", "metadata/model_cards");
  const chunks = card("Danh sách miếng (chunk)", "Mỗi miếng 1 MiB, có SHA-256 riêng để client kiểm tra");
  main.append(
    pageHead(variantId, "Chi tiết một variant trong Registry.", { href: "#/models", text: "Registry model" }),
    h("div", { class: "grid-2" }, info.el, cardEl.el),
    h("div", { class: "section-gap" }, chunks.el),
  );
  const load = shared(() => api(`/admin-api/variants/${encodeURIComponent(variantId)}`));
  const reload = () => {
    load.invalidate();
    ctx?.refresh();
  };
  const row = (label, value) => [h("dt", {}, label), h("dd", {}, value)];
  return {
    panels: [
      new Panel(info.body, {
        load,
        render: (v) =>
          h(
            "dl",
            { class: "dl-grid" },
            row("Trạng thái", h("span", { class: "row" }, statusBadge(v), statusActions(v, { readyCount: null, onDone: reload }))),
            v.db_status && v.db_status !== v.status ? row("Trong CSDL", badge("warning", `${v.db_status} (lệch Registry)`)) : null,
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
  mount(main, params, ctx) {
    return params[0] ? detailView(main, params[0], ctx) : listView(main, ctx);
  },
};
