"""Chọn ảnh trên chính máy client.

Có giao diện (Windows/Linux desktop): mở hộp thoại chọn file của tkinter.
Không mở được hộp thoại (Termux, SSH, thiếu tkinter): gõ đường dẫn trong terminal,
hoặc kéo-thả file vào cửa sổ cmd (cmd tự dán đường dẫn, có ngoặc kép nếu có dấu cách).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path

IMAGE_TYPES = [("Ảnh", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tất cả file", "*.*")]

Printer = Callable[[str], None]
Reader = Callable[[str], str]


def pick_with_dialog(multiple: bool, title: str) -> list[Path] | None:
    """Ảnh đã chọn (rỗng nếu bấm Cancel); None nếu không mở được hộp thoại."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:  # không có màn hình
        return None
    try:
        root.withdraw()
        root.attributes("-topmost", True)  # hộp thoại nổi trên cửa sổ cmd
        if multiple:
            names = list(
                filedialog.askopenfilenames(parent=root, title=title, filetypes=IMAGE_TYPES)
            )
        else:
            name = filedialog.askopenfilename(parent=root, title=title, filetypes=IMAGE_TYPES)
            names = [name] if name else []
    finally:
        root.destroy()
    return [Path(name) for name in names]


def parse_typed_paths(text: str) -> list[Path]:
    """`"C:\\a b\\x.jpg" C:\\y.jpg` -> [C:\\a b\\x.jpg, C:\\y.jpg]; giữ nguyên dấu `\\`."""
    try:
        parts = shlex.split(text, posix=False)
    except ValueError:  # thiếu ngoặc đóng
        parts = [text]
    return [Path(p.strip().strip("\"'")) for p in parts if p.strip().strip("\"'")]


def pick_with_prompt(multiple: bool, read: Reader = input) -> list[Path]:
    hint = "một hoặc nhiều ảnh, cách nhau bằng dấu cách" if multiple else "một ảnh"
    try:
        text = read(f"  Nhập đường dẫn {hint} (có thể kéo-thả file vào đây), Enter để thoát: ")
    except EOFError:
        return []
    paths = parse_typed_paths(text)
    return paths if multiple else paths[:1]


def pick_images(
    multiple: bool = True,
    title: str = "Chọn ảnh cần nhận diện",
    out: Printer = print,
    read: Reader = input,
) -> list[Path]:
    """Hộp thoại chọn file; không mở được thì hỏi đường dẫn trong terminal."""
    picked = pick_with_dialog(multiple, title)
    if picked is None:
        out("  Không mở được hộp thoại chọn file, hãy nhập đường dẫn ảnh.")
        picked = pick_with_prompt(multiple, read)
    return picked
