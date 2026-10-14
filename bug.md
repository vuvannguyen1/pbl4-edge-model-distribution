# Yêu cầu bổ sung: cache model, chạy offline và gửi đánh giá

## 1. Mục tiêu

Client phải có khả năng:

1. Nhận và lưu model từ server.
2. Tái sử dụng model đã lưu cho các lần detect sau, không tải lại không cần thiết.
3. Chạy detect offline khi server không hoạt động, nếu client đã có model phù hợp.
4. Cho phép người dùng nhập đánh giá bằng lệnh terminal.
5. Gửi đánh giá ngay tới server khi server đang chạy.
6. Lưu tạm đánh giá khi offline và tự động gửi bù khi server hoạt động trở lại.

Nguyên tắc: client không xin hoặc tải lại model trong mỗi lần detect. Client chỉ xin model khi chưa có model phù hợp hoặc model local không còn hợp lệ.

## 2. Trạng thái model trên client

Mỗi model local phải có metadata, tối thiểu gồm:

- `variant_id`;
- tên file model;
- kích thước file;
- SHA-256 toàn bộ file;
- phiên bản model;
- task mà model hỗ trợ;
- input shape, dtype và thông số normalize;
- trạng thái: `partial`, `complete`, hoặc `invalid`.

Model chỉ được coi là sẵn sàng khi:

1. File đã tải đủ.
2. Kích thước khớp metadata.
3. SHA-256 khớp Registry/server.
4. Có thể mở bằng runtime inference.

Nếu một điều kiện không đạt, client phải đánh dấu model là `invalid` và không dùng model đó để detect.

## 3. Quy trình detect khi server đang chạy

### Bước 1: Kiểm tra model local

Trước khi gửi request lên server, client kiểm tra kho model local theo:

- task cần detect;
- `variant_id` được chọn hoặc model phù hợp nhất;
- runtime và kiến trúc CPU;
- dung lượng model;
- SHA-256 và trạng thái `complete`.

### Bước 2: Dùng model local nếu đã có

Nếu tìm thấy model hợp lệ, client không gửi request xin model mới. Client nạp model local, nhận ảnh và trả kết quả detect.

### Bước 3: Xin model nếu chưa có

Nếu không có model hợp lệ, client:

1. Gửi `POST /api/register` để cập nhật thông tin thiết bị nếu cần.
2. Gửi `POST /api/request` để xin model phù hợp.
3. Tải model theo các chunk trong `plan`.
4. Kiểm tra SHA-256 từng chunk và toàn bộ file.
5. Ghi model và metadata vào kho local.
6. Chuyển trạng thái sang `complete`.
7. Nạp model và chạy detect.

Lần detect tiếp theo phải ưu tiên model local, không tải lại model trừ khi metadata, hash, runtime hoặc chính sách server thay đổi.

## 4. Quy trình detect khi server tắt

1. Client thử kết nối server trong thời gian chờ giới hạn.
2. Nếu server không phản hồi, client chuyển sang chế độ offline.
3. Client tìm model có trạng thái `complete` trong kho local.
4. Nếu có model phù hợp, client chạy detect mà không cần server.
5. Nếu không có model phù hợp, client trả lỗi rõ ràng: chưa thể detect offline vì chưa có model.

Trong chế độ offline, client không được xóa model local. Việc server tắt không được coi là lỗi detect nếu model local còn hợp lệ.

## 5. Quy trình gửi đánh giá

### 5.1. Nhập đánh giá bằng terminal

Sau khi detect, người dùng chủ động gọi lệnh terminal để gửi đánh giá. Client cung cấp một CLI, ví dụ:

```text
python -m client.feedback_cli --device-id phoneA --variant-id mobilenetv2_int8
```

CLI yêu cầu người dùng nhập:

- điểm đánh giá;
- dự đoán có đúng hay không;
- nhãn thực tế nếu biết;
- nhận xét tùy chọn;
- thông tin model và lần detect liên quan.

Mỗi feedback phải có `feedback_id` duy nhất để tránh gửi trùng khi retry.

### 5.2. Khi server đang chạy

Client kiểm tra kết nối rồi gửi feedback trực tiếp tới:

```text
POST /api/feedback
```

Server tiếp nhận feedback, chuyển qua mô hình hoặc quy trình đánh giá để xử lý, lưu kết quả và trả phản hồi cho client.

Feedback chỉ được xóa khỏi hàng chờ sau khi server xác nhận thành công.

### 5.3. Khi server tắt

Nếu không kết nối được server:

1. CLI vẫn nhận và kiểm tra nội dung đánh giá.
2. Ghi feedback vào hàng chờ local dạng JSONL.
3. Gắn trạng thái `pending`.
4. Không làm mất feedback và không yêu cầu người dùng nhập lại.

Ví dụ bản ghi:

```json
{
  "feedback_id": "fb-0001",
  "device_id": "phoneA",
  "variant_id": "mobilenetv2_int8",
  "prediction": "Labrador retriever",
  "expected_label": "Labrador retriever",
  "rating": 5,
  "comment": "Kết quả đúng",
  "status": "pending",
  "created_at": "2026-10-14T00:00:00Z"
}
```

### 5.4. Gửi bù khi server mở lại

Khi client phát hiện server hoạt động trở lại hoặc người dùng chạy lệnh đồng bộ:

1. Đọc các bản ghi `pending` theo thứ tự thời gian.
2. Gửi từng bản ghi tới `POST /api/feedback`.
3. Chỉ chuyển sang `sent` sau khi server xác nhận thành công.
4. Nếu lỗi mạng, giữ nguyên `pending` để retry lần sau.

Lệnh đồng bộ dự kiến:

```text
python -m client.feedback_cli --sync-pending --server http://<server-ip>:8000
```

## 6. Thay đổi code cần thực hiện

### Client

- Bổ sung `ModelCache` để quản lý file, metadata, hash và trạng thái model.
- Tách luồng `find_local_model()` khỏi luồng xin model.
- Sửa agent để ưu tiên detect bằng model local.
- Thêm chế độ offline khi request tới server timeout hoặc lỗi kết nối.
- Thêm `client.feedback_cli` cho nhập và gửi đánh giá.
- Thêm `FeedbackQueue` lưu JSONL `pending` và retry khi server trở lại.
- Bảo đảm feedback có `feedback_id` duy nhất và cơ chế idempotency.

### Server

- Giữ `POST /api/request` để phục vụ client chưa có model.
- Tiếp tục trả metadata, hash và plan chunk để client cache model an toàn.
- Cập nhật `POST /api/feedback` để nhận feedback thủ công từ CLI.
- Thêm kiểm tra `feedback_id` để không ghi trùng khi client retry.
- Chuyển feedback qua mô hình hoặc quy trình đánh giá và trả trạng thái xử lý.
- Ghi log thời gian nhận, thiết bị, model, kết quả đánh giá và trạng thái xử lý.

## 7. Tiêu chí hoàn thành

- [x] Detect lần đầu: không có model local thì client xin model và lưu thành công.
- [x] Detect lần sau: có model hợp lệ thì không gửi request xin model.
- [x] Model hỏng hoặc hash sai thì bị từ chối và tải lại khi server sẵn sàng.
- [x] Server tắt nhưng client có model hợp lệ thì detect offline thành công.
- [x] Server tắt và client không có model thì trả thông báo dễ hiểu.
- [x] Lệnh feedback yêu cầu người dùng nhập đánh giá.
- [x] Server chạy: feedback được gửi trực tiếp và qua quy trình đánh giá.
- [x] Server tắt: feedback được lưu `pending`, không bị mất.
- [x] Server mở lại: feedback `pending` được gửi bù và chỉ đánh dấu `sent` sau khi xác nhận.

