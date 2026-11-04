// Nhãn COCO và khối `detection` mặc định, dùng chung cho trang (app.js) và worker (worker.js).
// Cùng nội dung với client/detection.py của client Python.

export const COCO_LABELS = [
  "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
  "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
  "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
  "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
  "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
  "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
  "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
  "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
  "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
  "teddy bear", "hair drier", "toothbrush",
];

// Tên hiển thị tiếng Việt, cùng thứ tự COCO_LABELS. Gửi server vẫn dùng tên tiếng Anh.
export const COCO_LABELS_VI = [
  "người", "xe đạp", "ô tô", "xe máy", "máy bay", "xe buýt", "tàu hoả", "xe tải", "thuyền",
  "đèn giao thông", "trụ cứu hoả", "biển dừng", "đồng hồ đỗ xe", "ghế dài", "chim", "mèo", "chó",
  "ngựa", "cừu", "bò", "voi", "gấu", "ngựa vằn", "hươu cao cổ", "ba lô", "ô (dù)",
  "túi xách", "cà vạt", "vali", "đĩa bay", "ván trượt tuyết", "ván trượt tuyết đơn", "bóng", "diều",
  "gậy bóng chày", "găng bóng chày", "ván trượt", "ván lướt sóng", "vợt tennis", "chai",
  "ly rượu", "cốc", "nĩa", "dao", "thìa", "bát", "chuối", "táo", "bánh mì kẹp", "cam",
  "bông cải xanh", "cà rốt", "xúc xích kẹp", "pizza", "bánh vòng", "bánh ngọt", "ghế", "ghế sofa",
  "chậu cây", "giường", "bàn ăn", "bồn cầu", "TV", "laptop", "chuột", "điều khiển", "bàn phím",
  "điện thoại", "lò vi sóng", "lò nướng", "máy nướng bánh", "bồn rửa", "tủ lạnh", "sách", "đồng hồ",
  "bình hoa", "kéo", "gấu bông", "máy sấy tóc", "bàn chải đánh răng",
];

// SSD (TF Object Detection API) đánh id COCO 1..90, bỏ trống 10 id không thuộc bộ 80 lớp
export const COCO91_TO_80 = (() => {
  const missing = new Set([12, 26, 29, 30, 45, 66, 68, 69, 71, 83]);
  const map = new Array(92).fill(-1);
  let k = 0;
  for (let id = 1; id <= 90; id++) if (!missing.has(id)) map[id] = k++;
  return map;
})();

// Variant cũ không có khối `detection`: đoán theo tiền tố variant_id
const FALLBACK = [
  [/^yolox_(nano|tiny)/, { format: "yolox", input_size: [416, 416], resize: "letterbox", color: "BGR", pad_value: 114 }],
  [/^yolox_/, { format: "yolox", input_size: [640, 640], resize: "letterbox", color: "BGR", pad_value: 114 }],
  [/^ssd_mobilenet/, { format: "ssd_tf", input_size: [300, 300], resize: "stretch", color: "RGB" }],
  [/^fasterrcnn/, { format: "faster_rcnn", resize: "keep_ratio", min_side: 512, max_side: 864, size_divisor: 32, color: "BGR", class_offset: 1 }],
];

// Định dạng đầu ra trình duyệt giải mã được (retinanet chỉ dành cho client Python)
export const WEB_FORMATS = new Set(["yolox", "ssd_tf", "faster_rcnn"]);

export function detectionSpec(variant) {
  if (variant?.detection) return variant.detection;
  const hit = FALLBACK.find(([re]) => re.test(variant?.id || variant?.variant_id || ""));
  return hit ? hit[1] : null;
}

export function webSupported(variant) {
  const runtimes = variant?.runtimes || variant?.supported_runtimes;
  if (runtimes && !runtimes.includes("onnxruntime-web")) return false;
  const spec = detectionSpec(variant);
  return !!spec && WEB_FORMATS.has(spec.format);
}
