# Design system giao diện web PBL4

Áp dụng cho **dashboard server** (`/ui`) và **giao diện client** (trang web do tiến trình client mở ở
`localhost`). Chỉ có một theme sáng, tối ưu cho trình chiếu bằng máy chiếu.

## 1. Nguồn gốc

- Sinh bằng skill **ui-ux-pro-max** (`G:\Ky5Nam3\PBL4\.claude\skills\ui-ux-pro-max\`):

  ```cmd
  python "G:/Ky5Nam3/PBL4/.claude/skills/ui-ux-pro-max/scripts/search.py" "analytics dashboard data-dense light" --design-system --persist -p "PBL4 Edge Distribution" --density 8 --motion 2 --variance 3 --output-dir .
  ```

  Kết quả gốc của skill: [`design-system/pbl4-edge-distribution/MASTER.md`](../design-system/pbl4-edge-distribution/MASTER.md).
- Tra bổ sung: `--domain color` (palette sáng), `--domain style` (Data-Dense Dashboard),
  `--domain chart` (so sánh danh mục, tiến độ), `--domain ux` (bảng, trạng thái live, loading/rỗng/lỗi),
  `--domain google-fonts` (font có bộ ký tự `vietnamese`), `--domain icons`.
- Lần chạy đầu với truy vấn "technical monitoring dashboard edge devices real-time" trả palette
  **nền tối**, trái yêu cầu chỉ dùng theme sáng, nên đã chạy lại một lần với truy vấn hẹp hơn
  (đúng quy tắc "retry once" của skill).

**File này là quyết định cuối cùng và ghi đè `MASTER.md`** ở những điểm liệt kê tại mục 9.
Mọi giá trị dưới đây đã có trong [`client/web/shared/css/tokens.css`](../client/web/shared/css/tokens.css).
Component chỉ dùng biến (`var(--...)`), không viết mã màu trực tiếp.

## 2. Style

**Minimalism & Swiss Style + Data-Dense Dashboard** (cả hai đều hỗ trợ theme sáng, rủi ro
accessibility thấp).

- Lưới rõ ràng, nhiều khoảng trắng có chủ đích, chữ sans-serif, tương phản cao.
- Card phân tách bằng **viền mảnh, không đổ bóng**. Bóng chỉ dùng cho lớp nổi (dropdown, dialog).
- Không trang trí: không gradient, không glow, không ảnh nền, không hiệu ứng nhấc card khi hover.
- Dữ liệu là trung tâm: hàng KPI, bảng sắp xếp được, biểu đồ cột có nhãn trực tiếp.

## 3. Màu

### 3.1. Màu nền tảng

| Token | Mã | Dùng cho | Tương phản |
|---|---|---|---|
| `--color-primary` | `#1E40AF` | Nút chính, link, mục điều hướng đang chọn, viền focus | chữ trắng trên nền này 8.72:1 |
| `--color-primary-hover` | `#1E3A8A` | Hover/nhấn nút chính, màu tiêu đề | chữ trắng 10.36:1 |
| `--color-on-primary` | `#FFFFFF` | Chữ trên nền primary | |
| `--color-secondary` | `#3B82F6` | Cột biểu đồ, thanh tiến độ (không dùng cho chữ) | 3.68:1 trên card |
| `--color-accent` | `#D97706` | Đánh dấu variant được chọn/nổi bật (không dùng cho chữ, không làm nền nút) | 3.19:1 trên card |
| `--color-background` | `#F8FAFC` | Nền trang | |
| `--color-card` | `#FFFFFF` | Nền card, bảng, form | |
| `--color-text` | `#0F172A` | Chữ thân, số liệu | 17.06:1 trên nền trang |
| `--color-heading` | `#1E3A8A` | Tiêu đề trang/khối | 10.36:1 trên card |
| `--color-muted` | `#E9EEF6` | Nền header bảng, hàng hover, skeleton | |
| `--color-muted-text` | `#475569` | Chữ phụ, nhãn, chú thích | 7.58:1 trên card, 6.50:1 trên muted |
| `--color-border` | `#CBD5E1` | Viền card, đường kẻ bảng (trang trí) | |
| `--color-border-strong` | `#64748B` | Viền input, ô chunk chưa nhận (thành phần UI) | 4.76:1 trên card |
| `--color-ring` | `#1E40AF` | Vòng focus bàn phím | 8.72:1 |
| `--color-destructive` | `#DC2626` | Nút nguy hiểm (nền), chữ trắng | 4.83:1 |

### 3.2. Màu trạng thái

Màu **không bao giờ đứng một mình**: mỗi trạng thái luôn có icon + chữ (mục 8.6 yêu cầu triển khai,
quy tắc `color-not-only` của skill).

| Trạng thái | Chữ / icon | Nền nhạt | Icon (Phosphor) | Dùng ở đâu |
|---|---|---|---|---|
| **success**: online, complete, sha256 OK, `positive`, `keep` | `#166534` (6.49:1 trên nền nhạt) | `#DCFCE7` | `check-circle` | Thiết bị đang liên lạc, request xong, đánh giá tốt |
| **info**: đang truyền, đang xử lý | `#1D4ED8` (5.49:1) | `#DBEAFE` | `arrows-down-up` | Request đang tải, đang gửi form |
| **warning**: dữ liệu cũ, `switch`, `needs_review`, `neutral`, pending | `#92400E` (6.37:1) | `#FEF3C7` | `warning` | Mất cập nhật vài giây, server đổi variant, đánh giá chờ gửi |
| **danger**: lỗi, peer bị cấm, hash sai, `invalid`, `negative`, mất kết nối server | `#B91C1C` (5.30:1) | `#FEE2E2` | `x-circle` | Lỗi API, chunk hỏng, model hỏng |
| **neutral**: offline/không rõ, chưa có dữ liệu | `#475569` (6.92:1) | `#F1F5F9` | `minus-circle` | Thiết bị lâu không liên lạc, chưa đo |

Quy ước trạng thái thiết bị theo "lần cuối liên lạc" (`last_seen`):

| Nhãn | Điều kiện | Kiểu |
|---|---|---|
| Đang hoạt động | ≤ 30 s | success |
| Im lặng | 30 s – 5 phút | warning |
| Không liên lạc | > 5 phút | neutral |

### 3.3. Nguồn dữ liệu (lưới chunk, biểu đồ P2P)

| Nguồn | Màu | Phân biệt không cần màu |
|---|---|---|
| Server | `--color-source-server` `#1D4ED8` | Ô đặc |
| Peer (P2P) | `--color-source-peer` `#C2410C` | Ô **kẻ sọc chéo** |
| Chưa nhận | Viền `#64748B` nét đứt, nền trắng | Ô rỗng |
| Hỏng / bị từ chối | `#B91C1C` | Có dấu ✕ vẽ bằng SVG |

Xanh dương và cam là cặp an toàn với người mù màu. Độ sáng hai màu gần nhau (1.29:1), vì vậy
**bắt buộc** có sọc cho peer, và chú thích (legend) luôn hiện cạnh lưới.

## 4. Chữ

| Vai trò | Font | Ghi chú |
|---|---|---|
| Tiêu đề + thân | **Fira Sans** 400/500/600/700 | Có bộ ký tự `vietnamese` |
| Số liệu kỹ thuật: `device_id`, `request_id`, hash, log, đơn vị | **JetBrains Mono** 400/600 | Có `vietnamese` (log server có chữ tiếng Việt) |
| Dự phòng | `"Segoe UI", Roboto, Arial, sans-serif` / `Consolas, "Cascadia Mono", monospace` | Khi font chưa tải được |

- Font được **lưu trong repo** (woff2, giấy phép OFL) để demo qua hotspot không có internet vẫn
  đúng font. Không dùng Google Fonts CDN.
- Số trong bảng/KPI dùng `font-variant-numeric: tabular-nums` để cột không nhảy khi cập nhật.

Thang cỡ chữ (đã tăng so với mức 12–14px của style Data-Dense vì **trình chiếu**):

| Token | Cỡ | Dùng cho |
|---|---|---|
| `--text-xs` | 13px | Chú thích phụ, chú thích lưới chunk (cỡ nhỏ nhất được dùng) |
| `--text-sm` | 14px | Nhãn, badge, header bảng |
| `--text-base` | 16px | Thân, ô bảng, input |
| `--text-lg` | 18px | Tiêu đề card |
| `--text-xl` | 22px | Tiêu đề trang |
| `--text-2xl` | 28px | Tiêu đề lớn (UI client) |
| `--text-kpi` | 34px | Số KPI |

Line-height: thân 1.5, tiêu đề 1.25. Đậm nhạt: thân 400, nhãn 500, tiêu đề 600, KPI 700.

## 5. Khoảng cách, bo góc, bóng

Khoảng cách theo `MASTER.md` (density 8/10), cộng thêm một bậc cho lề trang:

| Token | Giá trị |
|---|---|
| `--space-xs` | 2px |
| `--space-sm` | 4px |
| `--space-md` | 8px |
| `--space-lg` | 12px |
| `--space-xl` | 16px |
| `--space-2xl` | 24px |
| `--space-3xl` | 32px |
| `--space-4xl` | 48px |

- Bo góc (Swiss, sắc nét hơn mức 8–16px của MASTER): `--radius-sm` 4px (badge, ô chunk),
  `--radius-md` 6px (nút, input), `--radius-lg` 8px (card, dialog), `--radius-full` (chấm trạng thái).
- Bóng: `--shadow-sm` `0 1px 2px rgba(15,23,42,.06)` cho thanh trên cùng;
  `--shadow-lg` `0 10px 15px rgba(15,23,42,.12)` chỉ cho dropdown/dialog.

## 6. Bố cục

- Breakpoint: 375 / 768 / 1024 / 1440px.
- **Dashboard:** thanh điều hướng bên trái rộng 240px ở ≥ 1024px. Dưới 1024px, điều hướng thu
  thành thanh trên cùng có nút mở menu. Thanh trạng thái trên cùng cao 56px. Nội dung rộng tối đa
  1440px, lưới 12 cột, khe 16px.
- **UI client:** một cột, rộng tối đa 960px, dùng tốt trên trình duyệt điện thoại (Termux).
- Bảng bọc trong khung cuộn ngang `overflow-x: auto`. Trang không bao giờ cuộn ngang.
- Hàng bảng cao 40px. Nút và input cao tối thiểu 40px (UI client trên điện thoại: 44px).
- Lớp z-index: nội dung 0, thanh trên cùng 10, dropdown 20, banner mất kết nối 30, dialog 40,
  toast 50.

## 7. Chuyển động

- Chỉ dùng CSS transition, **không dùng GSAP**. Chỉ animate `opacity`, `transform`,
  `background-color`, `border-color`.
- `--duration-fast` 150ms (hover, màu), `--duration-base` 200ms (đổi trạng thái),
  `--duration-slow` 300ms (thanh tiến độ). Easing `--ease-out` `cubic-bezier(0.2, 0, 0, 1)`.
- `prefers-reduced-motion: reduce` thì mọi thời lượng về 0 và thanh tiến độ hiện ngay giá trị cuối.
- Không animate khi dữ liệu poll về mà không đổi; chỉ ô/hàng thay đổi mới chớp nền `--color-muted`
  một lần.

## 8. Mẫu tương tác bắt buộc

**Trạng thái của mọi component dữ liệu:**

| Trạng thái | Hiển thị |
|---|---|
| Đang tải lần đầu | Skeleton giữ đúng kích thước (không nhảy bố cục) |
| Rỗng | Câu giải thích + lệnh/thao tác để có dữ liệu, ví dụ "Chưa có thiết bị nào. Chạy `python -m client.agent ...`". Không hiện số 0 như thể đã đo |
| Lỗi | `role="alert"`, nêu nguyên nhân + nút **Thử lại** |
| Mất kết nối server | Banner trên cùng màu danger; dữ liệu cũ vẫn hiện nhưng mờ đi và ghi "dữ liệu lúc HH:MM:SS" |

**Chỉ báo cập nhật trực tiếp** (thanh trên cùng, `role="status" aria-atomic="true"`):

- "Đang cập nhật · 19:30:12" (success) → "Dữ liệu cũ · 12 giây trước" (warning, sau 2 lần poll
  lỗi) → "Mất kết nối server" (danger).
- Có nút **Tạm dừng / Tiếp tục** cập nhật tự động. Ngừng poll khi tab bị ẩn.

**Form và thao tác:**

- Nhãn luôn hiện (không chỉ placeholder). Lỗi hiện ngay dưới ô, nối bằng `aria-describedby`.
- Nút gửi: khóa + chữ "Đang gửi..." trong lúc chờ, sau đó báo thành công/thất bại.
- Toast `aria-live="polite"`, tự tắt sau 4 giây, không lấy focus.
- Mỗi màn hình có đúng **một** nút chính.

**Bàn phím và accessibility:**

- Focus: viền 3px `--color-ring`, cách 2px; không bao giờ `outline: none` mà không thay thế.
- Có link "Bỏ qua tới nội dung chính". Tiêu đề theo thứ tự h1 → h2 → h3.
- Bảng: header sắp xếp được bằng Enter/Space, có `aria-sort`.
- Biểu đồ: luôn có nhãn giá trị trực tiếp + bảng số liệu tương ứng + một câu tóm tắt.
- Icon cạnh chữ: `aria-hidden="true"`. Nút chỉ có icon: có `aria-label`.
- Mọi phần tử bấm được có `cursor: pointer`.

**Biểu đồ** (theo `--domain chart`):

- So sánh variant (thời gian tải, nạp, latency, RAM): **biểu đồ cột ngang**, sắp giảm dần, nhãn số
  trên từng cột, cùng một màu `--color-secondary`. Variant đang được chọn tô `--color-accent`, có thêm
  chữ "đang dùng".
- Tỉ lệ chunk peer/server: **thanh xếp chồng 100%** kèm phần trăm. Không dùng biểu đồ tròn.
- Tiến độ một request: **lưới ô** (kiểu waffle), một ô một chunk, theo mục 3.3.
- Vẽ bằng SVG/CSS, không thêm thư viện biểu đồ.

**Icon:** bộ **Phosphor**, kiểu Regular (outline), 20px (24px ở UI client). Lưu dạng SVG sprite trong
repo (giấy phép MIT), không dùng emoji.

## 9. Điểm ghi đè so với `MASTER.md`

| MASTER.md | Quyết định | Lý do |
|---|---|---|
| Font tiêu đề Fira Code | Fira Sans cho tiêu đề, JetBrains Mono cho dữ liệu kỹ thuật | Fira Code không có bộ ký tự `vietnamese` (theo `google-fonts.csv` của skill) |
| Import Google Fonts | Font woff2 lưu trong repo | Demo qua hotspot có thể không có internet |
| `.btn-primary` nền `#D97706` chữ trắng | Nền `--color-primary`, chữ trắng | `#D97706`/trắng chỉ đạt 3.19:1 (cần 4.5:1) |
| Foreground `#1E3A8A` | Thân chữ `#0F172A`, tiêu đề `#1E3A8A` | Chữ gần đen dễ đọc hơn trên máy chiếu |
| Border `#DBEAFE` | `#CBD5E1` (trang trí), `#64748B` (input) | `#DBEAFE` trên trắng 1.22:1, viền input cần ≥ 3:1 |
| `.card` nền `#F8FAFC`, `cursor:pointer`, nhấc lên khi hover | Nền trắng, viền mảnh, không hover | Card không phải nút; trùng nền trang; tránh xê dịch bố cục |
| Bo góc 8–16px, bóng `md` cho card | 4/6/8px, card không bóng | Đúng tinh thần Swiss "sharp shadows if any" |
| Modal `backdrop-filter: blur` | Lớp phủ `rgba(15,23,42,.5)` không blur | Máy chiếu/điện thoại yếu; blur không cần cho demo |
| Motion: GSAP Scroll Reveal | CSS transition 150–300ms, không reveal khi cuộn | Dashboard cập nhật liên tục, không phải trang landing; không thêm thư viện |
| Page Pattern "Enterprise Gateway" | Không áp dụng | Pattern dành cho trang landing; đây là ứng dụng dashboard |
| Cỡ chữ 12–14px (style Data-Dense) | Thân 16px, bảng 16px, nhỏ nhất 13px | Trình chiếu |
| Trạng thái success/warning tông 700 | Tông 800 (`#166534`, `#92400E`) | Tông 700 chỉ đạt 4.57 và 4.51:1 trên nền nhạt, sát ngưỡng khi chiếu |

## 10. Checklist trước khi giao (từ skill, phần áp dụng cho web)

- [ ] Không dùng emoji làm icon; icon cùng một bộ, cùng kiểu.
- [ ] Mọi cặp chữ/nền ≥ 4.5:1; thành phần UI ≥ 3:1.
- [ ] Focus bàn phím luôn thấy được; thứ tự Tab khớp thứ tự nhìn.
- [ ] Màu không là tín hiệu duy nhất.
- [ ] `prefers-reduced-motion` được tôn trọng.
- [ ] Hiển thị đúng ở 375 / 768 / 1024 / 1440px, không cuộn ngang trang.
- [ ] Mọi component có loading / rỗng / lỗi / mất kết nối.
- [ ] Không có nội dung bị thanh cố định che.
- [ ] `cursor: pointer` trên mọi phần tử bấm được.
