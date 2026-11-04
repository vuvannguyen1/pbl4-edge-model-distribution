// Quyết định chọn model: vì sao Selector chọn variant đó, và form "Xin model thử".
import { ApiError, Panel, api, badge, emptyState, fmt, h, icon, toast } from "../../shared/js/ui.js";
import {
  bandwidthText,
  card,
  pageHead,
  requestLink,
  requestStatus,
  shared,
  sourceSplit,
  variantLink,
} from "./common.js";

function decisionCard(r) {
  return h(
    "article",
    { class: "req-card" },
    h(
      "div",
      { class: "req-title" },
      requestLink(r.request_id),
      h("span", { class: "mono" }, r.device_id),
      h("span", { class: "arrow", "aria-hidden": "true" }, "→"),
      h("span", { class: "sr-only" }, "được chọn"),
      variantLink(r.variant_id),
      requestStatus(r.status),
      h("span", { class: "tag" }, `selector ${r.selector_version}`),
      r.source === "web" ? h("span", { class: "tag" }, "xin thử từ web") : null,
    ),
    h(
      "p",
      { class: "small muted" },
      `${fmt.time(r.created_at)} · lớp ${r.device_class || "?"} · băng thông ${bandwidthText(r)} · hạn ${fmt.s(r.deadline_s)} · tải ước tính ${fmt.s(r.estimated_download_s)} · plan: ${sourceSplit(r.plan_counts)}`,
    ),
    h("p", { class: "reason" }, fmt.text(r.reason)),
    r.decision_log.length
      ? h(
          "details",
          { class: "disclosure" },
          h("summary", {}, icon("caret-right"), `Các bước lọc ứng viên (${r.decision_log.length})`),
          h("ol", { class: "decision-log" }, r.decision_log.map((line) => h("li", {}, fmt.text(line)))),
        )
      : null,
  );
}

function renderList(data, openState) {
  if (!data.requests.length) {
    return emptyState(
      "Chưa có quyết định nào.",
      "Mỗi lần thiết bị gọi POST /api/request, server ghi lại lý do chọn ở đây. Có thể thử ngay bằng form bên trên.",
    );
  }
  const list = h("div", {}, data.requests.map(decisionCard));
  // giữ các mục "Các bước lọc" đang mở khi danh sách vẽ lại
  list.querySelectorAll("details").forEach((d, i) => {
    const rid = data.requests[i]?.request_id;
    if (openState.has(rid)) d.open = true;
    d.addEventListener("toggle", () => (d.open ? openState.add(rid) : openState.delete(rid)));
  });
  return list;
}

function renderMemory(data) {
  const entries = Object.entries(data.class_variants);
  if (!entries.length) {
    return h(
      "p",
      { class: "muted" },
      "Chưa có. Khi latency đo được vượt ngưỡng SLO, server ghi nhớ variant nhẹ hơn cho cả lớp thiết bị và dùng cho các lượt sau.",
    );
  }
  return h(
    "dl",
    { class: "dl-grid" },
    entries.map(([cls, v]) => [h("dt", { class: "mono" }, cls), h("dd", {}, variantLink(v))]),
  );
}

// ---------------------------------------------------------------- form xin model thử

function field(id, label, control, hint, required = false) {
  return h(
    "div",
    { class: "field" },
    h("label", { for: id }, label, required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null),
    control,
    hint ? h("span", { class: "hint", id: `${id}-hint` }, hint) : null,
    h("span", { class: "field-error", id: `${id}-error`, "aria-live": "polite" }),
  );
}

function tryForm(onDone) {
  const device = h("select", { class: "input", id: "try-device", required: true, "aria-describedby": "try-device-hint try-device-error" });
  const bandwidth = h("input", { class: "input", id: "try-bw", type: "number", min: "1", step: "any", inputmode: "decimal", placeholder: "vd. 600", "aria-describedby": "try-bw-hint try-bw-error" });
  const deadline = h("input", { class: "input", id: "try-deadline", type: "number", min: "1", step: "any", value: "60", required: true, "aria-describedby": "try-deadline-error" });
  const storage = h("input", { class: "input", id: "try-storage", type: "number", min: "0", step: "any", placeholder: "không giới hạn", "aria-describedby": "try-storage-hint try-storage-error" });
  const preferred = h("select", { class: "input", id: "try-preferred" }, h("option", { value: "" }, "Để Selector tự chọn"));
  const mode = h(
    "select",
    { class: "input", id: "try-mode", "aria-describedby": "try-mode-hint" },
    h("option", { value: "live" }, "Camera trực tiếp (≥ 5 khung/giây)"),
    h("option", { value: "photo" }, "Ảnh chụp (≤ 2,5 s/ảnh)"),
    h("option", { value: "" }, "Không khai báo (như client 1.0)"),
  );
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, icon("lightning"), "Xin model thử");
  const result = h("div", { "aria-live": "polite" });

  const form = h(
    "form",
    { novalidate: true },
    h(
      "div",
      { class: "form-grid" },
      field("try-device", "Thiết bị", device, "Phải đăng ký trước (chạy client ít nhất một lần)", true),
      field("try-bw", "Băng thông (kbps)", bandwidth, "Bỏ trống: server dùng giới hạn của nó"),
      field("try-deadline", "Hạn tải (giây)", deadline, null, true),
      field("try-storage", "Dung lượng trống (MB)", storage, "Bỏ trống: không giới hạn"),
      field("try-mode", "Dùng để", mode, "Server lọc model theo tốc độ suy luận ước lượng của thiết bị"),
      field("try-preferred", "Variant mong muốn", preferred),
    ),
    h("div", { class: "form-actions" }, submit, h("span", { class: "small muted" }, "Chỉ lập kế hoạch tải, không tải file. Lượt này hiện trong danh sách với nhãn “xin thử từ web”.")),
    result,
  );

  const setError = (input, message) => {
    input.setAttribute("aria-invalid", message ? "true" : "false");
    form.querySelector(`#${input.id}-error`).replaceChildren(...(message ? [icon("warning"), message] : []));
  };
  const positive = (input, optional) => {
    if (input.value === "") return optional ? null : "Bắt buộc nhập.";
    const v = Number(input.value);
    return Number.isFinite(v) && v > 0 ? null : "Phải là số lớn hơn 0.";
  };
  for (const [input, optional] of [[bandwidth, true], [deadline, false]]) {
    input.addEventListener("blur", () => setError(input, positive(input, optional)));
  }
  storage.addEventListener("blur", () => setError(storage, storage.value !== "" && !(Number(storage.value) >= 0) ? "Phải là số không âm." : null));

  async function loadOptions() {
    try {
      const [devs, vars] = await Promise.all([api("/admin-api/devices"), api("/admin-api/variants")]);
      const selected = device.value;
      device.replaceChildren(
        ...(devs.devices.length
          ? devs.devices.map((d) => h("option", { value: d.device_id }, `${d.device_id} (${d.device_class}${d.runtime_family === "onnxruntime-web" ? ", trình duyệt" : ""})`))
          : [h("option", { value: "" }, "Chưa có thiết bị nào đăng ký")]),
      );
      if (selected) device.value = selected;
      device.disabled = !devs.devices.length;
      submit.disabled = !devs.devices.length;
      preferred.replaceChildren(
        h("option", { value: "" }, "Để Selector tự chọn"),
        ...vars.variants.filter((v) => v.distributable).map((v) => h("option", { value: v.variant_id }, v.variant_id)),
      );
    } catch (err) {
      setError(device, `Không tải được danh sách: ${err.message}`);
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errors = [
      [bandwidth, positive(bandwidth, true)],
      [deadline, positive(deadline, false)],
      [storage, storage.value !== "" && !(Number(storage.value) >= 0) ? "Phải là số không âm." : null],
      [device, device.value ? null : "Chọn một thiết bị."],
    ];
    errors.forEach(([input, msg]) => setError(input, msg));
    const firstBad = errors.find(([, msg]) => msg);
    if (firstBad) {
      firstBad[0].focus();
      return;
    }
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.lastChild.textContent = "Đang gửi…";
    try {
      const resp = await api("/api/request", {
        method: "POST",
        headers: { "X-PBL4-Source": "web-ui" },
        body: {
          device_id: device.value,
          task: "object_detection",
          bandwidth_kbps: bandwidth.value === "" ? null : Number(bandwidth.value),
          deadline_s: Number(deadline.value),
          free_storage_mb: storage.value === "" ? null : Number(storage.value),
          preferred_variant: preferred.value || null,
          have_variants: [],
          ...(mode.value ? { mode: mode.value } : {}),
        },
      });
      const counts = { server: 0, peer: 0 };
      resp.plan.forEach((p) => (counts[p.source] += 1));
      result.replaceChildren(
        h(
          "div",
          { class: "result-box stack" },
          h("p", { class: "row" }, badge("success", "Server đã chọn"), variantLink(resp.variant.id), requestLink(resp.request_id)),
          h("p", {}, fmt.text(resp.reason)),
          h("p", { class: "small muted" }, `${fmt.mb(resp.variant.size_bytes)} · ${resp.variant.chunk_count} miếng · tải ước tính ${fmt.s(resp.estimated_download_s)} · plan: ${sourceSplit(counts)}`),
        ),
      );
      toast(`Đã nhận quyết định ${resp.request_id}: ${resp.variant.id}`);
      onDone();
    } catch (err) {
      const fits = err instanceof ApiError && err.code === "NO_VARIANT_FITS";
      result.replaceChildren(
        h(
          "div",
          { class: "result-box", role: "alert" },
          h("p", { class: "row" }, badge(fits ? "warning" : "danger", fits ? "Không variant nào phù hợp" : "Lỗi")),
          h("p", {}, err.message),
          fits ? h("p", { class: "small muted" }, "Thử tăng hạn tải, tăng băng thông hoặc bỏ giới hạn dung lượng.") : null,
        ),
      );
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.lastChild.textContent = "Xin model thử";
    }
  });

  loadOptions();
  return { form, loadOptions };
}

export default {
  title: "Quyết định chọn model",
  mount(main, _params, ctx) {
    const formCard = card(
      "Xin model thử",
      "Gọi POST /api/request thay cho một thiết bị đã đăng ký để xem Selector quyết định thế nào.",
    );
    const list = card("Các quyết định gần nhất", "Mới nhất ở trên. Mở “Các bước lọc ứng viên” để xem vì sao từng bản bị loại.");
    const memory = card("Ghi nhớ theo lớp thiết bị", "Từ quyết định switch của feedback");
    const { form, loadOptions } = tryForm(() => ctx.refresh());
    formCard.body.append(form);
    main.append(
      pageHead(
        "Quyết định chọn model",
        "Bộ lọc cứng loại các bản sai runtime, thiếu RAM, chạy quá ngân sách thời gian của chế độ dùng, không tải kịp hạn hoặc không đủ dung lượng; rồi Selector chọn bản có mAP cao nhất trong số còn lại.",
      ),
      formCard.el,
      h("div", { class: "grid-3-1 section-gap" }, list.el, memory.el),
    );
    list.body.classList.add("flush");
    const openState = new Set();
    const load = shared(() => api("/admin-api/requests?limit=50"));
    const timer = setInterval(loadOptions, 15000);
    return {
      panels: [
        new Panel(list.body, { load, render: (d) => renderList(d, openState) }),
        new Panel(memory.body, { load, render: renderMemory }),
      ],
      destroy: () => clearInterval(timer),
    };
  },
};
