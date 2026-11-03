// Phần tử dùng lại giữa các màn hình dashboard.
import { badge, fmt, h, icon } from "../../shared/js/ui.js";

// Nhiều panel dùng chung một API: gộp các lần gọi trong cùng một lượt poll thành một.
// Sau khi ghi (đổi trạng thái, upload) gọi .invalidate() để lượt sau đọc lại thật.
export function shared(load, ttlMs = 1000) {
  let at = 0;
  let pending = null;
  const get = () => {
    if (!pending || Date.now() - at > ttlMs) {
      at = Date.now();
      pending = load();
      pending.catch(() => {
        pending = null;
      });
    }
    return pending;
  };
  get.invalidate = () => {
    pending = null;
  };
  return get;
}

export function pageHead(title, description, back = null) {
  return h(
    "div",
    { class: "page-head" },
    h(
      "div",
      {},
      back ? h("a", { class: "back", href: back.href }, icon("arrow-left"), back.text) : null,
      h("h1", {}, title),
      description ? h("p", {}, description) : null,
    ),
  );
}

// Trả {el, body}: body là chỗ Panel vẽ dữ liệu.
export function card(title, description = null, actions = null) {
  const body = h("div", { class: "card-body" });
  const el = h(
    "section",
    { class: "card" },
    h(
      "div",
      { class: "card-header" },
      h("div", {}, h("h2", {}, title), description ? h("p", {}, description) : null),
      actions,
    ),
    body,
  );
  return { el, body };
}

// Ngưỡng "lần cuối liên lạc" theo docs/design-system.md mục 3.2
export function deviceActivity(ageSeconds) {
  if (ageSeconds == null) return badge("neutral", "Chưa rõ");
  if (ageSeconds <= 30) return badge("success", "Đang hoạt động", "wifi-high");
  if (ageSeconds <= 300) return badge("warning", "Im lặng", "hourglass");
  return badge("neutral", "Không liên lạc", "wifi-slash");
}

const REQUEST_STATUS = {
  complete: ["success", "Hoàn tất"],
  downloading: ["info", "Đang tải"],
  stalled: ["warning", "Không báo tiến độ"],
  planned: ["neutral", "Chỉ lập kế hoạch (web)"],
};

export function requestStatus(status) {
  const [kind, text] = REQUEST_STATUS[status] || ["neutral", status];
  return badge(kind, text);
}

export function requestLink(rid) {
  return h("a", { class: "mono", href: `#/transfers/${encodeURIComponent(rid)}` }, rid);
}

export function variantLink(variantId) {
  return variantId
    ? h("a", { class: "mono", href: `#/models/${encodeURIComponent(variantId)}` }, variantId)
    : "—";
}

export function progressBar(received, total) {
  const done = total > 0 && received >= total;
  const pct = total > 0 ? (received / total) * 100 : 0;
  return h(
    "div",
    { class: "stack", style: "gap:var(--space-sm);min-width:140px" },
    h(
      "div",
      {
        class: `progress${done ? " is-done" : ""}`,
        role: "progressbar",
        "aria-valuemin": "0",
        "aria-valuemax": String(total),
        "aria-valuenow": String(received),
        "aria-label": `Đã nhận ${received}/${total} miếng`,
      },
      h("span", { style: `width:${pct}%` }),
    ),
    h("span", { class: "small muted num" }, `${received}/${total} miếng`),
  );
}

export function sourceSplit(counts) {
  const parts = [];
  if (counts.server) parts.push(`${counts.server} server`);
  if (counts.peer) parts.push(`${counts.peer} peer`);
  return parts.length ? parts.join(" + ") : "plan rỗng";
}

export function bandwidthText(r) {
  if (r.requested_bandwidth_kbps) return fmt.kbps(r.requested_bandwidth_kbps);
  if (r.bandwidth_kbps) return `${fmt.kbps(r.bandwidth_kbps)} (giới hạn server)`;
  return "không rõ";
}
