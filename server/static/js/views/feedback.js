// Đánh giá model: số đo hiệu năng (agent), đánh giá người dùng (+ form gửi), nhận xét văn bản.
import {
  Panel,
  api,
  badge,
  barChart,
  emptyState,
  fmt,
  h,
  icon,
  sortableTable,
  toast,
} from "../../shared/js/ui.js";
import { card, pageHead, shared, variantLink } from "./common.js";

const TABS = [
  ["metrics", "Hiệu năng (số đo agent)", "gauge"],
  ["manual", "Đánh giá người dùng", "thumbs-up"],
  ["text", "Nhận xét văn bản", "chat-circle-text"],
];

const VERDICT = {
  positive: ["success", "Tích cực"],
  neutral: ["warning", "Trung tính"],
  negative: ["danger", "Tiêu cực"],
};

function tabs(active) {
  return h(
    "nav",
    { class: "tabs", "aria-label": "Loại đánh giá" },
    TABS.map(([key, label, iconName]) =>
      h(
        "a",
        {
          class: "tab",
          href: `#/feedback/${key}`,
          "aria-current": key === active ? "page" : null,
        },
        icon(iconName),
        label,
      ),
    ),
  );
}

// ---------------------------------------------------------------- hiệu năng

function metricsTab(root) {
  const charts = card("So sánh theo variant", "Trung vị các lần đo thật do agent gửi về");
  const groups = card("Tổng hợp");
  const recent = card("Các lần đo gần nhất");
  groups.body.classList.add("flush");
  recent.body.classList.add("flush");
  root.append(charts.el, h("div", { class: "section-gap" }, groups.el), h("div", { class: "section-gap" }, recent.el));
  const load = shared(() => api("/admin-api/feedback?kind=metrics&limit=50"));
  const empty = () =>
    emptyState(
      "Chưa có số đo nào.",
      "Client gửi số đo (thời gian tải, nạp, latency, FPS) sau lần nhận diện đầu tiên: mở link /app trên điện thoại/laptop, hoặc chạy client Python:",
      "python -m client.agent --server http://<IP>:8000 --device-id laptopA --image samples\\dog.jpg",
    );
  const groupSort = { sortKey: "variant_id", sortDir: "asc" };
  const recentSort = { sortKey: null, sortDir: "asc" };
  const chart = (rows, key, format, title) =>
    h("div", { class: "stack" }, h("h3", {}, title), barChart({ rows: rows.map((g) => ({ label: g.variant_id, value: g[key] })), format, summary: `${title}, trung vị, sắp giảm dần.` }) || h("p", { class: "muted" }, "Chưa đo."));
  const panels = [
    new Panel(charts.body, {
      load,
      render: (d) =>
        d.groups.length
          ? h(
              "div",
              { class: "grid-2" },
              chart(d.groups, "latency_ms_median", fmt.ms, "Latency suy luận"),
              chart(d.groups, "load_ms_median", fmt.ms, "Thời gian nạp model"),
              chart(d.groups, "download_ms_median", fmt.ms, "Thời gian tải"),
              chart(d.groups, "peak_ram_mb_median", (v) => `${fmt.num(v)} MB`, "RAM đỉnh"),
            )
          : empty(),
    }),
    new Panel(groups.body, {
      load,
      render: (d) =>
        d.groups.length
          ? sortableTable({
              state: groupSort,
              rows: d.groups,
              columns: [
                { key: "variant_id", label: "Variant", render: (g) => variantLink(g.variant_id) },
                { key: "count", label: "Số lần đo", align: "right" },
                { key: "download_ms_median", label: "Tải", align: "right", render: (g) => fmt.ms(g.download_ms_median) },
                { key: "load_ms_median", label: "Nạp", align: "right", render: (g) => fmt.ms(g.load_ms_median) },
                { key: "latency_ms_median", label: "Latency", align: "right", render: (g) => fmt.ms(g.latency_ms_median) },
                { key: "peak_ram_mb_median", label: "RAM", align: "right", render: (g) => (g.peak_ram_mb_median == null ? "—" : `${fmt.num(g.peak_ram_mb_median)} MB`) },
                { key: "fps_median", label: "FPS camera", align: "right", render: (g) => (g.fps_median == null ? "—" : fmt.num(g.fps_median)) },
                { key: "chunks_from_peer", label: "Miếng peer / server", align: "right", render: (g) => `${g.chunks_from_peer} / ${g.chunks_from_server}` },
                { key: "switch_count", label: "Lần bị đổi", align: "right" },
              ],
            })
          : empty(),
    }),
    new Panel(recent.body, {
      load,
      render: (d) =>
        d.records.length
          ? sortableTable({
              caption: `${d.total} bản ghi trong ${d.sources.join(", ")}`,
              state: recentSort,
              rows: d.records,
              columns: [
                { key: "timestamp", label: "Lúc", render: (r) => fmt.time(r.timestamp) },
                { key: "device_id", label: "Thiết bị", render: (r) => h("span", { class: "mono" }, r.device_name || r.device_id) },
                { key: "variant_id", label: "Variant", render: (r) => variantLink(r.variant_id) },
                { key: "download_ms", label: "Tải", align: "right", render: (r) => fmt.ms(r.download_ms) },
                { key: "latency_ms_median", label: "Latency", align: "right", render: (r) => fmt.ms(r.latency_ms_median) },
                { key: "fps", label: "FPS", align: "right", render: (r) => (r.fps == null ? "—" : fmt.num(r.fps)) },
                { key: "top1_label", label: "Vật thể tin cậy nhất", render: (r) => (r.top1_label ? `${r.top1_label} (${fmt.pct(r.top1_confidence)})` : r.detections === 0 ? "không thấy vật thể" : "—") },
                { key: "decision", label: "Quyết định", sortable: false, render: (r) => (r.decision?.action === "switch" ? badge("warning", `Đổi sang ${r.decision.new_variant_id}`) : badge("success", "Giữ")) },
                { key: "data_kind", label: "Loại số liệu", render: (r) => h("span", { class: "tag" }, r.data_kind || "—") },
              ],
            })
          : empty(),
    }),
  ];
  for (const [panel, state] of [[panels[1], groupSort], [panels[2], recentSort]]) state.onChange = () => panel.redraw();
  return panels;
}

// ---------------------------------------------------------------- đánh giá người dùng

function randomId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `fb-web-${[...bytes].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}

function feedbackForm(onDone) {
  const ids = (n) => `fb-${n}`;
  const deviceList = h("datalist", { id: "fb-device-list" });
  const device = h("input", { class: "input", id: ids("device"), list: "fb-device-list", required: true, maxlength: "32", autocomplete: "off", "aria-describedby": "fb-device-hint fb-device-error" });
  const variant = h("select", { class: "input", id: ids("variant"), required: true, "aria-describedby": "fb-variant-error" });
  const prediction = h("input", { class: "input", id: ids("prediction"), maxlength: "200", placeholder: "vd. tiger cat" });
  const expected = h("input", { class: "input", id: ids("expected"), maxlength: "200", placeholder: "nếu biết" });
  const comment = h("textarea", { class: "input", id: ids("comment"), maxlength: "1000" });
  const rating = h(
    "fieldset",
    { class: "field", "aria-describedby": "fb-rating-error" },
    h("legend", {}, "Số sao", h("span", { class: "req", "aria-hidden": "true" }, " *")),
    h("div", { class: "choice-row" }, [1, 2, 3, 4, 5].map((n) => h("label", { class: "choice" }, h("input", { type: "radio", name: "rating", value: String(n) }), h("span", {}, icon("star"), String(n))))),
    h("span", { class: "field-error", id: "fb-rating-error", "aria-live": "polite" }),
  );
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, icon("paper-plane-tilt"), "Gửi đánh giá");
  const result = h("div", { "aria-live": "polite" });
  const labelled = (id, text, control, hint, required) =>
    h(
      "div",
      { class: "field" },
      h("label", { for: id }, text, required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null),
      control,
      hint ? h("span", { class: "hint", id: `${id}-hint` }, hint) : null,
      h("span", { class: "field-error", id: `${id}-error`, "aria-live": "polite" }),
    );
  const form = h(
    "form",
    { novalidate: true },
    deviceList,
    h(
      "div",
      { class: "form-grid" },
      labelled(ids("device"), "Thiết bị", device, "Chữ, số, _ và -, tối đa 32 ký tự", true),
      labelled(ids("variant"), "Variant được đánh giá", variant, null, true),
      labelled(ids("prediction"), "Dự đoán của model", prediction),
      labelled(ids("expected"), "Nhãn thực tế", expected),
    ),
    h("div", { class: "form-grid section-gap" }, rating),
    h("div", { class: "section-gap" }, labelled(ids("comment"), "Nhận xét", comment)),
    h("div", { class: "form-actions" }, submit, h("span", { class: "small muted" }, "Gửi qua POST /api/feedback, cùng đường với client.feedback_cli.")),
    result,
  );
  const setError = (key, message) => {
    form.querySelector(`#fb-${key}-error`).replaceChildren(...(message ? [icon("warning"), message] : []));
    const input = form.querySelector(`#fb-${key}`);
    input?.setAttribute("aria-invalid", message ? "true" : "false");
  };
  device.addEventListener("blur", () => setError("device", device.value && !/^[A-Za-z0-9_-]{1,32}$/.test(device.value) ? "Chỉ gồm chữ, số, _ và -, tối đa 32 ký tự." : null));

  (async () => {
    try {
      const [devs, vars] = await Promise.all([api("/admin-api/devices"), api("/admin-api/variants")]);
      deviceList.replaceChildren(...devs.devices.map((d) => h("option", { value: d.device_id })));
      variant.replaceChildren(...vars.variants.map((v) => h("option", { value: v.variant_id }, v.variant_id)));
    } catch (err) {
      setError("variant", `Không tải được danh sách: ${err.message}`);
    }
  })();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ratingValue = form.querySelector("input[name=rating]:checked")?.value;
    const checks = [
      ["device", !device.value ? "Nhập mã thiết bị." : !/^[A-Za-z0-9_-]{1,32}$/.test(device.value) ? "Chỉ gồm chữ, số, _ và -, tối đa 32 ký tự." : null, device],
      ["variant", variant.value ? null : "Chọn variant.", variant],
      ["rating", ratingValue ? null : "Chọn từ 1 đến 5 sao.", form.querySelector("input[name=rating]")],
    ];
    checks.forEach(([key, msg]) => setError(key, msg));
    const bad = checks.find(([, msg]) => msg);
    if (bad) {
      bad[2].focus();
      return;
    }
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.lastChild.textContent = "Đang gửi…";
    const text = (input) => input.value.trim() || null;
    try {
      const resp = await api("/api/feedback", {
        method: "POST",
        body: {
          feedback_id: randomId(),
          device_id: device.value,
          variant_id: variant.value,
          rating: Number(ratingValue),
          prediction: text(prediction),
          confidence: null,
          expected_label: text(expected),
          comment: text(comment),
          request_id: null,
          detected_at: null,
          created_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
        },
      });
      const [kind, label] = VERDICT[resp.evaluation.verdict];
      result.replaceChildren(
        h(
          "div",
          { class: "result-box stack" },
          h("p", { class: "row" }, badge("success", "Server đã nhận"), badge(kind, label), resp.evaluation.needs_review ? badge("warning", "Cần xem lại variant") : null),
          h("p", {}, fmt.text(resp.evaluation.reason)),
          h("p", { class: "small muted mono" }, resp.feedback_id),
        ),
      );
      form.querySelectorAll("input[type=radio]").forEach((r) => (r.checked = false));
      comment.value = "";
      toast("Đã gửi đánh giá.");
      onDone();
    } catch (err) {
      result.replaceChildren(h("div", { class: "result-box", role: "alert" }, h("p", { class: "row" }, badge("danger", "Chưa gửi được")), h("p", {}, err.message)));
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.lastChild.textContent = "Gửi đánh giá";
    }
  });
  return form;
}

function manualTab(root, ctx) {
  const formCard = card("Gửi đánh giá", "Dùng khi demo trên máy không chạy client.feedback_cli");
  const groups = card("Tổng hợp theo variant", "Thống kê mới nhất server tính sau mỗi đánh giá");
  const recent = card("Đánh giá gần nhất");
  groups.body.classList.add("flush");
  recent.body.classList.add("flush");
  formCard.body.append(feedbackForm(() => ctx.refresh()));
  root.append(formCard.el, h("div", { class: "section-gap" }, groups.el), h("div", { class: "section-gap" }, recent.el));
  const load = shared(() => api("/admin-api/feedback?kind=manual&limit=50"));
  const empty = () =>
    emptyState(
      "Chưa có đánh giá nào.",
      "Gửi bằng form bên trên, hoặc trên máy client sau khi nhận diện:",
      "python -m client.feedback_cli --device-id laptopA --server http://<IP>:8000",
    );
  const sortA = { sortKey: "variant_id", sortDir: "asc" };
  const sortB = { sortKey: null, sortDir: "asc" };
  const panels = [
    new Panel(groups.body, {
      load,
      render: (d) =>
        d.groups.length
          ? sortableTable({
              state: sortA,
              rows: d.groups,
              columns: [
                { key: "variant_id", label: "Variant", render: (g) => variantLink(g.variant_id) },
                { key: "variant_feedback_count", label: "Số đánh giá", align: "right" },
                { key: "variant_avg_score", label: "Điểm TB", align: "right", render: (g) => (g.variant_avg_score == null ? "—" : `${fmt.num(g.variant_avg_score, 2)} / 5`) },
                { key: "variant_avg_rating", label: "Sao TB", align: "right", render: (g) => `${fmt.num(g.variant_avg_rating)} / 5` },
                { key: "needs_review", label: "Tình trạng", render: (g) => (g.needs_review ? badge("warning", "Cần xem lại") : badge("success", "Ổn")) },
              ],
            })
          : empty(),
    }),
    new Panel(recent.body, {
      load,
      render: (d) =>
        d.records.length
          ? sortableTable({
              caption: `${d.total} đánh giá`,
              state: sortB,
              rows: d.records,
              columns: [
                { key: "received_at", label: "Lúc", render: (r) => fmt.time(r.received_at) },
                { key: "device_id", label: "Thiết bị", render: (r) => h("span", { class: "mono" }, r.device_name || r.device_id) },
                { key: "variant_id", label: "Variant", render: (r) => variantLink(r.variant_id) },
                { key: "rating", label: "Sao", align: "right", render: (r) => `${r.rating} / 5` },
                { key: "final_score", label: "Điểm", align: "right", sort: (r) => r.evaluation?.final_score ?? r.rating, render: (r) => fmt.num(r.evaluation?.final_score ?? r.rating, 2) },
                { key: "correct", label: "Dự đoán", render: (r) => h("span", {}, r.correct == null ? null : r.correct ? badge("success", "Đúng") : badge("danger", "Sai"), " ", r.prediction || "") },
                { key: "expected_label", label: "Nhãn thực tế", render: (r) => r.expected_label || "—" },
                { key: "comment", label: "Nhận xét", sortable: false, render: (r) => h("span", { class: "wrap-anywhere" }, r.comment || "—") },
                { key: "verdict", label: "Đánh giá", sortable: false, render: (r) => { const [k, t] = VERDICT[r.evaluation?.verdict] || ["neutral", "—"]; return badge(k, t); } },
              ],
            })
          : empty(),
    }),
  ];
  sortA.onChange = () => panels[0].redraw();
  sortB.onChange = () => panels[1].redraw();
  return panels;
}

// ---------------------------------------------------------------- nhận xét văn bản (Lực)

function textTab(root) {
  const chart = card("Phân bố nhãn", "Server chấm câu nhận xét thành 1–5 điểm bằng từ khoá (selector/feedback_classifier.py)");
  const recent = card("Nhận xét gần nhất", "Điểm ≤ 2 được đánh dấu để huấn luyện lại ML selector");
  recent.body.classList.add("flush");
  root.append(chart.el, h("div", { class: "section-gap" }, recent.el));
  const load = shared(() => api("/admin-api/feedback?kind=text&limit=50"));
  const empty = () =>
    emptyState(
      "Chưa có nhận xét văn bản nào.",
      "Gửi một câu nhận xét tự do từ máy client:",
      'python -m client.user_feedback --server http://<IP>:8000 --device-id laptopA --variant yolox_nano --comment "nhận diện đúng, tốc độ tốt"',
    );
  const sort = { sortKey: null, sortDir: "asc" };
  const scoreKind = (s) => (s >= 4 ? "success" : s === 3 ? "warning" : "danger");
  const panels = [
    new Panel(chart.body, {
      load,
      render: (d) =>
        d.groups.length
          ? barChart({ rows: d.groups.map((g) => ({ label: `${g.score} · ${g.label}`, value: g.count })), format: (v) => `${v} nhận xét`, summary: `${d.total} nhận xét theo nhãn.` })
          : empty(),
    }),
    new Panel(recent.body, {
      load,
      render: (d) =>
        d.records.length
          ? sortableTable({
              state: sort,
              rows: d.records,
              columns: [
                { key: "timestamp", label: "Lúc", render: (r) => fmt.time(r.timestamp) },
                { key: "device_id", label: "Thiết bị", render: (r) => h("span", { class: "mono" }, r.device_id) },
                { key: "distributed_variant", label: "Variant", render: (r) => variantLink(r.distributed_variant) },
                { key: "feedback_text", label: "Nhận xét", sortable: false, render: (r) => h("span", { class: "wrap-anywhere" }, r.feedback_text) },
                { key: "score", label: "Nhãn", render: (r) => badge(scoreKind(r.score), `${r.score} · ${r.label}`) },
                { key: "flag_for_retrain", label: "Huấn luyện lại", render: (r) => (r.flag_for_retrain ? badge("warning", "Có") : badge("neutral", "Không")) },
              ],
            })
          : empty(),
    }),
  ];
  sort.onChange = () => panels[1].redraw();
  return panels;
}

export default {
  title: "Đánh giá model",
  mount(main, params, ctx) {
    const active = TABS.some(([k]) => k === params[0]) ? params[0] : "metrics";
    const panelRoot = h("div", { class: "section-gap" });
    main.append(
      pageHead(
        "Đánh giá model",
        "Số đo do agent tự gửi sau mỗi lần nhận diện, đánh giá của người dùng (điểm + đúng/sai) và nhận xét dạng văn bản.",
      ),
      tabs(active),
      panelRoot,
    );
    const build = { metrics: metricsTab, manual: manualTab, text: textTab }[active];
    return { panels: build(panelRoot, ctx) };
  },
};
