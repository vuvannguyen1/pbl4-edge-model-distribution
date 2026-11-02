// Khung dashboard: router theo hash, poll 2 giây, chỉ báo cập nhật trực tiếp.
import { Poller, clear, fmt, h, icon } from "../shared/js/ui.js";
import overview from "./views/overview.js";
import devices from "./views/devices.js";
import models from "./views/models.js";
import decisions from "./views/decisions.js";
import transfers from "./views/transfers.js";
import feedback from "./views/feedback.js";
import logs from "./views/logs.js";

const VIEWS = { overview, devices, models, decisions, transfers, feedback, logs };
const POLL_MS = 2000;

const main = document.getElementById("main");
const liveStatus = document.getElementById("live-status");
const liveToggle = document.getElementById("live-toggle");
const banner = document.getElementById("offline-banner");
const bannerText = document.getElementById("offline-text");
const sidebar = document.getElementById("sidebar");
const navToggle = document.getElementById("nav-toggle");

let current = null; // {panels, destroy}

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const name = parts[0] && VIEWS[parts[0]] ? parts[0] : "overview";
  return { name, params: parts.slice(1).map(decodeURIComponent) };
}

async function tick() {
  if (!current) return;
  const results = await Promise.allSettled(current.panels.map((p) => p.refresh()));
  const failed = results.find((r) => r.status === "rejected");
  if (failed) throw failed.reason;
}

const poller = new Poller({
  tick,
  intervalMs: POLL_MS,
  onStatus({ state, lastOk, error }) {
    const at = lastOk ? fmt.time(lastOk.toISOString()) : "chưa có";
    const ago = lastOk ? fmt.ago((Date.now() - lastOk.getTime()) / 1000) : "chưa có";
    const views = {
      live: ["success", "check-circle", `Đang cập nhật · ${at}`],
      stale: ["warning", "warning", `Dữ liệu cũ · ${ago}`],
      offline: ["danger", "cloud-slash", "Mất kết nối server"],
      paused: ["neutral", "pause", `Đã tạm dừng · ${at}`],
    };
    const [kind, iconName, text] = views[state];
    liveStatus.className = `badge is-${kind}`;
    clear(liveStatus, icon(iconName), text);
    banner.hidden = state !== "offline";
    if (state === "offline") {
      bannerText.textContent = `Mất kết nối server (${error || "không phản hồi"}). Dữ liệu đang hiện là bản lúc ${at}.`;
    }
  },
});

function mount() {
  const { name, params } = parseRoute();
  current?.destroy?.();
  const view = VIEWS[name];
  clear(main);
  current = view.mount(main, params, { refresh: () => poller.now() }) || { panels: [] };
  current.panels ||= [];
  document.title = `${view.title} · PBL4 Edge`;
  for (const link of sidebar.querySelectorAll("a[data-route]")) {
    if (link.dataset.route === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  sidebar.classList.remove("open");
  navToggle.setAttribute("aria-expanded", "false");
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  poller.now();
}

liveToggle.addEventListener("click", () => {
  const paused = !poller.paused;
  poller.setPaused(paused);
  liveToggle.setAttribute("aria-pressed", String(paused));
  clear(liveToggle, icon(paused ? "play" : "pause"), h("span", {}, paused ? "Tiếp tục" : "Tạm dừng"));
});

navToggle.addEventListener("click", () => {
  const open = !sidebar.classList.contains("open");
  sidebar.classList.toggle("open", open);
  navToggle.setAttribute("aria-expanded", String(open));
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && sidebar.classList.contains("open")) {
    sidebar.classList.remove("open");
    navToggle.setAttribute("aria-expanded", "false");
    navToggle.focus();
  }
});

window.addEventListener("hashchange", mount);
mount();
