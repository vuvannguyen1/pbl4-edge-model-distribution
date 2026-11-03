// Thư viện giao diện dùng chung (dashboard server + giao diện client). ES module, không build.
// Mọi nội dung lấy từ dữ liệu đều đi qua textContent (h()), không dùng innerHTML.

const SPRITE = new URL("../icons/sprite.svg", import.meta.url).href;
const SVG_NS = "http://www.w3.org/2000/svg";

// ---------------------------------------------------------------- tạo phần tử

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "text") el.textContent = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function icon(name, { label = null, cls = "" } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", `icon ${cls}`.trim());
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `${SPRITE}#i-${name}`);
  svg.append(use);
  return svg;
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// ---------------------------------------------------------------- định dạng

const NF = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });

export const fmt = {
  num: (v, digits = 1) =>
    v === null || v === undefined || Number.isNaN(v) ? "—" : (digits === 2 ? NF2 : NF).format(v),
  int: (v) => (v === null || v === undefined ? "—" : Math.round(v).toLocaleString("vi-VN")),
  mb: (bytes) => (bytes == null ? "—" : `${NF.format(bytes / 1048576)} MB`),
  kbps: (v) => (v ? `${NF.format(v)} kbps` : "—"),
  ms: (v) => (v == null ? "—" : `${NF.format(v)} ms`),
  s: (v) => (v == null ? "—" : `${NF.format(v)} s`),
  pct: (v) => (v == null ? "—" : `${NF.format(v * 100)} %`),
  time(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString("vi-VN", { hour12: false });
  },
  ago(seconds) {
    if (seconds == null) return "chưa rõ";
    if (seconds < 5) return "vừa xong";
    if (seconds < 60) return `${Math.round(seconds)} giây trước`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} phút trước`;
    return `${Math.round(seconds / 3600)} giờ trước`;
  },
  short: (hash, n = 12) => (hash ? `${hash.slice(0, n)}…` : "—"),
  // Câu do server viết ("5.48 ms", "top-1 0.7180") -> cùng kiểu vi-VN với phần còn lại: "5,5 ms", "top-1 71,8 %".
  text(s) {
    if (s == null) return "";
    return String(s)
      .replace(/top-1 ([01]\.\d+)(?![\w.])/g, (_, v) => `top-1 ${fmt.pct(Number(v))}`)
      .replace(/(?<![\w.,])\d+\.\d+(?![\w.])/g, (v) => NF.format(Number(v)));
  },
};

// ---------------------------------------------------------------- badge, trạng thái

const KIND_ICON = {
  success: "check-circle",
  info: "arrows-down-up",
  warning: "warning",
  danger: "x-circle",
  neutral: "minus-circle",
};

export function badge(kind, text, iconName = KIND_ICON[kind]) {
  return h("span", { class: `badge is-${kind}` }, iconName ? icon(iconName) : null, text);
}

export function skeleton(rows = 3) {
  return h(
    "div",
    { class: "skeleton-block", "aria-hidden": "true" },
    Array.from({ length: rows }, (_, i) =>
      h("span", { class: "skeleton", style: `width:${92 - ((i * 17) % 40)}%` }),
    ),
  );
}

export function emptyState(title, hint, command) {
  return h(
    "div",
    { class: "state" },
    h("p", { class: "state-title" }, icon("info"), title),
    hint ? h("p", { class: "muted" }, hint) : null,
    command ? h("code", {}, command) : null,
  );
}

export function errorState(message, onRetry) {
  return h(
    "div",
    { class: "state is-danger", role: "alert" },
    h("p", { class: "state-title" }, icon("x-circle"), "Không tải được dữ liệu"),
    h("p", {}, message),
    onRetry
      ? h(
          "button",
          { class: "btn btn-secondary btn-sm", type: "button", onclick: onRetry },
          icon("arrow-clockwise"),
          "Thử lại",
        )
      : null,
  );
}

// ---------------------------------------------------------------- toast

export function toast(message, kind = "success") {
  let region = document.getElementById("toast-region");
  if (!region) {
    region = h("div", { id: "toast-region", class: "toast-region", "aria-live": "polite" });
    document.body.append(region);
  }
  const item = h("div", { class: `toast is-${kind}` }, icon(KIND_ICON[kind]), h("p", {}, message));
  region.append(item);
  setTimeout(() => item.remove(), 4000);
}

// ---------------------------------------------------------------- API

export class ApiError extends Error {
  constructor(kind, message, status = null, code = null) {
    super(message);
    this.kind = kind; // "network" | "http"
    this.status = status;
    this.code = code;
  }
}

export async function api(url, { method = "GET", body, headers = {}, timeoutMs = 8000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let resp;
  try {
    const opts = { method, headers: { ...headers }, signal: ctrl.signal, cache: "no-store" };
    if (body instanceof Blob) {
      opts.body = body;
    } else if (body !== undefined) {
      opts.body = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
    }
    resp = await fetch(url, opts);
  } catch (err) {
    const reason = err.name === "AbortError" ? "quá thời gian chờ" : "không kết nối được";
    throw new ApiError("network", `Mất kết nối server (${reason}).`);
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const err = data && data.error;
    throw new ApiError(
      "http",
      err ? err.message : `Server trả HTTP ${resp.status}.`,
      resp.status,
      err ? err.code : null,
    );
  }
  return data;
}

// ---------------------------------------------------------------- bảng sắp xếp được

// columns: [{key, label, render?(row), sort?(row) , align?: "right"}]
export function sortableTable({ columns, rows, caption, state, rowKey }) {
  const sorted = [...rows];
  if (state.sortKey) {
    const col = columns.find((c) => c.key === state.sortKey);
    const get = col.sort || ((r) => r[col.key]);
    sorted.sort((a, b) => {
      const x = get(a);
      const y = get(b);
      const cmp =
        x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(y);
      return state.sortDir === "desc" ? -cmp : cmp;
    });
  }
  const table = h("table", { class: "data" });
  if (caption) table.append(h("caption", {}, caption));
  const headRow = h("tr");
  for (const col of columns) {
    const active = state.sortKey === col.key;
    const sortValue = active ? (state.sortDir === "desc" ? "descending" : "ascending") : "none";
    const th = h("th", {
      scope: "col",
      class: col.align === "right" ? "right" : null,
      "aria-sort": col.sortable === false ? null : sortValue,
    });
    if (col.sortable === false) {
      th.append(col.label);
    } else {
      th.append(
        h(
          "button",
          {
            class: "sort-btn",
            type: "button",
            onclick: () => {
              state.sortDir = active && state.sortDir === "asc" ? "desc" : "asc";
              state.sortKey = col.key;
              state.onChange?.();
            },
          },
          col.label,
          icon("caret-right"),
        ),
      );
    }
    headRow.append(th);
  }
  table.append(h("thead", {}, headRow));
  const tbody = h("tbody");
  for (const row of sorted) {
    const tr = h("tr", rowKey ? { dataset: { key: rowKey(row) } } : {});
    for (const col of columns) {
      const content = col.render ? col.render(row) : row[col.key] ?? "—";
      tr.append(h("td", { class: col.align === "right" ? "right" : null }, content));
    }
    tbody.append(tr);
  }
  table.append(tbody);
  return h("div", { class: "table-wrap" }, table);
}

// ---------------------------------------------------------------- biểu đồ

// rows: [{label, value, highlight?, note?}] — vẽ cột ngang, sắp giảm dần, có nhãn số
export function barChart({ rows, format, summary }) {
  const data = rows.filter((r) => r.value != null).sort((a, b) => b.value - a.value);
  if (!data.length) return null;
  const max = Math.max(...data.map((r) => r.value)) || 1;
  return h(
    "figure",
    { class: "stack", style: "margin:0" },
    h(
      "ul",
      { class: "bar-chart", "aria-label": summary },
      data.map((r) =>
        h(
          "li",
          { class: "bar-row" },
          h("span", { class: "bar-label mono" }, r.label),
          h(
            "span",
            { class: "bar-track" },
            h("span", {
              class: `bar-fill${r.highlight ? " is-highlight" : ""}`,
              style: `width:${Math.max(1, (r.value / max) * 80)}%`,
            }),
            h("span", { class: "bar-value" }, format(r.value), r.note ? ` · ${r.note}` : ""),
          ),
        ),
      ),
    ),
    summary ? h("figcaption", { class: "small muted" }, summary) : null,
  );
}

// parts: [{label, value, cls}] — thanh xếp chồng 100 % kèm phần trăm
export function stackedBar(parts, { label }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) return null;
  const text = parts.map((p) => `${p.label} ${fmt.pct(p.value / total)}`).join(", ");
  return h(
    "div",
    {},
    h(
      "div",
      { class: "stacked", role: "img", "aria-label": `${label}: ${text}` },
      parts.map((p) => h("span", { class: p.cls, style: `width:${(p.value / total) * 100}%` })),
    ),
    h(
      "ul",
      { class: "legend" },
      parts.map((p) =>
        h(
          "li",
          {},
          h("span", { class: `swatch ${p.cls}` }),
          `${p.label}: ${fmt.int(p.value)} (${fmt.pct(p.value / total)})`,
        ),
      ),
    ),
  );
}

// items: [{index, source: "server"|"peer", received, bad}]
export function chunkGrid(items, { label }) {
  const got = items.filter((i) => i.received).length;
  return h(
    "div",
    { class: "stack" },
    h(
      "ul",
      { class: "chunk-grid", role: "img", "aria-label": `${label}: đã nhận ${got}/${items.length}` },
      items.map((i) => {
        const state = i.bad ? "hỏng" : i.received ? "đã nhận" : "chưa nhận";
        return h("li", {
          class: `chunk plan-${i.source}${i.received ? " got" : ""}${i.bad ? " bad" : ""}`,
          title: `Miếng ${i.index}: ${i.source}, ${state}`,
        });
      }),
    ),
    chunkLegend(),
  );
}

export function chunkLegend() {
  const item = (cls, text) => h("li", {}, h("span", { class: `chunk ${cls}`, style: "width:16px" }), text);
  return h(
    "ul",
    { class: "legend" },
    item("plan-server got", "Đã nhận từ server (ô đặc)"),
    item("plan-peer got", "Đã nhận từ peer (ô sọc)"),
    item("plan-server", "Chờ, lấy từ server"),
    item("plan-peer", "Chờ, lấy từ peer"),
    item("bad", "Hỏng / bị từ chối"),
  );
}

// ---------------------------------------------------------------- poll định kỳ

// Gọi `tick()` mỗi `intervalMs`; ngừng khi tab ẩn hoặc tạm dừng. Báo trạng thái qua onStatus:
// {state: "live"|"stale"|"offline"|"paused", lastOk: Date|null, error: string|null}
export class Poller {
  constructor({ tick, intervalMs = 2000, onStatus }) {
    this.tick = tick;
    this.intervalMs = intervalMs;
    this.onStatus = onStatus;
    this.failures = 0;
    this.lastOk = null;
    this.paused = false;
    this.timer = null;
    this.running = false;
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.now();
    });
  }

  status(error = null) {
    let state = "live";
    if (this.paused) state = "paused";
    else if (this.failures >= 3) state = "offline";
    else if (this.failures >= 1) state = "stale";
    this.onStatus?.({ state, lastOk: this.lastOk, error });
  }

  async run() {
    if (this.running) return;
    this.running = true;
    clearTimeout(this.timer);
    let error = null;
    try {
      await this.tick();
      this.failures = 0;
      this.lastOk = new Date();
    } catch (err) {
      this.failures += 1;
      error = err.message;
    } finally {
      this.running = false;
    }
    this.status(error);
    this.schedule();
  }

  schedule() {
    clearTimeout(this.timer);
    if (this.paused) return;
    this.timer = setTimeout(() => {
      if (document.hidden) this.schedule();
      else this.run();
    }, this.intervalMs);
  }

  now() {
    if (!this.paused) this.run();
  }

  setPaused(paused) {
    this.paused = paused;
    this.status();
    if (paused) clearTimeout(this.timer);
    else this.run();
  }
}

// Một vùng dữ liệu tự làm mới: giữ dữ liệu cũ khi lỗi, chỉ vẽ lại khi dữ liệu đổi.
export class Panel {
  constructor(container, { load, render, emptyCheck }) {
    this.container = container;
    this.load = load;
    this.render = render;
    this.emptyCheck = emptyCheck;
    this.data = null;
    this.snapshot = null;
    clear(container, skeleton(4));
  }

  async refresh() {
    try {
      const data = await this.load();
      const snapshot = JSON.stringify(data);
      this.container.classList.remove("panel-stale");
      if (snapshot !== this.snapshot) {
        this.snapshot = snapshot;
        this.data = data;
        this.redraw();
      }
    } catch (err) {
      if (this.data === null) {
        clear(this.container, errorState(err.message, () => this.refresh()));
      } else {
        this.container.classList.add("panel-stale");
      }
      throw err;
    }
  }

  redraw() {
    if (this.data === null) return;
    // giữ focus bàn phím khi vẽ lại: tìm lại phần tử cùng loại và cùng nội dung
    const active = document.activeElement;
    const focusKey =
      active && this.container.contains(active)
        ? [active.tagName, active.getAttribute("href"), active.textContent]
        : null;
    clear(this.container, this.render(this.data));
    if (focusKey) {
      const [tag, href, text] = focusKey;
      const again = [...this.container.querySelectorAll(tag)].find(
        (el) => el.getAttribute("href") === href && el.textContent === text,
      );
      again?.focus({ preventScroll: true });
    }
  }
}
