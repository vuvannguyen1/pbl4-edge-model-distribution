# PIPELINE FINE-TUNING PHOBERT CHO PHÂN LOẠI CẢM XÚC BÌNH LUẬN TIẾNG VIỆT (5 NHÃN)

> **Mục tiêu:** Xây dựng mô hình phân loại bình luận tiếng Việt thành 5 mức độ cảm xúc có thứ tự từ rất tiêu cực đến rất tích cực, sử dụng `vinai/phobert-base-v2` làm pretrained encoder và fine-tune trên dữ liệu mục tiêu.
>
> **Định hướng triển khai:** Google Colab GPU T4 16GB hoặc GPU tương đương; PyTorch + Hugging Face Transformers; lưu checkpoint và kết quả vào Google Drive.

---

## 1. Tổng quan bài toán

Đây là bài toán **5-class ordinal sentiment classification**. Năm lớp không chỉ khác nhau về ý nghĩa mà còn có thứ tự cường độ:

| Label gốc | Tên nhãn | Tiêu chí gán nhãn |
|---:|---|---|
| `1` | Rất tiêu cực | Lỗi nghiêm trọng, không dùng được, hỏng, trải nghiệm cực kỳ tệ hoặc cực kỳ thất vọng. |
| `2` | Tiêu cực | Có vấn đề rõ ràng như chậm, sai, chưa đạt yêu cầu, chất lượng kém hoặc không hài lòng; nhưng chưa đến mức nghiêm trọng nhất. |
| `3` | Trung tính | Bình thường, tạm ổn, nhận xét mang tính mô tả; không thể hiện rõ thái độ tích cực hoặc tiêu cực. |
| `4` | Tích cực | Hài lòng, hoạt động tốt, đáp ứng yêu cầu, trải nghiệm tốt. |
| `5` | Rất tích cực | Khen ngợi mạnh, xuất sắc, rất nhanh, vượt kỳ vọng, cực kỳ hài lòng. |

### 1.1. Đầu vào và đầu ra

- **Input:** Một bình luận tiếng Việt, có thể chứa teencode, emoji, viết tắt, lỗi chính tả, hashtag hoặc URL.
- **Output chính:** Một nhãn nguyên `1`, `2`, `3`, `4` hoặc `5`.
- **Output mở rộng:** Xác suất của từng nhãn, độ tin cậy, điểm cảm xúc kỳ vọng và nhãn dạng chữ.

Ví dụ:

```text
Input: Ứng dụng chạy rất mượt, phản hồi nhanh, dùng cực kỳ thích.
Output: 5 - Rất tích cực
```

### 1.2. Lưu ý về bản chất nhãn

Năm nhãn có thứ tự, do đó dự đoán `4` thay vì `5` thường ít nghiêm trọng hơn dự đoán `1` thay vì `5`. Pipeline cần đánh giá đồng thời:

1. Mô hình dự đoán đúng lớp hay không: Accuracy, Precision, Recall, Macro-F1.
2. Mô hình lệch bao nhiêu mức: MAE và Quadratic Weighted Kappa (QWK).

**Không nên chỉ tối ưu Accuracy.** Nếu dữ liệu mất cân bằng, mô hình có thể dự đoán tốt lớp phổ biến nhưng bỏ sót lớp 1 hoặc lớp 5.

---

## 2. Nguồn dữ liệu

Không có một bộ dữ liệu công khai phổ biến nào chắc chắn khớp hoàn toàn với định nghĩa 5 nhãn ở trên. Vì vậy, nên xây dựng dữ liệu theo hai tầng:

- **Tầng A – Dữ liệu nguồn:** Các corpus sentiment tiếng Việt hiện có, dùng để tham khảo, kiểm tra pipeline hoặc huấn luyện phụ.
- **Tầng B – Dữ liệu mục tiêu 5 nhãn:** Bình luận/review có rating 1–5 hoặc bình luận được người gán nhãn theo đúng guideline của dự án. Đây là dữ liệu quyết định chất lượng cuối cùng.

### 2.1. Nguồn dataset có sẵn

| Nguồn | Nội dung và nhãn | Vai trò trong pipeline | Lưu ý |
|---|---|---|---|
| [UIT-ViSFD](https://huggingface.co/datasets/visolex/ViSFD) | Bình luận đánh giá smartphone; có `comment`, `n_star` 1–5 và nhãn cảm xúc theo khía cạnh. | **Nguồn ưu tiên để thử tạo nhãn 5 mức yếu (weak labels)** từ `n_star`; phù hợp miền review sản phẩm. | `n_star` là rating của người dùng, không phải nhãn cảm xúc được xác minh tuyệt đối. Cần kiểm tra văn bản và rating có mâu thuẫn không. Nhãn theo khía cạnh không đồng nhất với nhãn tổng thể của bình luận. Kiểm tra license và điều kiện sử dụng của bản dữ liệu cụ thể. |
| [AIVIVN 2019 Sentiment Analysis](https://github.com/undertheseanlp/NLP-Vietnamese-progress/blob/master/tasks/sentiment_analysis.md) | Review tiếng Việt, nhãn Positive/Negative. | Dùng tham khảo, thử nghiệm domain review hoặc làm bài toán phụ trước khi fine-tune trên dữ liệu 5 nhãn. | **Không được tự động quy đổi Positive/Negative thành nhãn 1–5** vì không có đủ thông tin cường độ. |
| [UIT-VSFC](https://github.com/kietnv/VietnameseDatasets) | Phản hồi sinh viên; Positive/Negative/Neutral và chủ đề phản hồi. | Dùng làm dữ liệu bổ trợ để mô hình làm quen cách biểu đạt cảm xúc tiếng Việt. | Miền giáo dục có thể khác bình luận sản phẩm/dịch vụ. Corpus có điều kiện truy cập/thỏa thuận sử dụng; kiểm tra trước khi dùng. Không tự động quy đổi 3 lớp thành 5 lớp. |
| [UIT-VSMEC](https://huggingface.co/datasets/visolex/UIT-VSMEC) | Bình luận mạng xã hội, 7 nhóm cảm xúc (6 cảm xúc cơ bản + Other). | Tham khảo cách xử lý văn bản mạng xã hội, teencode và cảm xúc. Có thể dùng trong thí nghiệm auxiliary-task riêng. | Đây là emotion recognition, không phải thang hài lòng 1–5. Không map trực tiếp các cảm xúc sang 5 mức ordinal. |
| [VLSP 2016 Sentiment](https://vlsp.org.vn/resources-vlsp2016) | Corpus sentiment tiếng Việt, có Positive/Negative/Neutral. | Dữ liệu tham khảo hoặc huấn luyện phụ. | Cần điền form và ký thỏa thuận truy cập theo hướng dẫn của VLSP. Không đủ nhãn để tạo 5 mức một cách đáng tin cậy. |

**Khuyến nghị:** Ưu tiên xây dựng tập dữ liệu đúng miền và đủ 5 nhãn. Các corpus 2/3/7 lớp có thể hữu ích để thử nghiệm hoặc học biểu diễn, nhưng không nên gộp nhãn tùy tiện vào tập 5 lớp chính.

### 2.2. Nguồn tự thu thập dữ liệu

#### A. Review có rating 1–5 – lựa chọn tốt nhất để tạo nhãn ban đầu

1. **Review sản phẩm/dịch vụ từ hệ thống của chính dự án:** Xuất `review_text`, `rating`, `product_id`, `created_at` nếu bạn có quyền truy cập. Đây là nguồn phù hợp nhất khi mục tiêu là đánh giá mức độ hài lòng.
2. **Google Play:** Google Play Developer API có tài nguyên review gồm nội dung bình luận và số sao. API `reviews.list` yêu cầu OAuth scope `androidpublisher`; cần có quyền phù hợp với ứng dụng/package name được quản lý. Tài liệu: [Google Play Developer API – reviews.list](https://developers.google.com/android-publisher/api-ref/rest/v3/reviews/list) và [Review resource](https://developers.google.com/android-publisher/api-ref/rest/v3/reviews).
3. **Sàn thương mại điện tử:** Dùng bộ dữ liệu được cấp phép, API chính thức hoặc dữ liệu do chủ sở hữu cung cấp. Tránh mặc định rằng có thể crawl hàng loạt mọi trang review; phải kiểm tra điều khoản sử dụng và quyền khai thác dữ liệu.

Rating 1–5 có thể dùng làm **weak label** ban đầu:

| Rating gốc | Nhãn mục tiêu |
|---:|---:|
| 1 sao | 1 – Rất tiêu cực |
| 2 sao | 2 – Tiêu cực |
| 3 sao | 3 – Trung tính |
| 4 sao | 4 – Tích cực |
| 5 sao | 5 – Rất tích cực |

Tuy nhiên, đây chỉ là ánh xạ khởi tạo. Ví dụ, người dùng có thể cho 5 sao nhưng viết nội dung phàn nàn, hoặc cho 3 sao nhưng dùng lời khen. Cần lấy mẫu kiểm tra và gán nhãn lại các trường hợp mâu thuẫn.

#### B. Bình luận mạng xã hội không có rating

- **YouTube:** Có thể lấy comment thread bằng [YouTube Data API – commentThreads.list](https://developers.google.com/youtube/v3/docs/commentThreads/list). API hỗ trợ truy xuất comment của video và phân trang. Comment không có rating 1–5, vì vậy cần gán nhãn thủ công hoặc quy trình annotation có kiểm duyệt.
- **Facebook/TikTok và nền tảng khác:** Chỉ sử dụng API/chương trình truy cập chính thức, dữ liệu được cấp phép hoặc dữ liệu mà dự án có quyền khai thác. Không dùng scraping trái điều khoản hoặc thu thập dữ liệu cá nhân không cần thiết.
- **Kênh hỗ trợ khách hàng / khảo sát:** Có thể xuất phản hồi đã được đồng ý cho mục đích nghiên cứu, nhưng cần loại thông tin định danh như tên, số điện thoại, email, mã đơn hàng.

**Nếu bài toán là đánh giá chất lượng sản phẩm/dịch vụ:** Ưu tiên review có rating.  
**Nếu bài toán là phân tích cảm xúc bình luận mạng xã hội nói chung:** Ưu tiên bình luận mạng xã hội và tổ chức gán nhãn thủ công; không nên dùng rating sản phẩm làm đại diện cho toàn bộ miền dữ liệu.

### 2.3. Quy mô dữ liệu mục tiêu

Mục tiêu khởi đầu thực tế:

- Prototype: khoảng 500–1.000 mẫu mỗi lớp để kiểm tra toàn bộ pipeline.
- Baseline có ý nghĩa hơn: khoảng 2.000–3.000 mẫu mỗi lớp.
- Mục tiêu tốt hơn: 3.000–6.000 mẫu mỗi lớp hoặc nhiều hơn, nếu dữ liệu được gán nhãn tốt và đa dạng.

Tổng số lượng không phải yếu tố duy nhất. 5.000 mẫu trùng lặp hoặc gán nhãn nhiễu có thể kém hữu ích hơn tập nhỏ nhưng chất lượng cao. Cần thống kê phân phối thực tế trước khi quyết định thu thập thêm lớp nào.

---

## 3. Quy chuẩn gán nhãn 1–5

### 3.1. Quy tắc chung

Gán nhãn theo **thái độ/tổng thể trải nghiệm được thể hiện trong bình luận**, không chỉ dựa vào một từ khóa đơn lẻ.

| Label | Dấu hiệu điển hình | Ví dụ minh họa |
|---:|---|---|
| 1 | Thất bại nghiêm trọng, không thể sử dụng, hỏng hoàn toàn, cực kỳ thất vọng. | “Mở lên là bị lỗi liên tục, không thể sử dụng được.” |
| 2 | Có vấn đề đáng kể, chậm, sai, chất lượng thấp, chưa hài lòng. | “Ứng dụng khá chậm, thao tác nào cũng phải đợi.” |
| 3 | Trung lập, mô tả sự việc, bình thường hoặc vừa đủ dùng. | “Dùng được, tính năng ở mức cơ bản.” |
| 4 | Tốt, hài lòng, hoạt động đúng mong đợi. | “Chạy ổn định, thao tác dễ, đáp ứng nhu cầu.” |
| 5 | Khen ngợi rất mạnh, vượt kỳ vọng, cực kỳ hài lòng. | “Tuyệt vời, nhanh hơn hẳn các ứng dụng mình từng dùng.” |

### 3.2. Trường hợp khó

| Trường hợp | Cách xử lý |
|---|---|
| Có cả khen và chê | Đánh giá thái độ tổng thể. Nếu hai chiều cân bằng hoặc không thể kết luận, cân nhắc nhãn 3 và đánh dấu `ambiguous=true`. |
| Có phủ định: “không tốt”, “chẳng nhanh” | Phải giữ từ phủ định; không gán nhãn theo từ “tốt/nhanh” đứng riêng. |
| Mỉa mai: “Tốt quá, lỗi từ sáng đến giờ” | Dựa vào nghĩa ngữ cảnh thực tế; trường hợp không chắc chắn cần người gán nhãn thứ hai. |
| Chỉ có emoji | Chỉ gán nhãn nếu guideline xác định rõ ý nghĩa; nếu không thì đưa vào nhóm cần duyệt hoặc loại khỏi tập huấn luyện chính. |
| Bình luận không liên quan, spam, quảng cáo | Gắn trạng thái loại (`exclude`), không ép thành nhãn 3. |
| Rating và nội dung mâu thuẫn | Đánh dấu `rating_text_conflict`; đưa vào hàng đợi kiểm tra thủ công. |

### 3.3. Quy trình annotation

1. Viết guideline với định nghĩa và ví dụ rõ ràng cho từng lớp.
2. Cho ít nhất 2 người gán nhãn độc lập một phần dữ liệu đại diện.
3. Tính weighted Cohen's Kappa hoặc Krippendorff's alpha (ordinal) để đánh giá mức nhất quán.
4. Xem lại các cặp nhầm lẫn thường gặp, ví dụ 2↔3, 3↔4 và 1↔2.
5. Người thứ ba hoặc người có trách nhiệm dữ liệu adjudicate các trường hợp bất đồng.
6. Lưu cả nhãn ban đầu, nhãn cuối cùng, người gán và lý do bất đồng.

Không nên chỉ sử dụng LLM để tự động tạo toàn bộ nhãn rồi coi đó là ground truth. Có thể dùng LLM để đề xuất nhãn sơ bộ hoặc phát hiện mẫu bất thường; con người cần kiểm tra một tập đủ lớn và toàn bộ mẫu có độ tin cậy thấp.

---

## 4. Thiết kế dữ liệu và cấu trúc thư mục

### 4.1. Schema dữ liệu

File chính nên là CSV hoặc Parquet. Tối thiểu cần có:

| Cột | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `comment_id` | string | Có | ID duy nhất, không chứa thông tin định danh cá nhân. |
| `text` | string | Có | Nội dung bình luận gốc. |
| `label` | int | Có với train/validation/test | Giá trị 1–5. |
| `source` | string | Khuyến nghị | YouTube, Google Play, Tiki, internal... |
| `source_item_id` | string | Khuyến nghị | ID video/sản phẩm/ứng dụng, phục vụ group split. |
| `created_at` | datetime | Khuyến nghị | Ngày đăng, phục vụ temporal split. |
| `rating_raw` | int/string | Không | Rating gốc nếu có. Không đưa thẳng vào model input. |
| `annotator_confidence` | float | Khuyến nghị | Mức tự tin của người gán nhãn. |
| `review_status` | string | Khuyến nghị | `approved`, `needs_review`, `exclude`. |

**Không đưa các cột `label`, `rating_raw`, `annotator_confidence`, `source_item_id` hoặc metadata có thể tiết lộ nhãn vào input của mô hình.** Chỉ đưa nội dung bình luận, trừ khi thiết kế một mô hình đa phương thức/đa đặc trưng có chủ đích và có kiểm định leakage.

### 4.2. Cấu trúc thư mục đề xuất

```text
sentiment_phobert/
├── data/
│   ├── raw/                     # Dữ liệu tải về nguyên bản
│   ├── interim/                 # Sau chuẩn hóa cơ bản, trước split
│   ├── processed/
│   │   ├── train.csv
│   │   ├── validation.csv
│   │   └── test.csv
│   └── annotation/              # Guideline, nhãn bất đồng, audit
├── artifacts/
│   ├── best_model/
│   ├── last_checkpoint/
│   ├── tokenizer/
│   ├── segmenter/               # Ghi lại phiên bản/model VnCoreNLP
│   └── label_mapping.json
├── outputs/
│   ├── metrics.json
│   ├── classification_report.csv
│   ├── confusion_matrix.png
│   ├── error_analysis.csv
│   └── predictions.csv
├── configs/
│   └── config.yaml
└── notebooks/
    └── PhoBERT_5Class_Sentiment.ipynb
```

---

## 5. EDA trước khi huấn luyện

Chạy EDA trên dữ liệu thô trước khi split và trước khi xử lý quá mạnh.

### 5.1. Kiểm tra chất lượng dữ liệu

- Số lượng dòng, số lượng `comment_id` duy nhất.
- Giá trị null, chuỗi rỗng, bình luận chỉ chứa URL/emoji/ký tự đặc biệt.
- Số bản ghi trùng lặp hoàn toàn.
- Tỷ lệ trùng lặp sau khi chuẩn hóa khoảng trắng và Unicode.
- Tỷ lệ spam, ngôn ngữ khác tiếng Việt, bình luận quá ngắn/quá dài.
- Phân phối `source`, thời gian, rating gốc và trạng thái annotation.

### 5.2. Phân tích nhãn

Vẽ:

- Số lượng và tỷ lệ phần trăm từng lớp 1–5.
- Phân phối nhãn theo nguồn dữ liệu.
- Phân phối nhãn theo thời gian.
- Confusion/mismatch giữa `rating_raw` và nhãn người gán.

Nếu nhãn 1 hoặc 5 có quá ít mẫu, cần thu thập thêm hoặc kiểm tra xem định nghĩa 1 và 2, 4 và 5 có đang quá khó phân biệt hay không.

### 5.3. Phân tích độ dài

Thống kê theo:

- Số ký tự.
- Số từ trước word segmentation.
- Số token sau PhoBERT tokenizer.
- Phần trăm mẫu vượt quá 128, 160, 192 và 256 tokens.

PhoBERT có giới hạn đầu vào tối đa 256 tokens theo model card. Chọn `MAX_LENGTH` dựa trên phân phối thực tế; không đặt 256 một cách máy móc vì sequence dài làm tăng VRAM và thời gian huấn luyện.

### 5.4. EDA theo lỗi ngôn ngữ

Thống kê các dạng:

- Teencode: `ko`, `k`, `hok`, `đc`, `mik`, `sp`...
- Không dấu, sai dấu, sai chính tả.
- Kéo dài ký tự: `đẹpppp`, `tệeeee`.
- Emoji/emoticon.
- Viết hoa toàn bộ, dấu chấm than, dấu hỏi liên tiếp.
- Câu phủ định và mỉa mai.

Giữ lại các ví dụ thực tế để kiểm thử sau preprocessing.

---

## 6. Tiền xử lý dữ liệu

### 6.1. Nguyên tắc

Mục tiêu là chuẩn hóa vừa đủ, không xóa tín hiệu cảm xúc.

**Nên làm:**

- Chuẩn hóa Unicode về NFC.
- Decode HTML entity, bỏ HTML tag không mang thông tin.
- Chuẩn hóa khoảng trắng, ký tự xuống dòng.
- Chuẩn hóa URL thành token chung như `URL` nếu URL không quan trọng với tác vụ.
- Ẩn danh username/mention thành token `USER` khi cần.
- Chuẩn hóa cách viết lặp quá mức nhưng giữ dấu hiệu cường điệu ở mức hợp lý.
- Word segmentation bằng RDRSegmenter/VnCoreNLP trước tokenizer PhoBERT.

**Không nên làm:**

- Không bỏ toàn bộ dấu tiếng Việt.
- Không xóa từ phủ định: `không`, `chẳng`, `chưa`, `khó`...
- Không xóa toàn bộ dấu câu, emoji hoặc hashtag khi chưa có ablation chứng minh chúng gây hại.
- Không loại stopwords theo danh sách chung.
- Không stemming/lemmatization tùy tiện.
- Không dịch bình luận sang ngôn ngữ khác.

### 6.2. Word segmentation bắt buộc cho PhoBERT

PhoBERT yêu cầu văn bản **đã word-segment** trước khi đưa vào tokenizer. Ví dụ:

```text
Raw:       Chất lượng sản phẩm rất tốt và giao hàng nhanh.
Segmented: Chất_lượng sản_phẩm rất tốt và giao_hàng nhanh .
```

Khuyến nghị sử dụng RDRSegmenter thông qua VnCoreNLP/`py_vncorenlp`, đồng nhất với cách xử lý dữ liệu pretraining của PhoBERT.

Ví dụ khởi tạo:

```python
import py_vncorenlp

py_vncorenlp.download_model(save_dir="./artifacts/vncorenlp")
segmenter = py_vncorenlp.VnCoreNLP(
    annotators=["wseg"],
    save_dir="./artifacts/vncorenlp"
)

def word_segment(text: str) -> str:
    sentences = segmenter.word_segment(text)
    return " ".join(sentences)
```

Lưu ý:

- Chỉ khởi tạo segmenter một lần, không khởi tạo lại cho từng dòng.
- Ghi lại phiên bản VnCoreNLP/RDRSegmenter và lưu model segmenter cùng pipeline.
- Dùng đúng một quy trình segmentation trong train, validation, test và inference.
- Nếu nguồn có nhiều teencode, cần kiểm tra thủ công đầu ra segmentation. Có thể xây dựng từ điển teencode riêng nhưng chỉ áp dụng các quy tắc đã được kiểm chứng.

### 6.3. Hàm chuẩn hóa cơ bản tham khảo

```python
import re
import html
import unicodedata

URL_RE = re.compile(r"https?://\S+|www\.\S+", flags=re.IGNORECASE)
MENTION_RE = re.compile(r"(?<!\w)@[\w.]+")
HTML_RE = re.compile(r"<[^>]+>")


def normalize_comment(text: str) -> str:
    if text is None:
        return ""

    text = str(text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = HTML_RE.sub(" ", text)
    text = URL_RE.sub(" URL ", text)
    text = MENTION_RE.sub(" USER ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
```

Đây chỉ là bước chuẩn hóa cơ bản. Không áp dụng một bộ rule teencode quá mạnh trước khi đo hiệu quả. Nên tạo hai nhánh ablation:

- `A`: chuẩn hóa nhẹ + segmentation.
- `B`: chuẩn hóa nhẹ + từ điển teencode có kiểm duyệt + segmentation.

So sánh Macro-F1/QWK trên validation để chọn.

### 6.4. Tokenization

```python
from transformers import AutoTokenizer

MODEL_NAME = "vinai/phobert-base-v2"
tokenizer = AutoTokenizer.from_pretrained(
    MODEL_NAME,
    use_fast=False
)

encoded = tokenizer(
    segmented_text,
    truncation=True,
    max_length=160,
    padding=False
)
```

Dùng dynamic padding theo batch trong DataLoader để tránh pad tất cả mẫu lên 256 tokens.

---

## 7. Chia train / validation / test

### 7.1. Tỷ lệ mặc định

Nếu dataset đủ lớn, có thể bắt đầu với:

- Train: 80%.
- Validation: 10%.
- Test: 10%.

Dùng stratification theo nhãn để giữ tỷ lệ lớp tương đối ổn định.

### 7.2. Tránh data leakage

Đây là bước rất quan trọng với review và bình luận:

1. Khử trùng lặp trước khi split.
2. Nếu có `product_id`, `video_id`, `app_id`, `user_id` hoặc `thread_id`, cân nhắc chia theo nhóm (`GroupShuffleSplit`/group-aware split), để các mẫu từ cùng nhóm không xuất hiện cả train và test.
3. Nếu mục tiêu dự đoán bình luận trong tương lai, ưu tiên **time-based split**: dữ liệu cũ train, giai đoạn sau validation, giai đoạn mới nhất test.
4. Nếu cần kiểm tra khả năng tổng quát hóa giữa nền tảng, giữ riêng một nguồn làm external test, ví dụ train trên review sản phẩm và test trên review ứng dụng.
5. Không sử dụng test set để chọn epoch, threshold, hyperparameter hoặc preprocessing.

`StratifiedShuffleSplit` thông thường không ngăn leakage theo sản phẩm/người dùng/video. Cần chọn cách split theo đúng tình huống sử dụng thực tế.

---

## 8. Kiến trúc mô hình

### 8.1. Baseline bắt buộc: PhoBERT + Softmax 5 lớp

Checkpoint:

```python
MODEL_NAME = "vinai/phobert-base-v2"
```

**Lưu ý license:** Model card trên Hugging Face hiện hiển thị metadata license `AGPL-3.0`. Trước khi phân phối checkpoint, tích hợp vào dịch vụ hoặc sử dụng thương mại, cần đọc license và xác nhận nghĩa vụ áp dụng cho đúng hình thức sử dụng. Không mặc định rằng quyền sử dụng dataset cũng bao gồm quyền sử dụng pretrained model.

Khởi tạo:

```python
from transformers import AutoModelForSequenceClassification

id2label = {
    0: "1 - Rất tiêu cực",
    1: "2 - Tiêu cực",
    2: "3 - Trung tính",
    3: "4 - Tích cực",
    4: "5 - Rất tích cực",
}
label2id = {v: k for k, v in id2label.items()}

model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=5,
    id2label=id2label,
    label2id=label2id
)
```

Trong dữ liệu CSV, label gốc là 1–5; khi đưa vào Cross Entropy, đổi thành class index 0–4:

```python
df["label_id"] = df["label"].astype(int) - 1
```

Kiến trúc:

```text
Segmented comment
      ↓
PhoBERT tokenizer
      ↓
PhoBERT-base-v2 encoder
      ↓
Sequence classification head
      ↓
5 logits → Softmax
      ↓
P(label=1), ..., P(label=5)
```

Loss ban đầu: `CrossEntropyLoss`.

**Vì sao dùng Softmax trước?** Đây là baseline đơn giản, dễ debug, tận dụng trực tiếp Hugging Face Trainer và cho xác suất 5 lớp. Cần có baseline này trước khi thử các loss ordinal phức tạp hơn.

### 8.2. Thí nghiệm nâng cao: Ordinal Regression / CORAL

Nếu confusion matrix cho thấy mô hình thường nhầm các lớp gần nhau và thường xuyên nhầm các lớp xa nhau, thử ordinal classification.

Với nhãn `y ∈ {1,2,3,4,5}`, tạo 4 mục tiêu dạng cumulative:

| Nhãn gốc | Target ordinal |
|---:|---|
| 1 | `[0, 0, 0, 0]` |
| 2 | `[1, 0, 0, 0]` |
| 3 | `[1, 1, 0, 0]` |
| 4 | `[1, 1, 1, 0]` |
| 5 | `[1, 1, 1, 1]` |

Các đầu ra tương ứng với câu hỏi: nhãn có lớn hơn 1, 2, 3, 4 hay không. Có thể nghiên cứu CORAL/cumulative-link head và loss ordinal.

Thứ tự thực nghiệm:

1. Softmax + Cross Entropy làm baseline.
2. Softmax + Cross Entropy + ordinal penalty (thử nghiệm phụ).
3. CORAL/ordinal head.
4. So sánh Accuracy, Macro-F1, MAE, QWK trên cùng split.

Không mặc định CORAL tốt hơn Softmax; phải đo trên validation/test độc lập. Nếu dùng CORAL, đảm bảo cách chuyển các output ngưỡng thành nhãn dự đoán nhất quán và kiểm tra tính đơn điệu của xác suất ngưỡng.

---

## 9. Cấu hình huấn luyện ban đầu

Cấu hình gợi ý cho PhoBERT-base-v2 trên Colab T4 16GB:

| Hyperparameter | Giá trị ban đầu | Phạm vi thử |
|---|---:|---:|
| Model | `vinai/phobert-base-v2` | Cố định ở vòng đầu |
| Max length | 160 | 128 / 160 / 192 / 256 |
| Per-device batch size | 16 | 8 / 16 |
| Gradient accumulation | 1–2 | 1 / 2 / 4 |
| Effective batch | 16–32 | Tùy VRAM |
| Learning rate | `2e-5` | `1e-5`, `2e-5`, `3e-5` |
| Epochs | 4 | 3–6 |
| Optimizer | AdamW | AdamW |
| Weight decay | 0.01 | 0.0 / 0.01 / 0.05 |
| Warmup ratio | 0.1 | 0.06–0.1 |
| Scheduler | Linear | Linear / Cosine |
| Max grad norm | 1.0 | 1.0 |
| Precision | FP16 | FP16/BF16 nếu GPU hỗ trợ |
| Early stopping | Patience 2 | 2–3 |
| Seed | 42 | Chạy thêm 17, 202 khi chốt |

Các giá trị trên là điểm bắt đầu, không phải cấu hình tối ưu cho mọi dataset. Nếu CUDA out-of-memory: giảm batch size trước, sau đó tăng gradient accumulation; giảm max length nếu EDA cho thấy ít mẫu cần sequence dài.

### 9.1. Chọn optimizer và learning rate

- AdamW là lựa chọn baseline phù hợp cho fine-tuning Transformer.
- Thử `1e-5`, `2e-5`, `3e-5` trên validation.
- Có thể dùng learning rate thấp hơn cho encoder và cao hơn cho classification head (discriminative learning rates), ví dụ encoder `1e-5` và head `5e-5`.
- Không nên thử quá nhiều cấu hình cùng lúc khi dữ liệu validation nhỏ.

### 9.2. Mixed precision và bộ nhớ

- Dùng FP16 trên T4 để giảm VRAM và tăng tốc.
- Dùng dynamic padding.
- Tránh giữ toàn bộ logits/hidden states của mọi batch trên GPU.
- Với batch nhỏ, tăng gradient accumulation để đạt effective batch lớn hơn.
- Dùng `gradient_checkpointing` nếu thiếu VRAM, nhưng nó làm training chậm hơn.

### 9.3. Chọn best checkpoint

Theo dõi ít nhất:

- `eval_macro_f1` – metric chọn checkpoint chính.
- `eval_qwk` – metric ordinal để xem xét khi hai checkpoint có Macro-F1 gần nhau.
- `eval_loss` – theo dõi overfitting.

Khuyến nghị chọn checkpoint có Macro-F1 validation cao nhất; nếu chênh lệch rất nhỏ, dùng QWK và phân tích confusion matrix để quyết định. Không chọn checkpoint chỉ vì train accuracy cao.

---

## 10. Chiến lược xử lý mất cân bằng nhãn

### 10.1. Đo trước khi xử lý

Không dùng weighted loss/sampler theo thói quen. Trước tiên cần xem số mẫu từng lớp và Macro-F1 từng lớp trên baseline.

### 10.2. Thứ tự thử nghiệm

1. **Baseline:** Cross Entropy không trọng số.
2. **Class-weighted Cross Entropy:** Dùng khi lớp thiểu số có Recall/F1 thấp rõ rệt.
3. **WeightedRandomSampler:** Thử nếu batch thường xuyên không có lớp hiếm.
4. **Focal Loss:** Chỉ thử khi mô hình vẫn gặp khó với mẫu khó sau các bước trên.

Không nên bật class weights và oversampling mạnh cùng lúc ngay từ đầu vì có thể làm mô hình quá thiên về lớp hiếm.

Ví dụ tính trọng số căn bậc hai nghịch đảo tần suất để tránh trọng số quá cực đoan:

```python
import numpy as np
from sklearn.utils.class_weight import compute_class_weight

classes = np.array([0, 1, 2, 3, 4])
y_train = train_df["label_id"].to_numpy()

weights = compute_class_weight(
    class_weight="balanced",
    classes=classes,
    y=y_train
)

weights = np.sqrt(weights)
weights = weights / weights.mean()
```

Sau đó chuyển sang `torch.tensor(weights, dtype=torch.float)` và đưa vào `CrossEntropyLoss(weight=...)` trên device phù hợp.

Lưu ý: nếu phân phối nhãn tự nhiên là điều bạn muốn mô hình phản ánh (ví dụ môi trường thật có rất nhiều đánh giá 4 sao), không nhất thiết phải ép train set cân bằng tuyệt đối. Cần quyết định rõ mục tiêu là Macro-F1 hay phân phối dự đoán đúng thực tế.

---

## 11. Pipeline huấn luyện đầy đủ

### Phase 0 – Configuration

- Định nghĩa seed, đường dẫn dữ liệu, checkpoint và output.
- Định nghĩa model name, max length, batch size, learning rate, epochs.
- Định nghĩa mapping label gốc 1–5 sang index 0–4.
- Ghi phiên bản thư viện và cấu hình thí nghiệm.

### Phase 1 – Load và validate dữ liệu

- Đọc raw CSV/Parquet.
- Kiểm tra schema, kiểu dữ liệu, nhãn nằm trong `{1,2,3,4,5}`.
- Bỏ hoặc cách ly hàng thiếu text/label.
- Loại mẫu có trạng thái `exclude`.
- Kiểm tra ID trùng và nội dung trùng.

### Phase 2 – EDA trước train

- Phân phối nhãn.
- Phân phối nguồn dữ liệu.
- Độ dài văn bản và độ dài sau tokenizer.
- Teencode, emoji, URL, câu ngắn/dài.
- Kiểm tra mismatch giữa rating và text.

### Phase 3 – Split

- Deduplicate trước split.
- Tạo train/validation/test theo stratified group split hoặc time split tùy tình huống.
- Lưu split manifest (ID của mỗi mẫu trong từng tập) để tái lập.

### Phase 4 – Preprocessing

- Chuẩn hóa nhẹ.
- Word segmentation bằng RDRSegmenter/VnCoreNLP.
- Tokenize bằng PhoBERT tokenizer.
- Chọn `MAX_LENGTH` dựa trên EDA.
- Lưu cache dữ liệu đã segmentation/tokenization nếu pipeline xử lý tốn thời gian.

### Phase 5 – Dataset và DataLoader

Mỗi item trả về:

```python
{
    "input_ids": ...,
    "attention_mask": ...,
    "labels": label_id  # 0..4
}
```

Sử dụng dynamic padding theo batch. Không đưa `comment_id`, `source`, `rating_raw` hoặc thông tin nhãn vào model input.

### Phase 6 – Model

- Load `AutoModelForSequenceClassification` từ `vinai/phobert-base-v2`.
- `num_labels=5`.
- Loss baseline: Cross Entropy.
- Có thể thêm dropout/ordinal head trong các experiment riêng.

### Phase 7 – Training loop

Mỗi epoch:

1. `model.train()`.
2. Forward pass.
3. Tính loss.
4. Backpropagation; áp dụng gradient accumulation nếu cần.
5. Gradient clipping.
6. Optimizer step và scheduler step đúng chu kỳ.
7. `model.eval()` và đánh giá validation.
8. Ghi log loss, Accuracy, Macro-F1, per-class F1, MAE, QWK.
9. Lưu best checkpoint theo tiêu chí đã chọn.
10. Dừng sớm nếu validation không cải thiện sau `patience` epoch.

### Phase 8 – Chốt mô hình

- Load best checkpoint, không dùng last checkpoint một cách mặc định.
- Chạy test đúng một lần sau khi đã chốt lựa chọn.
- Tạo classification report và confusion matrix.
- Kiểm tra lỗi theo từng nguồn, nhãn và độ dài câu.
- Chạy thêm seed để kiểm tra độ ổn định nếu tài nguyên cho phép.

### Phase 9 – Inference và export

- Nhận bình luận mới.
- Dùng đúng normalization + segmentation + tokenizer.
- Chạy model và softmax.
- Quy đổi class index 0–4 về label 1–5.
- Xuất CSV/JSON cùng xác suất từng lớp.

---

## 12. Metrics và phân tích kết quả

| Metric | Ý nghĩa | Vai trò |
|---|---|---|
| Accuracy | Tỷ lệ dự đoán chính xác toàn bộ | Thông tin tổng quan |
| Macro Precision | Precision trung bình đều trên 5 lớp | Tránh lớp lớn chi phối |
| Macro Recall | Recall trung bình đều trên 5 lớp | Kiểm tra khả năng tìm đủ từng lớp |
| Macro-F1 | Trung bình F1 đều trên 5 lớp | **Metric chính đề xuất** |
| Per-class F1 | F1 riêng cho nhãn 1–5 | Tìm lớp yếu |
| MAE | Trung bình `abs(y_true - y_pred)` | Đo sai lệch số bậc |
| QWK | Cohen's Kappa với trọng số bình phương | Đánh giá dự đoán ordinal |

Ví dụ tính metrics:

```python
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    precision_recall_fscore_support,
    mean_absolute_error,
    cohen_kappa_score,
    classification_report,
    confusion_matrix,
)

# y_true và y_pred phải cùng hệ nhãn: 1..5
acc = accuracy_score(y_true, y_pred)
precision, recall, f1, support = precision_recall_fscore_support(
    y_true,
    y_pred,
    labels=[1, 2, 3, 4, 5],
    average="macro",
    zero_division=0,
)
mae = mean_absolute_error(y_true, y_pred)
qwk = cohen_kappa_score(y_true, y_pred, labels=[1, 2, 3, 4, 5], weights="quadratic")
cm = confusion_matrix(y_true, y_pred, labels=[1, 2, 3, 4, 5])

print({
    "accuracy": acc,
    "macro_precision": precision,
    "macro_recall": recall,
    "macro_f1": f1,
    "mae": mae,
    "qwk": qwk,
})
print(classification_report(y_true, y_pred, labels=[1,2,3,4,5], zero_division=0))
```

**Cách đọc confusion matrix:**

- Nhầm 1↔2 hoặc 4↔5: mô hình nhận ra chiều cảm xúc nhưng chưa tách tốt cường độ.
- Nhầm 1↔5 hoặc 2↔4: lỗi nghiêm trọng, cần kiểm tra preprocessing, annotation hoặc ngữ cảnh.
- Dự đoán dồn về lớp 3: có thể dữ liệu mất cân bằng, nhãn quá nhiễu hoặc model chưa học được cường độ.

---

## 13. Chiến lược tối ưu theo vòng lặp

Không tối ưu mọi thứ cùng lúc. Dùng quy trình ablation để biết thay đổi nào thật sự có ích.

| Experiment | Thay đổi | Mục đích |
|---|---|---|
| EXP-00 | PhoBERT + CE, max_len 160, LR 2e-5 | Baseline |
| EXP-01 | Max length 128 / 192 / 256 | Chọn độ dài phù hợp |
| EXP-02 | LR 1e-5 / 2e-5 / 3e-5 | Tối ưu learning rate |
| EXP-03 | Weighted CE | Xử lý class imbalance |
| EXP-04 | Teencode normalization bật/tắt | Xem ảnh hưởng văn bản mạng xã hội |
| EXP-05 | Giữ/bỏ emoji, hashtag, punctuation có kiểm soát | Xác định tín hiệu hữu ích |
| EXP-06 | Encoder LR 1e-5, head LR 5e-5 | Discriminative learning rates |
| EXP-07 | CORAL/ordinal loss | Tận dụng thứ tự nhãn |
| EXP-08 | 3 random seeds | Đo độ ổn định |

### 13.1. Error analysis

Xuất các mẫu dự đoán sai với các cột:

```text
comment_id, text, y_true, y_pred, confidence,
prob_1, prob_2, prob_3, prob_4, prob_5, source
```

Chia lỗi thành:

- Nhầm giữa hai mức liền kề.
- Nhầm giữa hai mức cách xa.
- Câu phủ định.
- Mỉa mai.
- Câu có cả khen và chê.
- Teencode hoặc sai chính tả.
- Thiếu ngữ cảnh.
- Nhãn gốc không nhất quán với nội dung.

Sau khi đọc lỗi, ưu tiên sửa **data/annotation** trước khi tăng độ phức tạp model. Với sentiment, dữ liệu nhãn nhiễu thường là nút thắt lớn.

### 13.2. Có nên ensemble không?

Chỉ thử ensemble sau khi đã có ít nhất hai model độc lập với validation tốt và lỗi bổ sung cho nhau. Có thể average probability của PhoBERT checkpoint khác seed hoặc các model cùng backbone với preprocessing khác nhau.

Không ensemble chỉ vì có nhiều mô hình; nếu mô hình cùng sai trên cùng ví dụ, ensemble ít giá trị. Đo latency và dung lượng trước khi triển khai.

---

## 14. Output / artifact cuối cùng

### 14.1. Model artifact

```text
artifacts/best_model/
├── config.json
├── model.safetensors          # hoặc pytorch_model.bin tùy định dạng save
├── tokenizer_config.json
├── special_tokens_map.json
├── vocab.txt / tokenizer files
└── training_config.json
```

Ngoài model, phải lưu:

- Label mapping 0–4 ↔ nhãn 1–5.
- Version và cấu hình VnCoreNLP/RDRSegmenter.
- Hàm normalization/teencode version.
- `MAX_LENGTH`.
- Metrics validation/test.
- Random seed, package versions và split manifest.

### 14.2. File dự đoán

`predictions.csv` nên có:

| Cột | Nội dung |
|---|---|
| `comment_id` | ID mẫu |
| `text` | Bình luận gốc |
| `pred_label` | Nhãn dự đoán 1–5 |
| `pred_label_name` | Tên nhãn |
| `prob_1` ... `prob_5` | Xác suất từng lớp |
| `confidence` | Xác suất lớn nhất |
| `expected_score` | Tổng `prob_i * i`, i=1..5 |

Ví dụ output:

```csv
comment_id,text,pred_label,pred_label_name,prob_1,prob_2,prob_3,prob_4,prob_5,confidence,expected_score
001,"Ứng dụng chạy nhanh và ổn định",4,"4 - Tích cực",0.01,0.03,0.12,0.70,0.14,0.70,3.93
```

`expected_score` hữu ích cho phân tích mức hài lòng tổng thể, nhưng nhãn phân lớp chính vẫn nên là `argmax(probabilities)` ở baseline Softmax. Nếu sử dụng ordinal head, cần định nghĩa rõ quy tắc giải mã nhãn.

### 14.3. Hàm inference tham khảo

```python
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModelForSequenceClassification

MODEL_DIR = "./artifacts/best_model"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Load tokenizer/model đã fine-tune
# Segmenter và normalization phải được load từ đúng phiên bản lúc train.
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, use_fast=False)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR)
model.to(DEVICE)
model.eval()


def predict_one(raw_text, segmenter, max_length=160):
    text = normalize_comment(raw_text)
    segmented_text = " ".join(segmenter.word_segment(text))

    inputs = tokenizer(
        segmented_text,
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
    )
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}

    with torch.inference_mode():
        logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0].cpu().numpy()

    pred_id = int(np.argmax(probs))  # 0..4
    pred_label = pred_id + 1         # 1..5
    expected_score = float(sum((i + 1) * p for i, p in enumerate(probs)))

    label_names = {
        1: "Rất tiêu cực",
        2: "Tiêu cực",
        3: "Trung tính",
        4: "Tích cực",
        5: "Rất tích cực",
    }

    return {
        "pred_label": pred_label,
        "pred_label_name": label_names[pred_label],
        "probabilities": {f"prob_{i+1}": float(p) for i, p in enumerate(probs)},
        "confidence": float(np.max(probs)),
        "expected_score": expected_score,
    }
```

Lưu ý: ví dụ trên minh họa inference; trong triển khai thực tế cần xử lý batch, lỗi input rỗng và load segmenter một lần. `normalize_comment` phải chính là hàm đã dùng trong training.

---

## 15. Kiểm tra chất lượng trước triển khai

Checklist trước khi đưa model vào sử dụng:

- [ ] Train/validation/test có cùng định nghĩa nhãn 1–5.
- [ ] Không có duplicate hoặc near-duplicate xuyên các split.
- [ ] Không có leakage từ `rating_raw`, ID, nguồn hoặc metadata.
- [ ] Có audit mẫu nhãn 1 và 5 vì hai lớp này thường khó thu thập đủ.
- [ ] Có báo cáo Macro-F1 và F1 riêng của từng lớp.
- [ ] Có MAE, QWK và confusion matrix.
- [ ] Đã kiểm tra dự đoán trên các ví dụ phủ định, mỉa mai, teencode, emoji và nội dung hỗn hợp.
- [ ] Train và inference sử dụng cùng normalization, word segmentation và tokenizer.
- [ ] Đã lưu best checkpoint, tokenizer, label mapping và segmenter version.
- [ ] Đã kiểm tra license của pretrained model và từng dataset trước khi tái phân phối/triển khai thương mại.
- [ ] Nếu xác suất được sử dụng để ra quyết định, đã kiểm tra calibration và định nghĩa ngưỡng chuyển sang người duyệt.

### Quy tắc xử lý độ tin cậy

Có thể cấu hình hệ thống ba vùng, sau khi hiệu chỉnh trên validation:

- Confidence cao: tự động trả nhãn.
- Confidence trung bình: trả nhãn kèm cờ cần kiểm tra.
- Confidence thấp: chuyển người duyệt hoặc trả trạng thái `uncertain`.

Không đặt ngưỡng confidence cố định như 0.8 mà không kiểm chứng calibration; softmax confidence không tự động đồng nghĩa với xác suất đúng.

---

## 16. Lộ trình thực nghiệm khuyến nghị

### Giai đoạn 1 – Data baseline

1. Thu thập một tập review/bình luận có rating 1–5 hoặc gán nhãn thủ công theo guideline.
2. Audit ngẫu nhiên mỗi lớp.
3. Đạt tối thiểu khoảng 500–1.000 mẫu/lớp để chạy thử; tiếp tục mở rộng khi có kết quả lỗi.
4. Tạo split chống leakage.

### Giai đoạn 2 – Baseline PhoBERT

1. Dùng `vinai/phobert-base-v2`.
2. Normalize nhẹ + RDRSegmenter.
3. Max length 160, batch size 16, LR 2e-5, 4 epochs.
4. Cross Entropy, chọn checkpoint theo validation Macro-F1.
5. Báo cáo Accuracy, Macro-F1, MAE, QWK và confusion matrix.

### Giai đoạn 3 – Data-centric optimization

1. Đọc 200–500 mẫu dự đoán sai hoặc bất đồng nhãn.
2. Sửa guideline và gán nhãn lại các trường hợp mơ hồ.
3. Bổ sung dữ liệu cho lớp yếu, đặc biệt là lớp 1 và 5 nếu thiếu.
4. Thử teencode normalization và các max length khác nhau.

### Giai đoạn 4 – Model-centric optimization

1. Class-weighted Cross Entropy nếu mất cân bằng rõ rệt.
2. Discriminative learning rates.
3. CORAL/ordinal loss.
4. Multi-seed/ensemble nếu cần và có đủ tài nguyên.

### Giai đoạn 5 – Finalization

1. Chọn cấu hình bằng validation.
2. Đánh giá test một lần.
3. Lưu model và pipeline inference đầy đủ.
4. Xuất `predictions.csv`, `metrics.json`, report và confusion matrix.
5. Kiểm tra license trước khi chia sẻ model/dataset.

---

## 17. Nguồn tham khảo và tài liệu chính thức

1. **PhoBERT model card:** https://huggingface.co/vinai/phobert-base-v2  
   Model card hiện hiển thị license metadata `AGPL-3.0`; cần rà soát license trước khi triển khai/phân phối.  
   Thông tin backbone, giới hạn 256 tokens, dữ liệu pretraining và yêu cầu input đã word-segment.
2. **PhoBERT paper:** https://aclanthology.org/2020.findings-emnlp.92/  
   Nguyen & Nguyen, *PhoBERT: Pre-trained language models for Vietnamese*, Findings of EMNLP 2020.
3. **VnCoreNLP GitHub:** https://github.com/vncorenlp/VnCoreNLP  
   Toolkit tiếng Việt, bao gồm word segmentation; có ví dụ dùng RDRSegmenter.
4. **UIT-ViSFD:** https://huggingface.co/datasets/visolex/ViSFD  
   Review smartphone, trường `n_star` và nhãn cảm xúc theo khía cạnh.
5. **AIVIVN 2019 Sentiment:** https://github.com/undertheseanlp/NLP-Vietnamese-progress/blob/master/tasks/sentiment_analysis.md  
   Benchmark review tiếng Việt nhãn Positive/Negative.
6. **UIT-VSFC / VietnameseDatasets:** https://github.com/kietnv/VietnameseDatasets  
   Corpus phản hồi sinh viên với nhãn sentiment/topic; kiểm tra điều kiện truy cập trước khi dùng.
7. **UIT-VSMEC:** https://huggingface.co/datasets/visolex/UIT-VSMEC  
   Corpus cảm xúc trên mạng xã hội tiếng Việt.
8. **VLSP 2016 resources:** https://vlsp.org.vn/resources-vlsp2016  
   Hướng dẫn yêu cầu truy cập corpus VLSP.
9. **Google Play Developer API – reviews.list:** https://developers.google.com/android-publisher/api-ref/rest/v3/reviews/list  
   API lấy review và yêu cầu authorization.
10. **YouTube Data API – commentThreads.list:** https://developers.google.com/youtube/v3/docs/commentThreads/list  
    API lấy comment thread của video.

---

## 18. Kết luận và cấu hình baseline chốt

Cấu hình đề xuất để bắt đầu triển khai:

```yaml
model_name: vinai/phobert-base-v2
num_labels: 5
label_values: [1, 2, 3, 4, 5]
label_to_class_id: {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
text_normalization: light
word_segmenter: VnCoreNLP_RDRSegmenter
max_length: 160
batch_size: 16
gradient_accumulation_steps: 1
learning_rate: 2.0e-5
weight_decay: 0.01
warmup_ratio: 0.1
epochs: 4
optimizer: AdamW
scheduler: linear
loss: CrossEntropyLoss
primary_metric: macro_f1
secondary_metrics: [accuracy, macro_precision, macro_recall, mae, qwk]
early_stopping_patience: 2
seed: 42
save_best_checkpoint: true
save_last_checkpoint: true
```

**Thứ tự ưu tiên tối ưu:**

1. Đúng định nghĩa nhãn và chất lượng dữ liệu.
2. Tránh leakage trong train/validation/test.
3. Đồng nhất preprocessing và word segmentation.
4. Baseline PhoBERT + Cross Entropy.
5. Phân tích lỗi, xử lý mất cân bằng và thử ordinal loss.
6. Tối ưu hyperparameter, multi-seed hoặc ensemble.

> **Thông điệp quan trọng:** Với bài toán 5 mức độ cảm xúc, chất lượng guideline và dữ liệu phân biệt được mức 1–2, 2–3, 3–4, 4–5 thường quan trọng không kém việc chọn backbone. Hãy xem PhoBERT là điểm khởi đầu mạnh, nhưng kết quả cuối cùng phải được xác nhận trên tập test độc lập thuộc đúng miền bình luận cần triển khai.
