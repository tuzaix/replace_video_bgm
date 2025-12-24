import sys
import traceback

from .video_detect_scenes import VideoDetectScenes

def main() -> None:
    #video_path = r"E:\Download\社媒助手\抖音\夏夏\20240421-弟弟说紫色很有韵味？#街拍 #谁还不是小腰精 #这谁顶得住啊 #今天长这样 #路人视角.mp4"
    video_path = r"E:\Download\社媒助手\抖音\夏夏\20250904-在华为新品发布会露个脸^_^#高级模特 #御姐范儿 #拍摄花絮.mp4"
    
    threshold = 0.5
    try:
        detect_scenes = VideoDetectScenes(device="auto", threshold=threshold)
        saved = detect_scenes.save(video_path)
        clips_meta = list(saved.get("clips_meta", []))
        print("AI检测完成，前3个镜头：")
        preview_count = min(3, len(clips_meta))
        for i in range(preview_count):
            m = clips_meta[i]
            item = {
                "start_frame": int(m.get("start_frame", 0)),
                "end_frame": int(m.get("end_frame", 0)),
                "start_time": f"{float(m.get('start_time', 0.0)):.2f}s",
                "end_time": f"{float(m.get('end_time', 0.0)):.2f}s",
                "path": str(m.get("path", "")),
            }
            print(item)
    except RuntimeError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print(f"错误：镜头分割执行失败: {e}", file=sys.stderr)
        sys.exit(1)

    print("已保存镜头分割结果：")
    print(f"  - JSON: {saved.get('json_path')}")
    print(f"  - TXT: {saved.get('txt_path')}")
    clips = saved.get("clips") or []
    print(f"  - Clips: {len(clips)} 个")
    if clips:
        print(f"  - Clips 目录: {saved.get('output_dir')}")


if __name__ == "__main__":
    main()
