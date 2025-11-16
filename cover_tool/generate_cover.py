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

def compute_draw_area_16_9_by_width(img_w: int, img_h: int, padding_pct: float = 0.05) -> tuple[int, int, int, int]:
    """计算图片上的 16:9 居中绘制区域，先剔除四边 padding，再按宽度优先计算，必要时按高度回退。

    输入支持两种形式：
    - 单值：`padding_pct: float`，表示左右留白比例（左/右各 `padding_pct`），上下为 0；
    - 四元组/列表：`(left, top, right, bottom)`，每个值可为**比例**（<= 1.0，建议 0~0.2）或**像素**（> 1.0）。

    算法：
    1) 将四边 padding 统一转换为**像素**：
       - 若为比例：`px = round(img_w * ratio)`（左右）或 `round(img_h * ratio)`（上下），比例会被夹到 [0, 0.2]；
       - 若为像素：直接使用非负值；
    2) 先从有效区域尺寸 `w_eff = img_w - pl_px - pr_px`、`h_eff = img_h - pt_px - pb_px` 出发，按**宽度优先**计算：
       - `draw_w = w_eff`、`draw_h = round(draw_w * 9/16)`；
       - 若 `draw_h > h_eff`，则回退为按**高度优先**：`draw_h = h_eff`、`draw_w = round(draw_h * 16/9)`；
    3) 在有效区域内**居中**放置该矩形并返回 `(left_px, top_px, draw_w, draw_h)`。

    该函数与 GUI 中的 16:9 活动区域逻辑保持一致，适用于将控件坐标映射到图片绘制区域。
    """
    try:
        # 解析 4 边 padding，支持比例或像素
        def _parse_pad(val, is_horizontal: bool) -> int:
            # 比例（<=1.0）按对应维度转换为像素，比例夹到 [0, 0.2]; 像素（>1.0）直接取非负
            if val is None:
                return 0
            v = float(val)
            if v <= 1.0:
                ratio = max(0.0, min(0.2, v))
                base = img_w if is_horizontal else img_h
                return int(round(base * ratio))
            # 像素值
            return max(0, int(round(v)))

        if isinstance(padding_pct, (tuple, list)) and len(padding_pct) == 4:
            pl_px = _parse_pad(padding_pct[0], True)
            pt_px = _parse_pad(padding_pct[1], False)
            pr_px = _parse_pad(padding_pct[2], True)
            pb_px = _parse_pad(padding_pct[3], False)
        else:
            # 单值：左右为比例或像素，上下为 0
            v = float(padding_pct)
            pl_px = _parse_pad(v, True)
            pr_px = _parse_pad(v, True)
            pt_px = 0
            pb_px = 0

        # 有效范围尺寸（至少为 1 像素）
        w_eff = max(1, img_w - pl_px - pr_px)
        h_eff = max(1, img_h - pt_px - pb_px)

        # 宽度优先计算 16:9，必要时按高度回退
        draw_w = w_eff
        draw_h = int(round(draw_w * 9.0 / 16.0))
        if draw_h > h_eff:
            draw_h = h_eff
            draw_w = int(round(draw_h * 16.0 / 9.0))
            draw_w = max(1, min(draw_w, w_eff))

        # 在有效区域内居中
        left_px = int(round(pl_px + (w_eff - draw_w) / 2.0))
        top_px = int(round(pt_px + (h_eff - draw_h) / 2.0))
        return left_px, top_px, int(draw_w), int(draw_h)
    except Exception:
        return 0, 0, int(img_w), int(img_h)

def map_block_to_draw_area(block: dict, draw_rect: tuple[int, int, int, int]) -> dict:
    """将字幕块从控件的 16:9 活动区映射到图片绘制区域，并估算字体像素大小。

    输入字段（来自 `CaptionPositionWidget.get_blocks()`）：
    - `active_w`, `active_h`: 控件 16:9 活动区宽高（像素）
    - `pixel_x`, `pixel_y`: 块在活动区的像素坐标（左上角）
    - `box_w`, `box_h`: 文本包围框宽高（像素）
    - 可选 `font_size`: 控件中该块的字体点大小（若有则优先使用）

    返回：位置信息与缩放关系，用于在图片上绘制：
    `{ "x": int, "y": int, "font_px": int, "scale_x": float, "scale_y": float, "draw_left": int, "draw_top": int, "draw_w": int, "draw_h": int }`。
    """
    # 坐标，box宽度和高度
    sx, sy, sw, sh = draw_rect
    # 控件坐标
    obx, oby, obw, obh = int(block.get("pixel_x", 0)), int(block.get("pixel_y", 0)), int(block.get("box_w", 0)), int(block.get("box_h", 0))

    # 控件的有效区域宽高
    ow = int(block.get("active_w", 0))
    oh = int(block.get("active_h", 0))

    # 映射的box宽和高
    sbw = int(round((sw/ow) * obw))
    sbh = int(round((sh/oh) * obh))

    # 映射的坐标(box的左上角)
    sbx = int(round((sw/ow) * obx + sx))
    sby = int(round((sh/oh) * oby + sy))

    # 映射的字体像素大小
    font_px = int(max(8, round((sbh / obh) * int(block.get("font_size", 0)))))

    # 调试用的映射box中心你点
    mid_x = sbx + sbw / 2
    mid_y = sby + sbh / 2

    return {
        "draw_ract_x": sx,      # 绘制区域的左上角x坐标
        "draw_ract_y": sy,      # 绘制区域的左上角y坐标
        "draw_ract_width": sw,  # 绘制区域的宽度
        "draw_ract_height": sh, # 绘制区域的高度
        "map_text_box_x": sbx,       # 映射box的左上角x坐标
        "map_text_box_y": sby,       # 映射box的左上角y坐标
        "map_text_box_width": sbw,   # 映射box的宽度
        "map_text_box_height": sbh,  # 映射box的高度
        "map_text_font_px": font_px, # 映射box的字体像素大小
        "map_text_box_centerpoint_x": int(mid_x), # 映射box的中心x坐标，用于调试
        "map_text_box_centerpoint_y": int(mid_y), # 映射box的中心y坐标，用于调试
    }

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
    img = stitch_images_with_blend(image_paths, blend_width=150)
    # 适配到 16:9 画布（以宽度为基准，等比居中裁剪/留边）
    # try:
    #     return fit_canvas_to_aspect_by_width(img, aspect_w=16, aspect_h=9, fill_color=(0, 0, 0))
    # except Exception:
    #     return img
    return img 

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
    caption_blocks: Optional[list[dict]] = None
) -> object:
    """在基础拼接图上合成多个字幕块（Unicode 安全）。

    修复中文乱码：改用 Pillow 或 Qt 绘制支持 Unicode 的字体。
    优先级：Pillow → PySide6/Qt → OpenCV（仅英文，保留作为兜底）。

    - 每个字幕块支持：`text`, `position(xr,yr)`, `font_size`, `font_bold`, `font_italic`(不直接支持),
      `color`, `bgcolor`(含透明度), `stroke_color`, `align`。

    返回合成后图片（同输入类型）。
    """
    if not caption_blocks: # 没字幕则直接返回
        return base_img
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

    # 尝试使用 Pillow（推荐，支持中文 TrueType 字体）
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        pil_available = True
    except Exception:
        import traceback
        traceback.print_exc()
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

            def _resolve_chinese_font(bold: bool, font_family: Optional[str] = None) -> Optional[str]:
                """根据 `font_family` 与粗体标志解析字体文件路径。

                优先级：
                1) 若 `font_family` 是存在的文件路径，直接使用；
                2) 若为项目内已知家族名或文件名，匹配 `gui/fonts` 下的对应 OTF；
                3) 若为 Windows 常见中文字体家族，映射到系统字体文件；
                4) 以上都未命中时，按粗体与常用项目字体回退。
                """
                try:
                    if font_family:
                        ff = str(font_family).strip()
                        # 显式文件路径
                        if _os_local.path.isfile(ff):
                            return ff

                        # 规范化家族名
                        ff_lower = ff.lower()
                        fonts_dir = _os_local.path.join(PROJECT_ROOT_LOCAL, "gui", "fonts")
                        known_project = {
                            "sourcehansanscn-regular": "SourceHanSansCN-Regular.otf",
                            "sourcehansanscn-normal": "SourceHanSansCN-Normal.otf",
                            "sourcehansanscn-medium": "SourceHanSansCN-Medium.otf",
                            "sourcehansanscn-bold": "SourceHanSansCN-Bold.otf",
                            "sourcehansanscn-heavy": "SourceHanSansCN-Heavy.otf",
                            "sourcehansanscn-light": "SourceHanSansCN-Light.otf",
                        }
                        # 兼容不带权重的家族名：按粗体选择
                        if ff_lower == "sourcehansanscn":
                            ff_lower = "sourcehansanscn-bold" if bold else "sourcehansanscn-regular"
                        if ff_lower in known_project:
                            p = _os_local.path.join(fonts_dir, known_project[ff_lower])
                            if _os_local.path.isfile(p):
                                return p

                        # Windows 字体家族映射
                        win_map = {
                            "microsoft yahei": r"C:\\Windows\\Fonts\\msyh.ttc",
                            "微软雅黑": r"C:\\Windows\\Fonts\\msyh.ttc",
                            "msyh": r"C:\\Windows\\Fonts\\msyh.ttc",
                            "msyhbd": r"C:\\Windows\\Fonts\\msyhbd.ttc",  # 粗体
                            "simhei": r"C:\\Windows\\Fonts\\simhei.ttf",
                            "黑体": r"C:\\Windows\\Fonts\\simhei.ttf",
                            "simsun": r"C:\\Windows\\Fonts\\simsun.ttc",
                            "宋体": r"C:\\Windows\\Fonts\\simsun.ttc",
                            "arial unicode ms": r"C:\\Windows\\Fonts\\arialuni.ttf",
                            "arialuni": r"C:\\Windows\\Fonts\\arialuni.ttf",
                            "notosanscjk": r"C:\\Windows\\Fonts\\NotoSansCJK-Regular.ttc",
                        }
                        # 针对粗体的 yahei 变体
                        if bold and ff_lower in {"microsoft yahei", "微软雅黑", "msyh"}:
                            ff_lower = "msyhbd" if "msyhbd" in win_map else ff_lower
                        if ff_lower in win_map and _os_local.path.isfile(win_map[ff_lower]):
                            return win_map[ff_lower]
                except Exception:
                    pass

                # 默认项目字体回退
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
                # Windows 常见中文字体作为兜底
                win_candidates = [
                    r"C:\\Windows\\Fonts\\msyh.ttc",
                    r"C:\\Windows\\Fonts\\simhei.ttf",
                    r"C:\\Windows\\Fonts\\simsun.ttc",
                    r"C:\\Windows\\Fonts\\arialuni.ttf",
                    r"C:\\Windows\\Fonts\\NotoSansCJK-Regular.ttc",
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

            # 计算 16:9 居中绘制区域（左右各 5% 留白）
            draw_rect = compute_draw_area_16_9_by_width(W, H, padding_pct=(0.05, 0.03, 0.05, 0.03))
            # 在原图上标示 16:9 绘制区域：浅灰半透明填充 + 边框
            try:
                dl, dt, dw, dh = draw_rect
                # 透明度统一为 20%（alpha ≈ 51/255）
                # 优化透明度：填充约 18%（更易看见背景），边框约 40%
                draw.rectangle(
                    [dl, dt, dl + dw, dt + dh],
                    fill=(229, 231, 235, 46),
                    outline=(160, 160, 160, 102),
                    width=2,
                )
            except Exception:
                pass
              # 获取中心点
            mid_x, mid_y = draw_rect[0] + draw_rect[2] / 2, draw_rect[1] + draw_rect[3] / 2
            # 画个中心点
            draw.ellipse(
                [mid_x - 10, mid_y - 10, mid_x + 10, mid_y + 10],
                fill=(255, 0, 0, 128),
                outline=(255, 0, 0, 255),
                width=2,
            )

            import pprint
            for block in caption_blocks:
                pprint.pprint(block)

                try:
                    t = _ensure_unicode_text(block.get("text", ""))
                    if not t:
                        continue
                    balign = str(block.get("align", "left"))
                    color_hex = str(block.get("color", "#ffffffff"))
                    bg_hex = str(block.get("bgcolor", "#00000000"))
                    stroke_hex = str(block.get("stroke_color", "#00000000"))
                    font_family = str(block.get("font_family", "SourceHanSansCN-Regular"))
                    bbold = bool(block.get("font_bold", False))

                    # 使用活动区映射计算绘制坐标与字号
                    mapped = map_block_to_draw_area(block, draw_rect)
                    px_size = int(mapped.get("map_text_font_px", 18))
                    stroke_w = int(max(0, round(px_size * 0.12))) + (1 if bbold else 0)

                    map_text_box_centerpoint_x = int(mapped.get("map_text_box_centerpoint_x", mid_x))
                    map_text_box_centerpoint_y = int(mapped.get("map_text_box_centerpoint_y", mid_y))
                    # 换一个蓝点，用于定位
                    draw.ellipse(
                        [map_text_box_centerpoint_x - 10, map_text_box_centerpoint_y - 10, map_text_box_centerpoint_x + 10, map_text_box_centerpoint_y + 10],
                        fill=(0, 0, 255, 128),
                        outline=(0, 0, 255, 255),
                        width=2,
                    )
                   

                    # 加载中文字体（优先项目字体）
                    font_path = _resolve_chinese_font(bold=bbold, font_family=font_family)
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

                    # 文本尺寸与定位（依照映射后的包围框与基线）
                    # 先用一个临时位置测量真实 bbox
                    bbox = draw.textbbox((0, 0), t, font=font, stroke_width=stroke_w)
                    tw = max(1, bbox[2] - bbox[0])
                    th = max(1, bbox[3] - bbox[1])

                    # 映射后的包围框与基线（基线为 box 底部）
                    dl, dt, dw, dh = draw_rect
                    sbx = int(mapped.get("map_text_box_x", dl))
                    sby = int(mapped.get("map_text_box_y", dt))
                    sbw = int(mapped.get("map_text_box_width", 0))
                    sbh = int(mapped.get("map_text_box_height", 0))
                    y = sby + sbh*0.75 # 重新计算y轴坐标，非常重要

                    # 根据对齐选择锚点并换算文本左上角 x
                    if balign == "center":
                        anchor_x = sbx + sbw // 2
                        x = anchor_x - tw // 2
                    elif balign == "right":
                        anchor_x = sbx + sbw
                        x = anchor_x - tw
                    else:
                        x = sbx

                    # 夹紧到绘制区域范围（保留 6px 边距；y 为基线，绘制时会减去 th）
                    y = max(dt + th + 6, min(dt + dh - 6, y))
                    x = max(dl + 6, min(dl + dw - tw - 6, x))

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
    else:
        return base_img

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
    caption_blocks: Optional[list[dict]] = None
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
        stitched = render_caption_blocks(stitched, caption_blocks)

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
            stitched_path = generate_thumbnail_single(image_paths=picks, caption_blocks=caption_blocks)
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

