// Log sự kiện: phần cuối file log server trong ngày, lọc theo mức và từ khoá.
import { Panel, api, badge, emptyState, h } from "../../shared/js/ui.js";
import { card, pageHead, requestLink } from "./common.js";

const LEVEL_KIND = { INFO: "neutral", WARNING: "warning", ERROR: "danger", CRITICAL: "danger", DEBUG: "neutral" };

function messageWithLinks(entry) {
  if (!entry.request_id) return entry.message;
  const marker = `[${entry.request_id}]`;
  const at = entry.message.indexOf(marker);
  if (at < 0) return entry.message;
  return [entry.message.slice(0, at), "[", requestLink(entry.request_id), "]", entry.message.slice(at + marker.length)];
}

export default {
  title: "Log sự kiện",
  mount(main, params) {
    const level = h(
      "select",
      { class: "input", id: "log-level" },
      h("option", { value: "" }, "Tất cả"),
      h("option", { value: "WARNING" }, "Cảnh báo trở lên"),
      h("option", { value: "ERROR" }, "Chỉ lỗi"),
    );
    const query = h("input", { class: "input", id: "log-q", type: "search", placeholder: "vd. r-0003, laptopA, peer", value: params[0] || "" });
    const tail = h("select", { class: "input", id: "log-tail" }, ["100", "200", "500"].map((n) => h("option", { value: n, selected: n === "200" }, `${n} dòng`)));
    const follow = h("input", { type: "checkbox", id: "log-follow", checked: true });
    const filters = h(
      "div",
      { class: "form-grid" },
      h("div", { class: "field" }, h("label", { for: "log-q" }, "Tìm"), query),
      h("div", { class: "field" }, h("label", { for: "log-level" }, "Mức"), level),
      h("div", { class: "field" }, h("label", { for: "log-tail" }, "Hiển thị"), tail),
      h("div", { class: "field" }, h("span", { class: "small", style: "font-weight:500" }, "Cuộn"), h("label", { class: "row", style: "min-height:40px" }, follow, "Tự cuộn tới dòng mới")),
    );
    const list = card("Log server hôm nay", "data/logs/server-YYYYMMDD.log · cũ ở trên, mới ở dưới");
    list.body.classList.add("flush");
    const scroller = h("div", { style: "max-height:65dvh;overflow-y:auto", tabindex: "0", "aria-label": "Danh sách log" });
    list.body.append(scroller);
    main.append(
      pageHead("Log sự kiện", "Mọi dòng log của một lượt xin model bắt đầu bằng mã request, bấm vào mã để xem lượt tải đó."),
      h("section", { class: "card card-body" }, filters),
      h("div", { class: "section-gap" }, list.el),
    );
    const panel = new Panel(scroller, {
      load() {
        const qs = new URLSearchParams({ tail: tail.value });
        if (level.value) qs.set("level", level.value);
        if (query.value.trim()) qs.set("q", query.value.trim());
        return api(`/admin-api/logs?${qs}`);
      },
      render(data) {
        if (!data.exists) {
          return emptyState("Hôm nay chưa có file log.", `Server ghi log vào ${data.file} khi có sự kiện đầu tiên.`);
        }
        if (!data.entries.length) {
          return emptyState("Không có dòng log nào khớp bộ lọc.", "Bỏ bớt từ khoá hoặc chọn mức “Tất cả”.");
        }
        return h(
          "ol",
          { class: "log-list" },
          data.entries.map((e) =>
            h(
              "li",
              {},
              h("span", { class: "muted" }, e.time),
              badge(LEVEL_KIND[e.level] || "neutral", e.level, null),
              h("span", { class: "msg" }, messageWithLinks(e)),
            ),
          ),
        );
      },
    });
    const originalRedraw = panel.redraw.bind(panel);
    panel.redraw = () => {
      originalRedraw();
      if (follow.checked) scroller.scrollTop = scroller.scrollHeight;
    };
    let timer = null;
    const refilter = () => {
      clearTimeout(timer);
      timer = setTimeout(() => panel.refresh().catch(() => {}), 300);
    };
    query.addEventListener("input", refilter);
    level.addEventListener("change", refilter);
    tail.addEventListener("change", refilter);
    return { panels: [panel], destroy: () => clearTimeout(timer) };
  },
};
