Mặc dù 2 điện thoại khác cấu hình, mô hình phân bố chung 1 mô hình nhưng lại không P2P được
- thay đổi phương thức lưu P2P, kiểm tra xem mô hình này đã được triển khai trên máy nào, và lấy từ máy đó đưa sang, không cần lấy từ server, thông báo nguồn nhận model. Ví dụ: "Bạn đã nhận được mô hình yolox-nano từ máy ..."
- Thêm chức năng đặt tên đăng nhập cho máy của mình trước khi vào giao diện chính, để có thể nhận biết được máy nào đang gửi model cho mình, tránh nhầm lẫn khi có nhiều máy cùng tham gia P2P.
- Phần đánh giá thì thêm đánh giá số sao, không đánh giá điểm, điểm sẽ được tính như sau: 
+ PhoBERT sẽ trả về điểm của bình luận 
+ Điểm = (số sao đánh giá) * 0,2 + (điểm bình luận từ PhoBERT) * 0,8
+ Bỏ trường mô hình đoán đúng/sai, và có thể đánh giá ngay mà không cần chụp ảnh khung hình rồi mới được đánh giá, miễn là trên máy đã có sẵn model.
