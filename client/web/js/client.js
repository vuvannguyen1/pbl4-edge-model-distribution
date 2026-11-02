// Giao diện client: chọn ảnh -> /api/detect (chạy client.agent) -> theo dõi tiến trình -> kết quả,
// đánh giá kết quả, kho model trên máy. Dữ liệu hiển thị luôn đi qua textContent (h()).
import { ApiError, Poller, api, badge, clear, emptyState, fmt, h, icon, toast } from "/shared/js/ui.js";

const $ = (id) => document.getElementById(id);
const SOURCE_TEXT = {
  server: ["info", "Vừa xin và tải từ server"],
  cache: ["success", "Dùng model có sẵn trên máy"],
};
const STATUS_KIND = { complete: "success", partial: "warning", invalid: "danger" };
const FEEDBACK_STATUS = {
  sent: ["success", "Đã gửi"],
  pending: ["warning", "Chờ gửi"],
  rejected: ["danger", "Bị từ chối"],
};
const VERDICT = { positive: ["success", "Tích cực"], neutral: ["warning", "Trung tính"], negative: ["danger", "Tiêu cực"] };

let state = null;
let chosenFile = null;
let previewUrl = null;

// ---------------------------------------------------------------- trạng thái chung

function renderConn(s) {
  $("device-id").textContent = s.device_id;
  document.title = `${s.device_id} · PBL4 Client`;
  const parts = [
    s.server_online
      ? badge("success", "Server đang kết nối", "cloud-check")
      : badge("danger", "Không kết nối được server", "cloud-slash"),
  ];
  if (s.pending_feedback) parts.push(badge("warning", `${s.pending_feedback} đánh giá chờ gửi`, "hourglass"));
  clear($("conn"), parts);
  $("sync-btn").hidden = !(s.pending_feedback && s.server_online);
}

function renderModels(s) {
  const share = s.sharing;
  $("sharing-text").textContent = share.enabled
    ? share.running
      ? `Đang chia sẻ P2P ở cổng ${share.port}: ${share.shared.join(", ") || "chưa có"} · đã gửi ${share.served} miếng`
      : `Chia sẻ P2P ở cổng ${share.port} sẽ bật sau lần nhận diện đầu tiên`
    : "Không chia sẻ P2P (chạy với --peer-port 9000 để bật)";
  if (!s.models.length) {
    clear(
      $("models-body"),
      emptyState(
        "Máy chưa có model nào.",
        s.server_online
          ? "Bấm Nhận diện: client sẽ xin server một model phù hợp rồi tải về."
          : "Cần kết nối server ít nhất một lần để tải model, sau đó mới nhận diện offline được.",
      ),
    );
    return;
  }
  clear(
    $("models-body"),
    h(
      "ul",
      { class: "model-list" },
      s.models.map((m) =>
        h(
          "li",
          {},
          h("span", { class: "mono" }, m.variant_id),
          badge(STATUS_KIND[m.status] || "neutral", m.status),
          m.active ? h("span", { class: "tag" }, "đang dùng") : null,
          h("span", { class: "small muted" }, [m.precision, m.size_bytes ? fmt.mb(m.size_bytes) : null, m.top1_accuracy ? `top-1 ${fmt.pct(m.top1_accuracy)}` : null].filter(Boolean).join(" · ")),
          m.reason ? h("span", { class: "small muted" }, m.reason) : null,
        ),
      ),
    ),
  );
}

async function refreshState() {
  const s = await api("/api/state", { timeoutMs: 5000 });
  const firstTime = state === null;
  const lastChanged = JSON.stringify(state?.last_detection) !== JSON.stringify(s.last_detection);
  state = s;
  renderConn(s);
  renderModels(s);
  if (firstTime || lastChanged) renderFeedback();
}

const poller = new Poller({
  tick: refreshState,
  intervalMs: 3000,
  onStatus({ state: st }) {
    if (st === "offline") {
      clear($("conn"), badge("danger", "Mất kết nối với chương trình client (đã tắt?)", "x-circle"));
    }
  },
});

// ---------------------------------------------------------------- chọn ảnh

function showPreview(src, alt) {
  const img = $("preview");
  img.src = src;
  img.alt = alt;
  img.hidden = false;
  $("dropzone").classList.add("has-image");
  setImageError("");
}

function setImageError(message) {
  clear($("image-error"), message ? [icon("warning"), message] : []);
}

function chooseFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    setImageError("File này không phải ảnh.");
    return;
  }
  if (file.size > 15 * 1024 * 1024) {
    setImageError("Ảnh lớn hơn 15 MB.");
    return;
  }
  chosenFile = file;
  $("sample").value = "";
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  showPreview(previewUrl, `Ảnh đã chọn: ${file.name}`);
}

function setupPicker() {
  const zone = $("dropzone");
  $("file").addEventListener("change", (e) => chooseFile(e.target.files[0]));
  for (const ev of ["dragenter", "dragover"]) {
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("is-over");
    });
  }
  for (const ev of ["dragleave", "drop"]) zone.addEventListener(ev, () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    chooseFile(e.dataTransfer.files[0]);
  });
  $("sample").addEventListener("change", (e) => {
    const name = e.target.value;
    if (!name) return;
    chosenFile = null;
    $("file").value = "";
    showPreview(`/samples/${encodeURIComponent(name)}`, `Ảnh mẫu ${name}`);
  });
  api("/api/samples")
    .then((data) => {
      $("sample").append(...data.samples.map((n) => h("option", { value: n }, n)));
    })
    .catch(() => {});
}

// ---------------------------------------------------------------- nhận diện

function fieldError(id, message) {
  const input = $(id);
  input.setAttribute("aria-invalid", message ? "true" : "false");
  clear($(`${id}-error`), message ? [icon("warning"), message] : []);
  return message;
}

function positiveOrEmpty(id) {
  const v = $(id).value;
  if (v === "") return null;
  return Number(v) > 0 ? null : "Phải là số lớn hơn 0.";
}

function renderProgress(job, logBox) {
  const done = job.status !== "running";
  const statusBadge = {
    running: badge("info", "Đang chạy", "hourglass"),
    done: badge("success", "Hoàn tất"),
    error: badge("danger", "Lỗi"),
  }[job.status];
  clear($("job-status"), statusBadge);
  const steps = job.sections.map((s, i) => {
    const last = i === job.sections.length - 1;
    const current = last && !done;
    return h(
      "li",
      { class: current ? "is-current" : "is-done" },
      icon(current ? "hourglass" : "check-circle"),
      s.step ? `Bước ${s.step}/7 · ${s.title}` : s.title,
    );
  });
  const c = job.chunks;
  const chunkBlock = c.total
    ? h(
        "div",
        { class: "stack", style: "gap:var(--space-sm)" },
        h(
          "div",
          {
            class: `progress${c.done >= c.total ? " is-done" : ""}`,
            role: "progressbar",
            "aria-valuemin": "0",
            "aria-valuemax": String(c.total),
            "aria-valuenow": String(c.done),
            "aria-label": "Tiến độ tải model",
          },
          h("span", { style: `width:${(c.done / c.total) * 100}%` }),
        ),
        h(
          "p",
          { class: "small row" },
          `Đã tải ${c.done}/${c.total} miếng`,
          h("span", { class: "swatch src-peer", "aria-hidden": "true" }),
          `peer ${c.peer}`,
          h("span", { class: "swatch src-server", "aria-hidden": "true" }),
          `server ${c.server}`,
        ),
      )
    : null;
  const err = job.error
    ? h(
        "div",
        { class: "result-box", role: "alert" },
        h("p", { class: "state-title", style: "color:var(--color-danger)" }, icon("x-circle"), "Không nhận diện được"),
        h("p", {}, job.error),
      )
    : null;
  const details = $("log-details") || h("details", { class: "disclosure", id: "log-details" }, h("summary", {}, icon("caret-right"), "Nhật ký chi tiết (như trên terminal)"), logBox);
  clear($("progress-body"), h("ol", { class: "steps" }, steps), chunkBlock, err, details);
}

function renderResult(r) {
  const [kind, text] = r.offline ? ["warning", "Offline: dùng model có sẵn trên máy"] : SOURCE_TEXT[r.source] || ["neutral", r.source];
  const row = (label, value) => [h("dt", {}, label), h("dd", {}, value)];
  const top = r.top5[0];
  clear(
    $("result-body"),
    h(
      "div",
      { class: "result-grid" },
      h(
        "div",
        { class: "stack" },
        h("p", { class: "sr-only" }, top ? `Kết quả cao nhất: ${top.label}, ${fmt.pct(top.probability)}` : ""),
        h(
          "ol",
          { class: "top5", "aria-label": "Top-5 dự đoán" },
          r.top5.map((p) =>
            h(
              "li",
              {},
              h("span", { class: "line" }, h("span", {}, p.label), h("span", { class: "num" }, fmt.pct(p.probability))),
              h("div", { class: "progress", "aria-hidden": "true" }, h("span", { style: `width:${p.probability * 100}%` })),
            ),
          ),
        ),
      ),
      h(
        "dl",
        { class: "dl-grid" },
        row("Model", h("span", { class: "mono" }, r.variant.id)),
        row("Nguồn model", badge(kind, text)),
        r.request_id ? row("Request", h("span", { class: "mono" }, r.request_id)) : null,
        row("Latency (trung vị)", `${fmt.ms(r.latency_ms_median)} · ${r.runs} lần`),
        row("Nạp model", fmt.ms(r.load_ms)),
        row("RAM đỉnh", r.peak_ram_mb != null ? `${fmt.num(r.peak_ram_mb)} MB` : "không đo được"),
        r.download
          ? row("Tải model", `${fmt.ms(r.download_ms)} · ${r.download.chunks_from_peer} miếng peer, ${r.download.chunks_from_server} server${r.download.bad_chunks ? `, ${r.download.bad_chunks} lần lỗi` : ""}`)
          : null,
        r.decision
          ? row("Server quyết định", r.decision.action === "switch" ? badge("warning", `Đổi sang ${r.decision.new_variant_id}`) : badge("success", "Giữ model này"))
          : null,
        r.decision?.reason ? row("Lý do", h("span", { class: "small" }, r.decision.reason)) : null,
      ),
    ),
  );
  $("result-card").hidden = false;
}

async function followJob(jobId) {
  const logBox = h("pre", { class: "log-box", tabindex: "0", "aria-label": "Nhật ký" });
  $("log-details")?.remove();
  let since = 0;
  for (;;) {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}?since=${since}`, { timeoutMs: 5000 });
    } catch (err) {
      clear($("job-status"), badge("warning", "Mất liên lạc với client, đang thử lại"));
      await new Promise((r) => setTimeout(r, 1500));
      continue;
    }
    if (job.lines.length) {
      logBox.textContent += job.lines.join("\n") + "\n";
      logBox.scrollTop = logBox.scrollHeight;
    }
    since = job.line_count;
    renderProgress(job, logBox);
    if (job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, 700));
  }
}

function setupDetect() {
  const form = $("detect-form");
  const btn = $("detect-btn");
  for (const id of ["bandwidth", "deadline"]) {
    $(id).addEventListener("blur", () => fieldError(id, positiveOrEmpty(id)));
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const sample = $("sample").value;
    if (!chosenFile && !sample) {
      setImageError("Chọn một ảnh hoặc ảnh mẫu trước.");
      $("dropzone").scrollIntoView({ block: "center" });
      return;
    }
    const bad = ["bandwidth", "deadline"].find((id) => fieldError(id, positiveOrEmpty(id)));
    if (bad) {
      $(bad).closest("details").open = true;
      $(bad).focus();
      return;
    }
    const qs = new URLSearchParams();
    if (sample) qs.set("sample", sample);
    else qs.set("name", chosenFile.name);
    for (const id of ["bandwidth", "deadline", "preferred"]) if ($(id).value.trim()) qs.set(id, $(id).value.trim());
    if ($("refresh").checked) qs.set("refresh", "1");

    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    btn.lastElementChild.textContent = "Đang nhận diện…";
    $("progress-card").hidden = false;
    $("result-card").hidden = true;
    try {
      const { job_id } = await api(`/api/detect?${qs}`, {
        method: "POST",
        body: sample ? undefined : chosenFile,
        timeoutMs: 60000,
      });
      $("progress-card").scrollIntoView({ behavior: "smooth", block: "start" });
      const job = await followJob(job_id);
      if (job.status === "done") {
        renderResult(job.result);
        $("result-title").setAttribute("tabindex", "-1");
        $("result-title").focus({ preventScroll: true });
        $("result-card").scrollIntoView({ behavior: "smooth", block: "start" });
        toast(`Nhận diện xong: ${job.result.top5[0]?.label ?? "?"}`);
      }
      poller.now();
    } catch (err) {
      const message = err instanceof ApiError && err.code === "BUSY" ? err.message : `Không bắt đầu được: ${err.message}`;
      clear($("progress-body"), h("div", { class: "result-box", role: "alert" }, h("p", {}, message)));
      clear($("job-status"), badge("danger", "Lỗi"));
    } finally {
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
      btn.lastElementChild.textContent = "Nhận diện";
    }
  });
}

// ---------------------------------------------------------------- đánh giá

function choiceGroup(name, legend, options) {
  return h(
    "fieldset",
    { class: "field" },
    h("legend", {}, legend, h("span", { class: "req", "aria-hidden": "true" }, " *")),
    h("div", { class: "choice-row" }, options.map(([value, label, iconName]) => h("label", { class: "choice" }, h("input", { type: "radio", name, value }), h("span", {}, iconName ? icon(iconName) : null, label)))),
    h("span", { class: "field-error", id: `${name}-error`, "aria-live": "polite" }),
  );
}

async function renderHistory(target) {
  try {
    const data = await api("/api/feedback");
    if (!data.records.length) {
      clear(target);
      return;
    }
    clear(
      target,
      h("h3", {}, "Đánh giá đã gửi trên máy này"),
      h(
        "ul",
        { class: "history section-gap" },
        data.records.slice(0, 6).map((r) => {
          const [kind, text] = FEEDBACK_STATUS[r.status] || ["neutral", r.status];
          const verdict = VERDICT[r.verdict];
          return h(
            "li",
            {},
            h("span", { class: "muted" }, fmt.time(r.created_at)),
            h("span", { class: "mono" }, r.variant_id),
            `${r.rating}/5 · ${r.correct ? "đúng" : "sai"}`,
            badge(kind, text),
            verdict ? badge(verdict[0], verdict[1]) : null,
          );
        }),
      ),
    );
  } catch {
    clear(target);
  }
}

function renderFeedback() {
  const body = $("feedback-body");
  const last = state?.last_detection;
  const history = h("div");
  if (!last) {
    clear(body, emptyState("Chưa có lần nhận diện nào để đánh giá.", "Nhận diện một ảnh trước, rồi quay lại đây."), history);
    renderHistory(history);
    return;
  }
  const expected = h("input", { class: "input", id: "fb-expected", maxlength: "200", placeholder: "nếu biết" });
  const comment = h("textarea", { class: "input", id: "fb-comment", maxlength: "1000" });
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, icon("paper-plane-tilt"), h("span", {}, "Gửi đánh giá"));
  const result = h("div", { "aria-live": "polite" });
  const form = h(
    "form",
    { class: "stack", novalidate: true },
    h(
      "p",
      { class: "small muted" },
      `Lần nhận diện ${fmt.time(last.detected_at)} · model `,
      h("span", { class: "mono" }, last.variant_id),
      ` · dự đoán “${last.prediction ?? "?"}”${last.confidence != null ? ` (${fmt.pct(last.confidence)})` : ""}`,
    ),
    h(
      "div",
      { class: "form-grid" },
      choiceGroup("rating", "Điểm", [1, 2, 3, 4, 5].map((n) => [String(n), String(n), "star"])),
      choiceGroup("correct", "Dự đoán có đúng không?", [["yes", "Đúng", "thumbs-up"], ["no", "Sai", "thumbs-down"]]),
    ),
    h(
      "div",
      { class: "form-grid" },
      h("div", { class: "field" }, h("label", { for: "fb-expected" }, "Nhãn thực tế"), expected, h("span", { class: "hint" }, "Bỏ trống nếu dự đoán đúng hoặc không biết")),
      h("div", { class: "field" }, h("label", { for: "fb-comment" }, "Nhận xét"), comment),
    ),
    h("div", { class: "form-actions" }, submit),
    result,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const rating = form.querySelector("input[name=rating]:checked")?.value;
    const correct = form.querySelector("input[name=correct]:checked")?.value;
    clear($("rating-error"), rating ? [] : [icon("warning"), "Chọn điểm từ 1 đến 5."]);
    clear($("correct-error"), correct ? [] : [icon("warning"), "Chọn Đúng hoặc Sai."]);
    if (!rating || !correct) {
      form.querySelector(`input[name=${!rating ? "rating" : "correct"}]`).focus();
      return;
    }
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.lastElementChild.textContent = "Đang gửi…";
    try {
      const resp = await api("/api/feedback", {
        method: "POST",
        body: { variant_id: last.variant_id, rating: Number(rating), correct: correct === "yes", expected_label: expected.value, comment: comment.value },
        timeoutMs: 15000,
      });
      const [kind, text] = FEEDBACK_STATUS[resp.status] || ["neutral", resp.status];
      const verdict = VERDICT[resp.evaluation?.verdict];
      clear(
        result,
        h(
          "div",
          { class: "result-box stack" },
          h("p", { class: "row" }, badge(kind, text), verdict ? badge(verdict[0], verdict[1]) : null),
          resp.evaluation ? h("p", {}, resp.evaluation.reason) : null,
          resp.message ? h("p", {}, resp.message) : null,
        ),
      );
      toast(resp.status === "sent" ? "Server đã nhận đánh giá." : "Đã lưu đánh giá, sẽ gửi bù khi có server.", resp.status === "sent" ? "success" : "warning");
      form.querySelectorAll("input[type=radio]").forEach((r) => (r.checked = false));
      comment.value = "";
      expected.value = "";
      renderHistory(history);
      poller.now();
    } catch (err) {
      clear(result, h("div", { class: "result-box", role: "alert" }, h("p", {}, err.message)));
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.lastElementChild.textContent = "Gửi đánh giá";
    }
  });
  clear(body, form, history);
  renderHistory(history);
}

function setupSync() {
  const btn = $("sync-btn");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const r = await api("/api/sync", { method: "POST", timeoutMs: 30000 });
      toast(r.error ? `Gửi được ${r.sent}, còn ${r.remaining} chờ: ${r.error}` : `Đã gửi bù ${r.sent} đánh giá.`, r.error ? "warning" : "success");
      renderFeedback();
      poller.now();
    } catch (err) {
      toast(err.message, "danger");
    } finally {
      btn.disabled = false;
    }
  });
}

// Tải lại trang giữa chừng: hiện lại lượt nhận diện gần nhất (đang chạy thì theo tiếp).
async function resumeLastJob() {
  try {
    await refreshState();
  } catch {
    return;
  }
  if (!state.job) return;
  $("progress-card").hidden = false;
  const job = await followJob(state.job.job_id);
  if (job.status === "done") renderResult(job.result);
}

setupPicker();
setupDetect();
setupSync();
resumeLastJob().finally(() => poller.now());
