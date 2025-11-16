"""
生成横向拼接的封面图片（screen_cover）的小工具。

功能概述：
- 从一个图片目录中随机选择若干图片（默认每个封面 3 张）。
- 调用 `youtube.thumbnail.generate_thumbnail(image_paths=..., caption=...)` 生成横向拼接的封面图片（可叠加字幕）。
- 将拼接后的封面图片保存到图片目录下的 `screen_cover/` 子目录。

命令行参数：
- `images_dir`：图片目录（必填）。
- `--caption`：字幕文本（可选）。
- `--count`：要生成的封面图片个数（默认 10）。
- `--per-cover`：每个封面由几张图片组成（默认 3）。
- `--workers`：并发线程数（默认根据 CPU，通常为 4）。
- `--seed`：随机种子（可选，便于复现）。
- `--color`：字幕颜色（默认 yellow）。

依赖：
- 依赖项目内的 `youtube/thumbnail.py`（仅调用其中的 `generate_thumbnail` 进行合成）。
"""

import argparse
import os
import random
import sys
import uuid
from typing import List, Optional
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
 


# 允许从项目根目录运行 `python tools/generate_screen_covers.py`
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def list_images(images_dir: str) -> List[str]:
    """列出目录中的图片文件路径。

    - 仅扫描一级目录，不递归。
    - 支持常见图片扩展名：jpg/jpeg/png/webp/bmp。

    Args:
        images_dir: 图片目录路径。

    Returns:
        图片文件绝对路径列表。
    """
    files: List[str] = []
    for name in os.listdir(images_dir):
        p = os.path.join(images_dir, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in IMAGE_EXTS:
            files.append(os.path.abspath(p))
    return files


def choose_images(candidates: List[str], k: int) -> List[str]:
    """从候选图片中选择 k 张。

    - 若候选数 >= k，使用 `random.sample` 无重复抽取。
    - 若候选数 < k，使用 `random.choices` 允许重复抽取。

    Args:
        candidates: 候选图片路径列表。
        k: 选择数量。

    Returns:
        选择的图片路径列表（长度为 k）。
    """
    if not candidates:
        return []
    if len(candidates) >= k:
        return random.sample(candidates, k)
    return random.choices(candidates, k=k)


def ensure_dir(path: str) -> None:
    """确保目录存在，不存在则创建。"""
    os.makedirs(path, exist_ok=True)


# 不再导出视频，因此移除分辨率/帧率相关解析


def save_stitched_cover(stitched_image_path: str, images_dir: str) -> str:
    """把拼接好的封面图片移动到 `images_dir/封面/` 并返回新路径。

    Args:
        stitched_image_path: `generate_thumbnail` 返回的图片路径。
        images_dir: 用户提供的图片目录路径，用于创建 `封面` 子目录。

    Returns:
        新的封面图片路径（JPG）。
    """
    out_dir = os.path.join(images_dir, "封面")
    ensure_dir(out_dir)
    ext = os.path.splitext(stitched_image_path)[1].lower() or ".jpg"
    out_name = f"封面_{uuid.uuid4().hex[:8]}{ext}"
    out_path = os.path.join(out_dir, out_name)

    try:
        shutil.move(stitched_image_path, out_path)
        return out_path
    except Exception as e:
        print(f"Error copying stitched cover: {e}")
        return stitched_image_path


def _color_to_bgr(color: str) -> tuple:
    """将颜色名映射为 OpenCV 的 BGR 元组。"""
    m = {
        "yellow": (0, 255, 255),
        "white": (255, 255, 255),
        "black": (0, 0, 0),
        "red": (0, 0, 255),
        "green": (0, 255, 0),
        "blue": (255, 0, 0),
    }
    return m.get(color.lower(), (0, 255, 255))


def generate_thumbnail(
    image_paths: List[str],
    caption: Optional[str] = None,
    color: str = "yellow",
    position: Optional[tuple] = None,
    align: str = "left",
    captions: Optional[list[tuple[str, tuple[float, float]]]] = None,
) -> str:
    """生成横向拼接的缩略图并返回临时文件路径。

    - 使用 OpenCV 读取图片，按统一高度缩放后横向拼接。
    - 可选叠加字幕文本并绘制半透明背景增强可读性。
    - 支持单字幕（`caption` + `position`）或多字幕（`captions=[(text,(xr,yr)), ...]`）。
      多字幕时忽略单字幕参数；水平对齐通过 `align` 指定：'left' | 'center' | 'right'。
    - 输出为临时 JPG 文件路径。

    Returns: 临时文件路径；如果无可读图片则抛出 ValueError。
    """
    # 延迟导入，避免模块导入阶段因缺失 OpenCV 导致报错
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise ImportError("OpenCV (cv2) 未安装。请执行 `pip install opencv-python-headless` 后重试封面生成。")

    imgs = []
    for p in image_paths:
        try:
            img = cv2.imread(p)
            if img is not None:
                imgs.append(img)
        except Exception:
            continue

    if not imgs:
        raise ValueError("No readable images provided for stitching")

    heights = [im.shape[0] for im in imgs]
    target_h = max(240, min(min(heights), 720))

    resized = []
    for im in imgs:
        h, w = im.shape[:2]
        scale = target_h / float(h)
        new_w = max(1, int(round(w * scale)))
        resized.append(cv2.resize(im, (new_w, target_h), interpolation=cv2.INTER_AREA))

    try:
        stitched = np.hstack(resized)
    except Exception:
        max_w = max(r.shape[1] for r in resized)
        padded = []
        for r in resized:
            pad_w = max_w - r.shape[1]
            if pad_w > 0:
                pad = np.zeros((r.shape[0], pad_w, 3), dtype=r.dtype)
                padded.append(np.concatenate([r, pad], axis=1))
            else:
                padded.append(r)
        stitched = np.hstack(padded)

    # 叠加字幕：优先使用多字幕；否则使用单字幕
    bgr = _color_to_bgr(color)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.6, min(1.5, stitched.shape[0] / 480.0))
    thickness = int(round(font_scale * 2))
    w, h = stitched.shape[1], stitched.shape[0]

    def draw_one(text: str, xr: float, yr: float) -> None:
        nonlocal stitched
        text = str(text)
        (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)
        xr = max(0.0, min(1.0, float(xr))); yr = max(0.0, min(1.0, float(yr)))
        x = int(round(xr * w))
        y = int(round(yr * h))
        if align == "center":
            x = x - tw // 2
        elif align == "right":
            x = x - tw
        y = max(th + 6, min(h - 6, y))
        x0, y0 = x - 6, y - th - 6
        x1, y1 = x + tw + 6, y + baseline + 6
        overlay = stitched.copy()
        cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 0, 0), thickness=cv2.FILLED)
        alpha = 0.35
        stitched = cv2.addWeighted(overlay, alpha, stitched, 1 - alpha, 0)
        cv2.putText(stitched, text, (x, y), font, font_scale, bgr, thickness, lineType=cv2.LINE_AA)

    if captions and len(captions) > 0:
        for item in captions:
            try:
                t, pos = item
                xr, yr = float(pos[0]), float(pos[1])
            except Exception:
                t, xr, yr = "", 0.02, 0.95
            if t:
                draw_one(t, xr, yr)
    elif caption:
        xr, yr = (0.02, 0.95) if position is None else (float(position[0]), float(position[1]))
        draw_one(str(caption), xr, yr)

    tmp_path = os.path.join(tempfile.gettempdir(), f"stitched_cover_{uuid.uuid4().hex[:8]}.jpg")
    ok = cv2.imwrite(tmp_path, stitched)
    if not ok:
        raise IOError("Failed to write stitched cover image")
    return tmp_path


def generate_one_cover(
    images_dir: str,
    image_paths: List[str],
    caption: str | None,
    color: str = "yellow",
    position: Optional[tuple] = None,
    align: str = "left",
    output_dir: Optional[str] = None,
    captions: Optional[list[tuple[str, tuple[float, float]]]] = None,
) -> str | None:
    """从若干图片生成一个横向拼接的封面图片并保存到 `封面/`。

    Returns the final saved path or None on failure.
    """
    stitched_path = generate_thumbnail(
        image_paths=image_paths,
        caption=caption,
        color=color,
        position=position,
        align=align,
        captions=captions,
    )
    if not stitched_path or not os.path.exists(stitched_path):
        print("Failed to generate stitched cover image.")
        return None

    # 保存到指定输出目录；若未指定则默认保存到 `images_dir/封面` 下
    out_base = output_dir if output_dir else images_dir
    out_path = save_stitched_cover(stitched_path, out_base)
    return out_path


def generate_covers_concurrently(
    images_dir: str,
    all_images: List[str],
    count: int,
    per_cover: int,
    caption: str | None,
    color: str,
    workers: int,
    caption_position: Optional[tuple] = None,
    caption_align: str = "left",
    progress_cb: Optional[callable] = None,
    output_dir: Optional[str] = None,
    captions: Optional[list[tuple[str, tuple[float, float]]]] = None,
) -> int:
    """并发生成封面图片。

    - 先在主线程预生成每个任务的图片选择，避免并发影响随机数状态。
    - 使用线程池并发执行 `generate_one_cover`。
    - 可选 `caption_position=(x_ratio, y_ratio)` 与 `caption_align` 控制字幕位置与对齐。
    - 若提供 `progress_cb(idx, path, (w,h))`，每次生成成功后回调一次。

    Returns:
        成功生成的封面数量。
    """
    tasks: List[List[str]] = [choose_images(all_images, per_cover) for _ in range(count)]
    # 预览任务队列
    for i, picks in enumerate(tasks, start=1):
        print(f"[queued {i}/{count}] Using images: {', '.join(os.path.basename(p) for p in picks)}")

    ok_count = 0
    workers = max(1, int(workers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                generate_one_cover,
                images_dir,
                picks,
                caption,
                color,
                caption_position,
                caption_align,
                output_dir,
                captions,
            ): i
            for i, picks in enumerate(tasks, start=1)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                result = future.result()
                if result:
                    ok_count += 1
                    print(f"[done {idx}/{count}] Generated cover: {result}")
                    # 回调当前封面分辨率
                    try:
                        import cv2
                        im = cv2.imread(result)
                        if im is not None:
                            h, w = im.shape[:2]
                            if callable(progress_cb):
                                progress_cb(idx, result, (w, h))
                    except Exception:
                        if callable(progress_cb):
                            progress_cb(idx, result, (0, 0))
                else:
                    print(f"[done {idx}/{count}] Failed to generate cover")
            except Exception as e:
                print(f"[done {idx}/{count}] Exception: {e}")
    return ok_count


def main() -> None:
    """命令行入口：批量生成横向拼接的封面图片（截图）。"""
    parser = argparse.ArgumentParser(description="Generate stitched cover images from a directory of photos.")
    parser.add_argument("images_dir", help="截图目录")
    parser.add_argument("--caption", default=None, help="字幕文本")
    parser.add_argument("--count", type=int, default=10, help="生成的封面图片个数，默认 10")
    parser.add_argument("--per-cover", type=int, default=4, help="每个封面由几张图片组成，默认 4")
    parser.add_argument("--seed", type=int, default=int(time.time()), help="随机种子，可选")
    parser.add_argument("--color", default="yellow", help="字幕颜色，默认 yellow")
    workers_default = max(1, min(8, (os.cpu_count() or 1)))
    parser.add_argument("--workers", type=int, default=workers_default, help=f"并发线程数，默认 {workers_default}")

    args = parser.parse_args()

    images_dir = os.path.abspath(args.images_dir)
    if not os.path.isdir(images_dir):
        print(f"Not a directory: {images_dir}")
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)

    all_images = list_images(images_dir)
    if not all_images:
        print(f"No images found in {images_dir}")
        sys.exit(1)

    print(
        f"Found {len(all_images)} images. Generating {args.count} covers, "
        f"{args.per_cover} images per cover, workers={args.workers}."
    )

    generated = generate_covers_concurrently(
        images_dir=images_dir,
        all_images=all_images,
        count=args.count,
        per_cover=args.per_cover,
        caption=args.caption,
        color=args.color,
        workers=args.workers,
        output_dir=None,
    )

    print(f"Done. Successfully generated {generated}/{args.count} covers.")


if __name__ == "__main__":
    main()