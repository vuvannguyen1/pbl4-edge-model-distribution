# Dữ liệu mẫu cho demo

| File | Nguồn | Ghi chú |
|---|---|---|
| `imagenet_synset.txt` | [ONNX Model Zoo – synset.txt](https://raw.githubusercontent.com/onnx/models/main/validated/vision/classification/synset.txt) | 1000 nhãn ImageNet-1k, dòng thứ `i` ứng với đầu ra thứ `i` của model. Được commit vào repo |
| `cat.jpg` | [Wikimedia Commons – Cat03.jpg](https://commons.wikimedia.org/wiki/File:Cat03.jpg) | Không commit; tải bằng script |
| `dog.jpg` | [Wikimedia Commons – YellowLabradorLooking_new.jpg](https://commons.wikimedia.org/wiki/File:YellowLabradorLooking_new.jpg) | Không commit; tải bằng script |

Tải lại tất cả:

```cmd
python tools/download_assets.py
```

Nếu máy không tải được ảnh (mất mạng, bị chặn), hãy tự chép một ảnh `.jpg` bất kỳ
(ví dụ ảnh chó, mèo, cái ba lô...) vào thư mục `samples\` rồi truyền đường dẫn cho client:

```cmd
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\anh_cua_ban.jpg
```

Giấy phép của ảnh thuộc tác giả gốc trên Wikimedia Commons (xem trang nguồn ở bảng trên).
Ảnh chỉ dùng để chạy thử phân loại trong demo. Test tự động **không** dùng các ảnh này;
test tự sinh ảnh bằng Pillow.
