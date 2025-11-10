模块概述

- 随机挑选多段视频，统一分辨率/帧率后拼接为一个成片。
- 将拼接后的视频替换为指定 BGM 音轨，输出最终成品。
- 全流程默认采用 H.265（HEVC）编码，优先 GPU，加速且压缩比高。
- 内置 TS 片段缓存与裁剪策略，提升重复运行时的效率与可控性。
- 支持并发批量生成多个输出，统一线程池管理，日志清晰可追踪。
核心功能点

- 随机拼接：从输入目录中随机抽取 N 个视频，打乱顺序后拼接。
- 统一规格：对分辨率、帧率、像素格式进行标准化，保证播放兼容性。
- 编码压缩：采用 H.265 编码，高压缩比同时兼顾主观观感。
- BGM 替换：将视频原音轨替换为指定背景音乐，输出适配的 AAC 音频。
- 并发生成：根据输出数量与线程数并发执行任务，显著提升吞吐。
- 片段裁剪：支持对每个原视频的头/尾裁剪秒数，缩短素材并增加变化。
- 缓存复用：TS 中间结果按裁剪参数命名缓存，避免重复转码。
- 压缩对比：自动打印输入与输出体积对比与压缩比例，便于质量评估。
- GPU 兜底：NVENC 不可用或失败时自动回退到 CPU x265，保证稳定产出。
输入与输出

- 输入源：用户指定的一个或多个视频目录，支持常见格式（ SUPPORTED_VIDEO_EXTS ）。
- 背景音乐：指定 BGM 目录，从中按随机种子挑选音频文件（支持常见音频格式）。
- 中间产物：每段素材转为 TS 片段（无音轨），按裁剪参数命名缓存。
- 最终输出：拼接成片并替换 BGM，默认输出为 mp4 容器，视频编码为 H.265。
编码与压缩策略

- 统一编码：全流程以 H.265 为目标编码，GPU 使用 hevc_nvenc ，CPU 使用 libx265 。
- 通用选项：像素格式统一为 yuv420p ，容器优化使用 +faststart ，保证播放兼容。
- GPU 参数（示例目标：≥50% 压缩且主观观感良好）：
  - -preset p6 、 -tune hq 、 -rc vbr -cq 32 -b:v 0
  - 启用 B 帧与参考： -bf 3 -b_ref_mode middle
  - 自适应量化： -spatial_aq 1 -temporal_aq 1 -aq-strength 8
  - 预观察与 GOP： -rc-lookahead 32 -g 240
- CPU 参数（示例目标：≥50% 压缩且主观观感良好）：
  - -crf 30 -preset slow
  - -x265-params 包含 aq-mode=2 、 psy-rd/psy-rdoq 、 qcomp=0.65 、 rc-lookahead=60 、 keyint/min-keyint 、 bframes 、 ref 、 scenecut 、 limit-sao
- 可调档位：观感优先（降低 cq/crf ）、体积优先（提高 cq/crf ）、速度优先（降低 preset ）。
随机拼接与并发

- 随机策略：按自动种子打乱选中素材片段的顺序，保证每次产出不同。
- 并发执行：统一采用线程池；即使单输出也使用 max_workers=1 的线程池。
- 任务管理：收集任务、执行、聚合结果与错误，打印成功/失败摘要与产出文件列表。
TS 缓存与裁剪

- 片段转换：将每段原视频转为 TS（视频轨），移除音频（ -an ），生成缺失 PTS 并重置时间戳。
- 缓存命名： <stem>_headX_tailY.ts ，X/Y 为裁剪秒数（整数无小数，非整数一位小数）。
- 参数一致性：当裁剪参数变化，会清理不匹配的旧缓存，避免复用错误。
- 价值：高效复用中间结果，显著降低二次运行时间。
BGM 替换

- 选取策略：按随机种子从 BGM 目录中挑选音频文件。
- 合并输出：将拼接视频与 BGM 合并输出为最终成片，音频编码统一为 AAC（如 96k ）。
错误处理与日志

- 健壮性：对外部工具（ ffmpeg / ffprobe ）缺失、转码失败、文件异常等情况进行处理。
- 日志提示：打印关键步骤与参数、任务状态、压缩比例、文件大小等，便于诊断。
- 用户中断：响应 KeyboardInterrupt ，及时结束并总结状态。
配置参数与 CLI（典型项）

- --outputs ：生成的输出数量。
- --threads ：线程数，影响并发度。
- --count ：随机拼接的视频段数。
- --gpu/--no-gpu ：是否优先使用 GPU 编码（需要 ffmpeg 支持 NVENC）。
- --width --height --fps ：目标分辨率与帧率（统一规格）。
- --fill ：缩放与填充策略（如等比缩放、裁剪或留黑）。
- --trim-head --trim-tail ：每段素材的头尾裁剪秒数。
- 其他编码相关（视版本保留）： --nvenc_cq --bitrate --crf 等。
性能与兼容性

- 播放兼容：统一 yuv420p 与 +faststart ，适配主流播放器与浏览器。
- 吞吐优化：TS 缓存 + 并发执行，提升多产出场景的表现。
- GPU 加速：NVENC 极大提升编码速度，CPU 作为兜底保证稳定性。
边界条件与限制

- 已强压缩素材：若输入已高度压缩，进一步压缩空间有限；建议提升 cq/crf 。
- 超低码率音频：BGM 的码率与声道数对体积影响不大，但过低码率可能影响听感。
- 时间戳异常：部分源可能缺失/错乱 PTS，已通过 -fflags +genpts 、重置时间戳尽量规避。
使用流程示例

- 准备素材与 BGM 目录。
- 选择输出数量与线程数（如 outputs=5 threads=3 ）。
- 配置分辨率/帧率/裁剪策略。
- 启用 GPU（默认）或显式 --no-gpu 。
- 运行并查看日志中的体积对比与产出文件列表。

模块分层（v3 结构优化）

- settings.py：共享 Settings 配置数据类，供 GUI/CLI/脚本统一使用。
- workflow.py：纯业务工作流模块，提供 `run_video_concat_workflow(settings, callbacks)`；不依赖 Qt，易测试与复用。
- cli.py：命令行入口，组装 settings 并连接 callbacks，打印阶段/进度/日志。
- video_concat.py：底层操作集合（转码、分组、拼接、输出），供工作流调用。

CLI 使用示例（Windows）：

```
python -m concat_tool.cli \
  --video-dirs D:\\videos1 D:\\videos2 \
  --bgm-path D:\\audios \
  --outputs 2 --count 5 --gpu --threads 4 \
  --width 1080 --height 1920 --fps 25 --fill pad \
  --trim-head 0.0 --trim-tail 1.0 --group-res --quality-profile balanced
```

说明：CLI 默认强制使用内置 FFmpeg（ffmpeg/bin）。若未检测到内置 ffmpeg，将直接报错并退出，以避免系统环境差异造成的不稳定行为。

开发环境兜底（隐藏参数）：若确需在开发环境临时允许系统 FFmpeg 兜底，可设置环境变量 `FFMPEG_DEV_FALLBACK` 为 `1/true/yes/on`（不区分大小写）。启用后，若内置未找到，将回退到系统 PATH 中的 ffmpeg/ffprobe。

CLI 使用示例（macOS）：

```
# 可选：开发环境允许系统 ffmpeg 兜底（仅开发）
export FFMPEG_DEV_FALLBACK=1

python3 -m concat_tool.cli \
  --video-dirs "/Users/me/Videos1" "/Users/me/Videos2" \
  --bgm-path "/Users/me/Audios" \
  --outputs 2 --count 5 --gpu --threads 4 \
  --width 1080 --height 1920 --fps 25 --fill pad \
  --trim-head 0.0 --trim-tail 1.0 --group-res --quality-profile balanced
```

统一启动策略（Bootstrap）

- 本模块及 CLI 已统一集成 `utils.bootstrap_ffmpeg.bootstrap_ffmpeg_env`，优先使用内置 FFmpeg，并在开发环境允许系统兜底（通过 `FFMPEG_DEV_FALLBACK`）。
- 若需要在自定义脚本中复用该策略，请参考项目根目录 `README_v3.md` 的“统一启动策略（Bootstrap）”章节，或直接使用如下示例：

```
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env

# 在脚本一开始调用，确保后续 subprocess.run('ffmpeg') 能找到正确版本
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)
```