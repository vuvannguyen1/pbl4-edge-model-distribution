// Thiết bị & P2P: cấu hình, lớp thiết bị, lần cuối liên lạc, chia sẻ peer.
import { Panel, api, badge, emptyState, fmt, h, sortableTable } from "../../shared/js/ui.js";
import { card, deviceActivity, pageHead, requestLink, variantLink } from "./common.js";

function peerCell(d) {
  if (!d.peer_origin) return badge("neutral", "Không mở cổng peer");
  if (d.peer_banned) return h("div", { class: "stack", style: "gap:4px" }, badge("danger", "Bị cấm (gửi sai)"), h("span", { class: "mono small" }, d.peer_origin));
  if (d.sharing) return h("div", { class: "stack", style: "gap:4px" }, badge("success", "Đang chia sẻ", "share-network"), h("span", { class: "mono small" }, d.peer_origin));
  return h("div", { class: "stack", style: "gap:4px" }, badge("neutral", "Chưa có model đủ"), h("span", { class: "mono small" }, d.peer_origin));
}

export default {
  title: "Thiết bị & P2P",
  mount(main) {
    const list = card(
      "Thiết bị đã đăng ký",
      "Đang hoạt động: liên lạc server trong 30 giây (client đang mở tự gửi heartbeat 10 giây/lần) hoặc đang chia sẻ P2P. Đến 5 phút là im lặng.",
    );
    main.append(
      pageHead(
        "Thiết bị & P2P",
        "Hai thiết bị cùng lớp (cùng hệ điều hành, loại CPU, nhóm RAM) mới chia sẻ model cho nhau qua P2P.",
      ),
      list.el,
    );
    const sort = { sortKey: "device_id", sortDir: "asc" };
    const panel = new Panel(list.body, {
      load: () => api("/admin-api/devices"),
      render(data) {
        if (!data.devices.length) {
          return emptyState(
            "Chưa có thiết bị nào đăng ký.",
            "Thiết bị xuất hiện ở đây sau khi gọi POST /api/register, ví dụ khi chạy client agent:",
            "python -m client.agent --server http://<IP>:8000 --device-id laptopA --peer-port 9000 --serve",
          );
        }
        return sortableTable({
          caption: `${data.devices.length} thiết bị · cập nhật ${fmt.time(data.now)}`,
          state: sort,
          rows: data.devices,
          columns: [
            // lớp thiết bị nằm dưới tên (không tách cột) để bảng vừa màn 1366 px
            { key: "device_id", label: "Thiết bị / lớp", render: (d) => h("div", { class: "stack", style: "gap:2px" }, h("span", { class: "mono" }, d.device_id), h("span", { class: "mono small muted" }, d.device_class)) },
            { key: "last_seen_age_s", label: "Hoạt động", render: (d) => h("div", { class: "stack", style: "gap:4px" }, d.active ? badge("success", "Đang hoạt động", "wifi-high") : deviceActivity(d.last_seen_age_s), h("span", { class: "small muted" }, fmt.ago(d.last_seen_age_s))) },
            {
              key: "ram_mb",
              label: "Cấu hình",
              render: (d) =>
                h(
                  "div",
                  { class: "stack", style: "gap:2px" },
                  h("span", {}, `${d.platform} · ${d.cpu_arch} · RAM ${fmt.num(d.ram_mb / 1024)} GB`),
                  h("span", { class: "small muted" }, d.runtime),
                ),
            },
            { key: "peer", label: "P2P", sortable: false, render: peerCell },
            {
              key: "complete_variants",
              label: "Model đã có đủ",
              sortable: false,
              render: (d) => (d.complete_variants.length ? h("div", { class: "row" }, d.complete_variants.map(variantLink)) : h("span", { class: "muted" }, "chưa có")),
            },
            { key: "request_count", label: "Lượt xin", align: "right", render: (d) => (d.last_request_id ? h("div", { class: "stack", style: "gap:2px;justify-items:end" }, String(d.request_count), h("span", { class: "small", style: "white-space:nowrap" }, requestLink(d.last_request_id))) : "0") },
          ],
        });
      },
    });
    sort.onChange = () => panel.redraw();
    return { panels: [panel] };
  },
};
