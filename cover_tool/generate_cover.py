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


def _imread_unicode(path: str, flags: int = 1):
    """Safely read images from paths containing non-ASCII characters on Windows.

    - OpenCV's `cv2.imread` may fail on Unicode paths on Windows.
    - This helper uses `np.fromfile` + `cv2.imdecode` to bypass that limitation.
    - Falls back to `cv2.imread` if needed; returns `None` on failure.
    """
    try:
        import numpy as np
        import cv2
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, flags)
        return img
    except Exception:
        try:
            import cv2
            return cv2.imread(path, flags)
        except Exception:
            return None


def _rgba_hex_to_bgra(hex_rgba: str) -> tuple[int, int, int, int]:
    """将 '#rrggbbaa' 或 '#rrggbb' 转换为 BGRA 元组。

    - 输入示例："#ffcc00"（不透明）或 "#ffcc00cc"（含透明度）。
    - 返回值：`(b, g, r, a)`，a 为 0-255。
    """
    s = (hex_rgba or "").strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 6:
        r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16); a = 255
    elif len(s) == 8:
        r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16); a = int(s[6:8], 16)
    else:
        r, g, b, a = 0, 0, 0, 0
    return b, g, r, a


def stitch_images(image_paths: List[str]) -> object:
    """生成基础拼接图（不含字幕）。

    - 使用 Unicode 安全的读取方式 `_imread_unicode` 读取图片。
    - 将所有图片按统一高度缩放后横向拼接；若列宽不一致则右侧填充黑边。
    - 返回拼接后的 `numpy.ndarray`（BGR）。

    Raises:
    - ValueError: 当所有输入图片均不可读取时。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise ImportError("OpenCV (cv2) 未安装。请执行 `pip install opencv-python-headless` 后重试封面生成。")

    imgs = []
    for p in image_paths:
        try:
            img = _imread_unicode(p, cv2.IMREAD_COLOR)
            if img is not None:
                imgs.append(img)
        except Exception:
            continue
    if not imgs:
        raise ValueError("No readable images provided for stitching")

    heights = [im.shape[0] for im in imgs]
    # 保持原有图片决定的拼接分辨率：统一到最小高度，避免任何上采样
    target_h = min(heights)

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
    return stitched


def build_stitched_image(image_paths: List[str]) -> object:
    """构建基础拼接图（公共封装）。

    目的：
    - 将“拼接图片”步骤独立为一个函数，便于上层按两步调用：
      1) `build_stitched_image(...)` 生成基础拼接图；
      2) `overlay_captions(...)` 在基础图上叠加字幕样式。

    参数：
    - `image_paths`: 参与拼接的图片路径列表。

    返回：
    - `numpy.ndarray`（BGR），基础拼接图，不含字幕。

    异常：
    - 当所有输入图片均不可读取时抛出 `ValueError`。
    """
    # 默认改为含过渡的拼接，提升视觉效果
    return stitch_images_with_blend(image_paths, blend_width=150)


def stitch_images_with_blend(image_paths: List[str], blend_width: int = 24) -> object:
    """生成带横向过渡的拼接图（在相邻图片的接缝处做线性混合）。

    原因分析：
    - 旧实现使用 `np.hstack` 直接拼接，接缝处为硬边界，无过渡（"过渡未生效"）。
    - 为提升观感，在每个相邻图片接缝处增加 `blend_width` 像素的线性混合带。

    实现要点：
    - 统一高度缩放各图（同 `stitch_images`）。
    - 从第一张开始，逐张拼接；在接缝区域按线性权重将左图的末段与右图的起段混合：
      `blended = left * w_left + right * w_right`，其中权重在 [1→0] 与 [0→1] 线性变化。
    - 输出总宽为所有宽度之和减去每个接缝的重叠宽度（避免重复像素）。

    参数：
    - `image_paths`: 参与拼接的图片路径列表。
    - `blend_width`: 每个接缝的混合带宽度（像素，自动根据相邻宽度取最小值）。

    返回：
    - `numpy.ndarray`（BGR），带接缝过渡的拼接图。

    Raises:
    - ValueError: 当所有输入图片均不可读取时。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise ImportError("OpenCV (cv2) 未安装。请执行 `pip install opencv-python-headless` 后重试封面生成。")

    # 读取并筛选可用图片
    imgs = []
    for p in image_paths:
        try:
            img = _imread_unicode(p, cv2.IMREAD_COLOR)
            if img is not None:
                imgs.append(img)
        except Exception:
            continue
    if not imgs:
        raise ValueError("No readable images provided for stitching")

    # 统一高度（与原逻辑一致）
    heights = [im.shape[0] for im in imgs]
    # 保持原有图片决定的拼接分辨率：统一到最小高度，避免任何上采样
    target_h = min(heights)

    resized = []
    for im in imgs:
        h, w = im.shape[:2]
        scale = target_h / float(h)
        new_w = max(1, int(round(w * scale)))
        resized.append(cv2.resize(im, (new_w, target_h), interpolation=cv2.INTER_AREA))

    # 单图直接返回
    if len(resized) == 1:
        return resized[0]

    # 逐张拼接，接缝处线性混合
    out = resized[0]
    for i in range(1, len(resized)):
        right = resized[i]
        h = out.shape[0]
        w_left = out.shape[1]
        w_right = right.shape[1]
        bw = int(max(1, min(blend_width, w_left, w_right)))

        # 新输出宽度：左宽 + 右宽 - 重叠宽度
        new_w = w_left + w_right - bw
        new_out = np.zeros((h, new_w, 3), dtype=out.dtype)

        # 左侧非重叠区域直接拷贝
        left_keep = w_left - bw
        if left_keep > 0:
            new_out[:, :left_keep, :] = out[:, :left_keep, :]

        # 接缝过渡区域线性混合
        left_overlap = out[:, w_left - bw : w_left, :].astype(np.float32)
        right_overlap = right[:, :bw, :].astype(np.float32)
        # 权重从左到右线性变化
        w_right_vec = np.linspace(0.0, 1.0, bw, dtype=np.float32).reshape(1, bw, 1)
        w_left_vec = 1.0 - w_right_vec
        blended = left_overlap * w_left_vec + right_overlap * w_right_vec
        blended = np.clip(blended, 0, 255).astype(out.dtype)
        new_out[:, left_keep : left_keep + bw, :] = blended

        # 右侧非重叠区域拷贝（去掉用于混合的起始 bw 列）
        if w_right - bw > 0:
            new_out[:, left_keep + bw : , :] = right[:, bw:, :]

        out = new_out

    return out


def render_caption_blocks(
    base_img: object,
    caption_blocks: Optional[list[dict]],
    default_align: str = "left",
) -> object:
    """在基础拼接图上合成多个字幕块（Unicode 安全）。

    修复中文乱码：改用 Pillow 或 Qt 绘制支持 Unicode 的字体。
    优先级：Pillow → PySide6/Qt → OpenCV（仅英文，保留作为兜底）。

    - 每个字幕块支持：`text`, `position(xr,yr)`, `font_size`, `font_bold`, `font_italic`(不直接支持),
      `color`, `bgcolor`(含透明度), `stroke_color`, `align`。

    返回合成后图片（同输入类型）。
    """
    # 本地工程根与文本处理工具
    import os as _os_local
    try:
        PROJECT_ROOT_LOCAL = PROJECT_ROOT
    except NameError:
        PROJECT_ROOT_LOCAL = _os_local.path.abspath(_os_local.path.join(_os_local.path.dirname(__file__), ".."))

    def _ensure_unicode_text(x) -> str:
        if isinstance(x, bytes):
            try:
                return x.decode("utf-8")
            except Exception:
                return x.decode("utf-8", errors="ignore")
        return str(x)

    if not caption_blocks:
        return base_img

    # 尝试使用 Pillow（推荐，支持中文 TrueType 字体）
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        pil_available = True
    except Exception:
        pil_available = False

    if pil_available:
        try:
            # 转 PIL RGBA
            def _np_bgr_to_pil_rgba(arr):
                import cv2
                return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA))

            def _pil_rgba_to_np_bgr(img_rgba):
                import cv2
                return cv2.cvtColor(np.array(img_rgba), cv2.COLOR_RGBA2BGR)

            def _rgba_hex_to_rgba(s: str) -> tuple[int, int, int, int]:
                b, g, r, a = _rgba_hex_to_bgra(s)
                return (r, g, b, a)

            def _resolve_chinese_font(bold: bool) -> Optional[str]:
                # 优先项目内字体
                candidates = [
                    _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts", "SourceHanSansCN-Bold.otf") if bold else _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts", "SourceHanSansCN-Regular.otf"),
                    _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts", "SourceHanSansCN-Normal.otf"),
                    _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts", "SourceHanSansCN-Medium.otf"),
                    _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts", "SourceHanSansCN-Heavy.otf") if bold else _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts", "SourceHanSansCN-Light.otf"),
                ]
                for p in candidates:
                    try:
                        if _os_local.path.isfile(p):
                            return p
                    except Exception:
                        continue
                # Windows 常见中文字体作为回退
                win_candidates = [
                    r"C:\\Windows\\Fonts\\msyh.ttc",  # 微软雅黑
                    r"C:\\Windows\\Fonts\\simhei.ttf",  # 黑体
                    r"C:\\Windows\\Fonts\\simsun.ttc",  # 宋体
                    r"C:\\Windows\\Fonts\\arialuni.ttf",  # Arial Unicode MS
                    r"C:\\Windows\\Fonts\\NotoSansCJK-Regular.ttc",  # Noto CJK（若安装）
                ]
                for p in win_candidates:
                    try:
                        if _os_local.path.isfile(p):
                            return p
                    except Exception:
                        continue
                return None

            img_rgba = _np_bgr_to_pil_rgba(base_img)
            W, H = img_rgba.size
            draw = ImageDraw.Draw(img_rgba)

            # 基准缩放与字体大小近似（保持与原 cv2 路径视觉相近）
            base_scale = max(0.6, min(1.5, H / 480.0))

            for block in caption_blocks:
                try:
                    t = _ensure_unicode_text(block.get("text", ""))
                    if not t:
                        continue
                    pos = block.get("position", (0.0, 0.0))
                    xr = max(0.0, min(1.0, float(pos[0])))
                    yr = max(0.0, min(1.0, float(pos[1])))
                    balign = str(block.get("align", default_align if default_align in {"left", "center", "right"} else "left"))
                    color_hex = str(block.get("color", "#ffffffff"))
                    bg_hex = str(block.get("bgcolor", "#00000000"))
                    stroke_hex = str(block.get("stroke_color", "#00000000"))
                    bfont_size = int(block.get("font_size", 18))
                    bbold = bool(block.get("font_bold", False))

                    # 计算近似像素字号与描边宽度
                    font_scale_b = max(0.4, min(4.0, base_scale * (bfont_size / 18.0)))
                    px_size = int(max(8, round(font_scale_b * 18)))
                    stroke_w = int(max(0, round(font_scale_b * 2))) + (1 if bbold else 0)

                    # 加载中文字体（优先项目字体）
                    font_path = _resolve_chinese_font(bold=bbold)
                    if font_path:
                        try:
                            try:
                                font = ImageFont.truetype(font_path, px_size, layout_engine=getattr(ImageFont, "LAYOUT_BASIC", None))
                            except Exception:
                                font = ImageFont.truetype(font_path, px_size)
                        except Exception:
                            font = ImageFont.load_default()
                    else:
                        font = ImageFont.load_default()

                    # 文本尺寸与定位
                    # 先用一个临时位置测量真实 bbox
                    bbox = draw.textbbox((0, 0), t, font=font, stroke_width=stroke_w)
                    tw = max(1, bbox[2] - bbox[0])
                    th = max(1, bbox[3] - bbox[1])
                    x = int(round(xr * W))
                    y = int(round(yr * H))
                    if balign == "center":
                        x -= tw // 2
                    elif balign == "right":
                        x -= tw
                    y = max(th + 6, min(H - 6, y))

                    # 背景矩形（半透明）
                    rgba_bg = _rgba_hex_to_rgba(bg_hex)
                    if rgba_bg[3] > 0:
                        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        ovr = ImageDraw.Draw(overlay)
                        x0, y0 = x - 6, y - th - 6
                        x1, y1 = x + tw + 6, y + 6
                        ovr.rectangle([x0, y0, x1, y1], fill=rgba_bg)
                        img_rgba = Image.alpha_composite(img_rgba, overlay)
                        draw = ImageDraw.Draw(img_rgba)

                    # 文本颜色与描边颜色
                    rgba_text = _rgba_hex_to_rgba(color_hex)
                    rgba_stroke = _rgba_hex_to_rgba(stroke_hex)

                    # 在透明层上绘制文本以便 alpha 叠加
                    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    ovr = ImageDraw.Draw(overlay)
                    try:
                        ovr.text((x, y - th), t, font=font, fill=rgba_text, stroke_width=stroke_w if rgba_stroke[3] > 0 else 0, stroke_fill=rgba_stroke if rgba_stroke[3] > 0 else None)
                    except Exception:
                        # 某些 Pillow 版本不支持 stroke_*；退化为无描边
                        ovr.text((x, y - th), t, font=font, fill=rgba_text)
                    img_rgba = Image.alpha_composite(img_rgba, overlay)
                    draw = ImageDraw.Draw(img_rgba)
                except Exception:
                    continue

            # 转回 BGR ndarray
            return _pil_rgba_to_np_bgr(img_rgba)
        except Exception:
            # Pillow 路径失败则继续尝试 Qt
            pass

    # Qt 路径（PySide6）作为回退，支持 Unicode 字体
    try:
        from PySide6 import QtGui
        import numpy as np
        import cv2

        def _resolve_font_qt(bold: bool) -> QtGui.QFont:
            fam = "Source Han Sans CN"
            font = QtGui.QFont(fam)
            font.setBold(bool(bold))
            # 若项目字体未注册，尝试加载
            try:
                import os as _os_qt
                fonts_dir = _os_qt.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts")
                if _os_qt.path.isdir(fonts_dir):
                    for name in _os_qt.listdir(fonts_dir):
                        lower = name.lower()
                        if lower.endswith(".ttf") or lower.endswith(".otf"):
                            lp = _os_qt.path.join(fonts_dir, name)
                            QtGui.QFontDatabase.addApplicationFont(lp)
            except Exception:
                pass
            return font

        img = base_img.copy()
        h, w = img.shape[:2]
        base_scale = max(0.6, min(1.5, h / 480.0))

        # 转 QImage
        qimg = QtGui.QImage(img.data, w, h, img.strides[0], QtGui.QImage.Format.Format_BGR888)
        qimg = qimg.copy()  # detatch
        painter = QtGui.QPainter(qimg)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        for block in caption_blocks:
            try:
                t = _ensure_unicode_text(block.get("text", ""))
                if not t:
                    continue
                pos = block.get("position", (0.0, 0.0))
                xr = max(0.0, min(1.0, float(pos[0])))
                yr = max(0.0, min(1.0, float(pos[1])))
                balign = str(block.get("align", default_align if default_align in {"left", "center", "right"} else "left"))
                color_hex = str(block.get("color", "#ffffffff"))
                bg_hex = str(block.get("bgcolor", "#00000000"))
                stroke_hex = str(block.get("stroke_color", "#00000000"))
                bfont_size = int(block.get("font_size", 18))
                bbold = bool(block.get("font_bold", False))

                font_scale_b = max(0.4, min(4.0, base_scale * (bfont_size / 18.0)))
                px_size = int(max(8, round(font_scale_b * 18)))

                font = _resolve_font_qt(bbold)
                font.setPixelSize(px_size)
                painter.setFont(font)

                # 文本度量
                fm = QtGui.QFontMetrics(font)
                tw = max(1, fm.horizontalAdvance(t))
                th = max(1, fm.height())
                x = int(round(xr * w))
                y = int(round(yr * h))
                if balign == "center":
                    x -= tw // 2
                elif balign == "right":
                    x -= tw
                y = max(th + 6, min(h - 6, y))

                # 背景矩形
                _, _, _, ba = _rgba_hex_to_bgra(bg_hex)
                if ba > 0:
                    bb, bg, br, ba2 = _rgba_hex_to_bgra(bg_hex)
                    painter.fillRect(x - 6, y - th - 6, tw + 12, th + 12, QtGui.QColor(br, bg, bb, ba2))

                # 描边（通过多次偏移绘制近似描边）
                sb, sg, sr, sa = _rgba_hex_to_bgra(stroke_hex)
                cb, cg, cr, ca = _rgba_hex_to_bgra(color_hex)
                if sa > 0:
                    pen = QtGui.QPen(QtGui.QColor(sr, sg, sb, sa))
                    pen.setWidth(max(1, int(round(px_size * 0.12))))
                    painter.setPen(pen)
                    painter.drawText(x, y, t)
                pen = QtGui.QPen(QtGui.QColor(cr, cg, cb, ca))
                painter.setPen(pen)
                painter.drawText(x, y, t)
            except Exception:
                continue

        painter.end()
        # 转回 BGR ndarray
        ptr = qimg.bits()
        ptr.setsize(qimg.height() * qimg.bytesPerLine())
        arr = np.array(ptr, dtype=np.uint8).reshape((qimg.height(), qimg.width(), 3))
        return arr.copy()
    except Exception:
        pass

    # 最后兜底：OpenCV（不支持中文，会出现乱码，仅保留以避免崩溃）
    try:
        import cv2
        img = base_img
        h, w = img.shape[0], img.shape[1]
        font = cv2.FONT_HERSHEY_SIMPLEX
        base_scale = max(0.6, min(1.5, h / 480.0))
        for block in caption_blocks:
            try:
                t = _ensure_unicode_text(block.get("text", ""))
                if not t:
                    continue
                pos = block.get("position", (0.0, 0.0))
                xr = max(0.0, min(1.0, float(pos[0])))
                yr = max(0.0, min(1.0, float(pos[1])))
                balign = str(block.get("align", default_align if default_align in {"left", "center", "right"} else "left"))
                color_hex = str(block.get("color", "#ffffffff"))
                bg_hex = str(block.get("bgcolor", "#00000000"))
                stroke_hex = str(block.get("stroke_color", "#00000000"))
                bfont_size = int(block.get("font_size", 18))
                bbold = bool(block.get("font_bold", False))
                font_scale_b = max(0.4, min(4.0, base_scale * (bfont_size / 18.0)))
                thickness_b = int(max(1, round(font_scale_b * 2))) + (1 if bbold else 0)
                cb, cg, cr, ca = _rgba_hex_to_bgra(color_hex)
                (tw, th), baseline = cv2.getTextSize(t, font, font_scale_b, thickness_b)
                x = int(round(xr * w))
                y = int(round(yr * h))
                if balign == "center":
                    x -= tw // 2
                elif balign == "right":
                    x -= tw
                y = max(th + 6, min(h - 6, y))
                cv2.putText(img, t, (x, y), font, font_scale_b, (cb, cg, cr), thickness_b, lineType=cv2.LINE_AA)
            except Exception:
                continue
        return img
    except Exception:
        # 如果连 OpenCV 路径也失败，直接返回原图
        return base_img


def overlay_captions(
    base_img: object,
    caption_blocks: Optional[list[dict]] = None,
    default_align: str = "left",
    legacy_caption: Optional[str] = None,
    legacy_position: Optional[tuple[float, float]] = None,
    legacy_color: str = "yellow",
    legacy_captions: Optional[list[tuple[str, tuple[float, float]]]] = None,
) -> object:
    """在基础拼接图上叠加字幕（公共封装）。

    优先使用带样式的 `caption_blocks`；若未提供则回退到老接口的单/多字幕：
    - `legacy_captions`: [(text, (xr, yr))] 列表，统一颜色与对齐。
    - `legacy_caption` + `legacy_position`: 单字幕。

    参数：
    - `base_img`: 基础拼接图（BGR ndarray）。
    - `caption_blocks`: 新版多字幕块（含样式）。
    - `default_align`: 新版字幕块的默认对齐。
    - `legacy_caption`: 旧版单字幕文本。
    - `legacy_position`: 旧版单字幕位置 (xr, yr) 范围 [0,1]。
    - `legacy_color`: 旧版字幕颜色（名称）。
    - `legacy_captions`: 旧版多字幕列表。

    返回：
    - 合成字幕后的图片（BGR ndarray）。
    """
    if caption_blocks and len(caption_blocks) > 0:
        return render_caption_blocks(base_img, caption_blocks, default_align=default_align)

    # 旧版路径：使用统一样式进行叠加
    try:
        import cv2
    except ImportError:
        raise ImportError("OpenCV (cv2) 未安装。请执行 `pip install opencv-python-headless` 后重试封面生成。")

    img = base_img
    h, w = img.shape[0], img.shape[1]
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.6, min(1.5, h / 480.0))
    thickness = int(round(font_scale * 2))
    bgr = _color_to_bgr(legacy_color)

    def draw_one_legacy(text: str, xr: float, yr: float, align: str = default_align) -> None:
        nonlocal img
        text = str(text or "")
        if not text:
            return
        (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)
        xr = max(0.0, min(1.0, float(xr))); yr = max(0.0, min(1.0, float(yr)))
        x = int(round(xr * w))
        y = int(round(yr * h))
        if align == "center":
            x -= tw // 2
        elif align == "right":
            x -= tw
        y = max(th + 6, min(h - 6, y))
        x0, y0 = x - 6, y - th - 6
        x1, y1 = x + tw + 6, y + baseline + 6
        overlay = img.copy()
        cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 0, 0), thickness=cv2.FILLED)
        alpha = 0.35
        img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)
        cv2.putText(img, text, (x, y), font, font_scale, bgr, thickness, lineType=cv2.LINE_AA)

    if legacy_captions and len(legacy_captions) > 0:
        for item in legacy_captions:
            try:
                t, pos = item
                xr, yr = float(pos[0]), float(pos[1])
            except Exception:
                t, xr, yr = "", 0.02, 0.95
            if t:
                draw_one_legacy(t, xr, yr, default_align)
    elif legacy_caption:
        xr, yr = (0.02, 0.95) if legacy_position is None else (float(legacy_position[0]), float(legacy_position[1]))
        draw_one_legacy(str(legacy_caption), xr, yr, default_align)

    return img


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
def save_stitched_cover(stitched_image_path: str, out_dir: str) -> str:
    """把拼接好的封面图片移动到 `out_dir` 并返回新路径。

    Args:
        stitched_image_path: `generate_thumbnail` 返回的图片路径。
        out_dir: 用户提供的输出目录路径。

    Returns:
        新的封面图片路径（JPG）。
    """
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


def generate_thumbnail_single(
    image_paths: List[str],
    caption_blocks: Optional[list[dict]] = None,
    default_align: str = "left",
) -> str:
    """生成一个横向拼接的封面图片并返回临时文件路径。

    精简为两步、仅保留字幕块逻辑：
    1) 使用 `build_stitched_image(image_paths)` 构建基础拼接图；
    2) 若提供 `caption_blocks`，调用 `render_caption_blocks(...)` 叠加字幕块（默认对齐 `default_align`）。

    输出为临时 JPG 文件路径；如果无可读图片则抛出 ValueError。
    """
    # 延迟导入，避免模块导入阶段因缺失 OpenCV 导致报错
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise ImportError("OpenCV (cv2) 未安装。请执行 `pip install opencv-python-headless` 后重试封面生成。")

    # 第一步：生成基础拼接图
    stitched = build_stitched_image(image_paths)

    # 第二步：仅叠加字幕块（不再支持旧版单/多字幕）
    if caption_blocks and len(caption_blocks) > 0:
        stitched = render_caption_blocks(stitched, caption_blocks, default_align=default_align)

    tmp_path = os.path.join(tempfile.gettempdir(), f"stitched_cover_{uuid.uuid4().hex[:8]}.jpg")
    ok = cv2.imwrite(tmp_path, stitched)
    if not ok:
        raise IOError("Failed to write stitched cover image")
    return tmp_path


def generate_thumbnail(
    image_paths: List[str],
    output_dir: str,
    count: int,
    per_cover: int,
    caption_blocks: Optional[list[dict]] = None,
    progress_cb: Optional[callable] = None,
) -> int:
    """批量顺序生成封面图片（去并发），并保存到指定输出目录。

    参数
    - `image_paths`: 候选图片的绝对路径列表（将从中随机抽取）。
    - `output_dir`: 合成封面输出目录（最终图片将保存在 `output_dir`）。
    - `count`: 要生成的封面数量。
    - `per_cover`: 每个封面拼接的图片数量。
    - `caption_blocks`: 字幕块列表（含文本、位置、字体参数、颜色与背景透明、描边、对齐等）。
    - `progress_cb`: 可选回调 `(idx, path, (w, h))`，每次生成成功后调用。

    返回
    - 成功生成的封面数量（int）。
    """

    ok_count = 0
    tasks: List[List[str]] = [choose_images(image_paths, per_cover) for _ in range(max(1, int(count)))]
    for i, picks in enumerate(tasks, start=1):
        print(f"[queued {i}/{count}] Using images: {', '.join(os.path.basename(p) for p in picks)}")

    for i, picks in enumerate(tasks, start=1):
        try:
            # 生成临时封面并保存到输出目录的 `封面/` 子目录
            stitched_path = generate_thumbnail_single(
                image_paths=picks,
                caption_blocks=caption_blocks,
                default_align="left",
            )
            if not stitched_path or not os.path.exists(stitched_path):
                print(f"[done {i}/{count}] Failed to generate cover")
                continue

            out_path = save_stitched_cover(stitched_path, output_dir)
            ok_count += 1
            print(f"[done {i}/{count}] Generated cover: {out_path}")

            # 回调当前封面分辨率
            try:
                import cv2
                im = _imread_unicode(out_path, flags=cv2.IMREAD_UNCHANGED)
                if im is not None:
                    h, w = im.shape[:2]
                    if callable(progress_cb):
                        progress_cb(i, out_path, (w, h))
            except Exception:
                if callable(progress_cb):
                    progress_cb(i, out_path, (0, 0))
        except Exception as e:
            print(f"[done {i}/{count}] Exception: {e}")

    return ok_count

