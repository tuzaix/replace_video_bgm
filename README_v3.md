# 视频BGM分离和替换工具 v3.0

## 🎵 概述

这是一个基于深度学习的视频背景音乐(BGM)分离和替换工具的改进版本，参考了先进的video_separator实现模式，提供了更强大的音频分离功能、质量控制和自定义配置选项。

## 🚀 v3.0 新功能

### 1. 增强的音频分离算法
- **多种分离策略**: 支持4种不同的分离策略
  - `vocals_only`: 只保留人声
  - `vocals_and_other`: 保留人声和其他音频
  - `custom_mix`: 自定义混合比例
  - `adaptive`: 自适应策略（根据音频特征动态调整）

### 2. 音频质量控制系统
- **质量评估指标**:
  - 信噪比 (SNR)
  - 频谱质心
  - 过零率
  - RMS能量
  - 综合质量分数
- **质量阈值控制**: 可设置最低质量要求
- **实时质量监控**: 处理过程中实时评估分离质量

### 3. 音频预处理功能
- **音频标准化**: 自动调整音频电平
- **高通滤波**: 去除低频噪声
- **噪声抑制**: 智能降噪处理
- **可选启用**: 可根据需要开启或关闭

### 4. 高级配置选项
- **模型选择**: 支持多种demucs模型
- **重叠参数**: 可调整模型重叠率
- **音量控制**: 精确控制各音频源的音量
- **并发控制**: 灵活的多线程配置

## 📋 系统要求

- Python 3.8+
- Windows/macOS/Linux
- 推荐: NVIDIA GPU (支持CUDA)
- 内存: 建议12GB以上（用于高质量处理）

## 🛠️ 安装

1. 克隆或下载项目到本地
2. 安装依赖:

```bash
pip install -r requirements_v3.txt
```

### GPU支持 (推荐)

```bash
# 对于CUDA 11.6
pip install torch torchaudio --extra-index-url https://download.pytorch.org/whl/cu116
```

## 📖 使用方法

### 基本用法

```bash
python video_bgm_replacer_v3.py <视频目录> <BGM目录>
```

### 高级用法示例

#### 1. 只保留人声
```bash
python video_bgm_replacer_v3.py videos/ bgm/ --strategy vocals_only --vocals-volume 1.0
```

#### 2. 自定义混合比例
```bash
python video_bgm_replacer_v3.py videos/ bgm/ --strategy custom_mix --vocals-volume 1.0 --other-volume 0.4
```

#### 3. 高质量处理
```bash
python video_bgm_replacer_v3.py videos/ bgm/ --strategy adaptive --overlap 0.75 --quality-threshold 0.85 --workers 1
```

#### 4. 快速处理
```bash
python video_bgm_replacer_v3.py videos/ bgm/ --strategy vocals_and_other --overlap 0.1 --disable-preprocessing --disable-quality-check --workers 8
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--strategy` | 分离策略 | `adaptive` |
| `--model` | demucs模型名称 | `htdemucs` |
| `--overlap` | 模型重叠参数 | `0.25` |
| `--vocals-volume` | 人声音量 | `1.0` |
| `--other-volume` | 其他音频音量 | `0.3` |
| `--quality-threshold` | 质量阈值 | `0.7` |
| `--workers` | 并发线程数 | `4` |
| `--disable-preprocessing` | 禁用音频预处理 | `False` |
| `--disable-quality-check` | 禁用质量检查 | `False` |

## 🎯 分离策略详解

### 1. Vocals Only (vocals_only)
- **适用场景**: 需要完全去除背景音乐，只保留人声
- **特点**: 最干净的人声分离
- **推荐用途**: 语音内容、访谈、教学视频

### 2. Vocals and Other (vocals_and_other)
- **适用场景**: 保留人声和环境音，去除音乐
- **特点**: 保持自然的音频环境
- **推荐用途**: 日常视频、vlog、纪录片

### 3. Custom Mix (custom_mix)
- **适用场景**: 需要精确控制各音频源的比例
- **特点**: 完全可定制的混合方案
- **推荐用途**: 专业音频制作、特殊需求

### 4. Adaptive (adaptive)
- **适用场景**: 自动根据音频特征选择最佳策略
- **特点**: 智能化处理，无需手动调整
- **推荐用途**: 批量处理、通用场景

## 📊 质量控制

### 质量评估指标

1. **信噪比 (SNR)**: 衡量信号与噪声的比例
2. **频谱质心**: 反映音频的频率分布特征
3. **过零率**: 衡量音频的动态特性
4. **RMS能量**: 反映音频的整体能量水平
5. **综合质量分数**: 0-1范围的综合评分

### 质量阈值设置

- `0.9+`: 极高质量（适合专业制作）
- `0.8-0.9`: 高质量（适合商业用途）
- `0.7-0.8`: 良好质量（适合一般用途）
- `0.6-0.7`: 可接受质量（适合快速处理）
- `<0.6`: 低质量（需要调整参数）

## 🔧 音频预处理

### 预处理步骤

1. **音频标准化**: 将音频电平标准化到合适范围
2. **高通滤波**: 去除80Hz以下的低频噪声
3. **噪声抑制**: 基于阈值的智能降噪

### 何时禁用预处理

- 音频质量已经很好
- 需要保持原始音频特性
- 追求最快处理速度

## 📈 性能优化

### GPU优化
- 自动检测CUDA支持
- GPU内存管理
- 批处理优化

### CPU优化
- 多线程并发处理
- 内存使用优化
- 资源自动清理

### 推荐配置

| 场景 | Workers | Strategy | Overlap | 预处理 | 质量检查 |
|------|---------|----------|---------|--------|----------|
| 高质量 | 1-2 | adaptive | 0.75 | ✓ | ✓ |
| 平衡 | 4 | adaptive | 0.25 | ✓ | ✓ |
| 快速 | 8+ | vocals_and_other | 0.1 | ✗ | ✗ |

## 🐛 故障排除

### 常见问题

1. **内存不足**
   - 减少并发线程数
   - 降低overlap参数
   - 禁用预处理

2. **质量不达标**
   - 增加overlap参数
   - 启用预处理
   - 尝试不同的分离策略

3. **处理速度慢**
   - 增加并发线程数
   - 降低overlap参数
   - 禁用质量检查

### 错误代码

- `质量分数 < 阈值`: 分离质量不达标，建议调整参数

---

## 🧭 GUI 标签页扩展规范（Tab Architecture）

为了便于在桌面 GUI 中整合不同功能模块（如视频混剪、封面生成、BGM 合并、音视频分离等），本项目采用标签页（QTabWidget）架构进行组织。

### 结构概览

- MainWindow（gui/main_gui.py）
  - 使用 QTabWidget 作为中央控件。
  - 当前已有标签：
    - “视频混剪”：原主面板迁移至该标签。
    - “封面生成”：提供 cover_tool.generate_cover 的骨架页面（参数输入与占位交互）。
    - “更多功能”：占位页，用于后续扩展说明。

### 统一注册方法（register_feature_tab）

为保持一致的样式与行为，主窗口提供统一的标签页注册入口：

```python
def register_feature_tab(self, title: str, widget: QtWidgets.QWidget) -> int:
    """
    将功能页统一注册到主窗口的 QTabWidget 中，并返回注册后的索引。
    """
    index = self.tabs.addTab(widget, title)
    try:
        widget.setContentsMargins(6, 6, 6, 6)
    except Exception:
        pass
    return index
```

使用示例：

```python
concat_tab, root_layout = create_concat_tab(self)
self.register_feature_tab("视频混剪", concat_tab)

cover_tab = CoverGeneratorTab(self)
self.register_feature_tab("封面生成", cover_tab)
```

### 工厂与类的约定

- 工厂函数：
  - `gui/tabs/video_concat_tab.py:create_concat_tab(parent) -> (tab_widget, root_layout)`
  - 适用于最小侵入的迁移方案，保持 MainWindow 中既有变量与事件处理方式。
- 类封装：
  - `gui/tabs/video_concat_tab.py:VideoConcatTab(QWidget)`（已提供骨架）
  - 适用于逐步迁移控件与事件绑定到页内，MainWindow 通过清晰接口进行交互。

### 占位页与文档入口

新增“更多功能（开发中）”标签页，包含文档入口按钮以打开 `README_v3.md`，展示规划中的模块：

- 批量封面生成与导出
- BGM 智能匹配与自动淡入淡出
- 视频剪辑预览与快捷标注
- 结果表格导出 CSV/Excel


### 新增功能页的约定

1. 每个功能页应封装为一个 QWidget 子类（示例：CoverGeneratorTab）。
2. 尽量在功能页内部完成 UI 搭建与事件绑定，避免修改全局状态。
3. 使用 MainWindow.register_feature_tab(title, widget) 将功能页注册为新的标签。
4. 业务代码应放置在相应的工具模块中（如 cover_tool/generate_cover.py），GUI 通过线程安全的方式调用。

### 开发流程建议（Iterative）

1. 核心逻辑：先实现最核心的业务函数（CLI 已存在可复用）。
2. 错误处理：补充边界检查、异常捕获、日志输出。
3. 文档注释：为关键函数撰写 Docstring（Google/Numpy 风格），适当增加行内注释。

### 统一风格与一致性

- 代码需通过项目的格式化与 Lint（如 Black/Flake8 等，如已配置）。
- 避免使用过于晦涩的模式；优先可读性与一致性。
- 若功能页涉及 FFmpeg，则沿用统一的 bootstrap_ffmpeg_env 策略进行环境初始化。

### 示例：封面生成页参数

- images_dir（图片目录）
- caption（字幕文本，可选）
- per_cover（每个封面图片数）
- count（生成数量）
- workers（并发线程数）
- color（字幕颜色）

后续可将封面生成页接入 cover_tool.generate_cover.generate_covers_concurrently，实现并发生成与结果展示。该页目前为骨架实现，在 GUI 中已可见并可输入参数。
- `GPU内存不足`: 减少batch size或使用CPU
- `模型加载失败`: 检查网络连接和模型文件

## 📁 输出结构

```
video_directory/
├── mixed_bgm_video/          # 输出视频目录
│   ├── video1_with_new_bgm.mp4
│   └── video2_with_new_bgm.mp4
├── tmp/                      # 临时文件目录（自动清理）
└── bgm_replacement.log       # 处理日志
```

## 🔄 版本对比

| 功能 | v1.0 | v2.0 | v3.0 |
|------|------|------|------|
| 基本分离 | ✓ | ✓ | ✓ |
| 错误修复 | ✗ | ✓ | ✓ |
| 多种策略 | ✗ | ✗ | ✓ |
| 质量控制 | ✗ | ✗ | ✓ |
| 音频预处理 | ✗ | ✗ | ✓ |
| 自适应分离 | ✗ | ✗ | ✓ |
| 详细配置 | ✗ | ✗ | ✓ |

## 🎮 使用示例

运行示例脚本查看各种使用方式：

```bash
python example_v3.py
```

## 🧩 架构：GUI 与工作流分层（v3 项目结构优化）

为提升可维护性与清晰度，项目对“长视频混合拼接”功能进行了分层设计：

- GUI 层：`gui/main_gui.py`
  - 仅负责界面、信号与线程管理。
  - 将业务逻辑委托给工作流模块，保持代码简洁易懂。
  - 默认强制使用内置 FFmpeg（ffmpeg/bin），不再提供用户切换选项，避免环境差异导致的问题。
- 工作流层：`concat_tool/workflow.py`
  - 纯 Python 业务模块，无 Qt 依赖，便于单元测试与复用。
  - 提供 `WorkflowCallbacks` 回调与 `run_video_concat_workflow(settings, cb)` 统一入口。
- 配置层：`concat_tool/settings.py`
  - 提供共享 `Settings` dataclass，供 GUI/CLI/脚本统一使用。

这样设计可以让 GUI 与业务逻辑解耦，便于后续扩展（例如命令行入口、服务端调用）。

## 🖥️ 命令行（CLI）用法

新增 `concat_tool/cli.py`，可在终端直接运行混合拼接工作流：

```bash
python -m concat_tool.cli \
  --video-dirs D:\\videos1 D:\\videos2 \
  --bgm-path D:\\audios \
  --outputs 2 --count 5 --gpu --threads 4 \
  --width 1080 --height 1920 --fps 25 --fill pad \
  --trim-head 0.0 --trim-tail 1.0 --group-res --quality-profile balanced
```

关键参数说明：
- `--video-dirs`：输入视频目录（一个或多个）
- `--bgm-path`：BGM 文件或目录路径
- `--output`：输出路径（目录或单文件；多目录输入时请提供目录）
- `--gpu`：启用 NVENC（若可用），不可用时自动回退 CPU（日志有提示）
- `--group-res`：按分辨率分组输出；如分组不满足条件则自动回退到随机模式
- `--quality-profile`：编码质量档位（visual/balanced/size）

CLI 输出会打印阶段、进度与日志；完成后显示成功文件列表与大小。默认强制使用内置 FFmpeg（若未找到会直接报错），行为与 GUI 一致。

### FFmpeg 使用策略（默认强制内置，开发可兜底）

- 默认：GUI 与 CLI 均首选并仅使用内置 FFmpeg，优先从打包目录或 `vendor/ffmpeg/bin` 解析，将其插入到 PATH 前端。
- 未找到内置：GUI 显示“不可用”，CLI 报错退出，避免系统 PATH 带来的差异与不稳定。
- 开发兜底（隐藏参数）：若确需在开发环境临时允许系统 FFmpeg 兜底，可设置环境变量 `FFMPEG_DEV_FALLBACK` 为 `1/true/yes/on`（不区分大小写）。启用后，若内置未找到，将回退到系统 PATH 中的 ffmpeg/ffprobe。

#### CLI（macOS）使用示例

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

注：macOS 下推荐使用 `python3`；路径包含空格时请使用引号包裹。

#### CLI（Windows）使用示例（含开发兜底可选项）

```
:: 可选：开发环境允许系统 ffmpeg 兜底（仅开发）
set FFMPEG_DEV_FALLBACK=1

python -m concat_tool.cli ^
  --video-dirs "D:\\videos1" "D:\\videos2" ^
  --bgm-path "D:\\audios" ^
  --outputs 2 --count 5 --gpu --threads 4 ^
  --width 1080 --height 1920 --fps 25 --fill pad ^
  --trim-head 0.0 --trim-tail 1.0 --group-res --quality-profile balanced
```

#### 发行包包含 FFmpeg 的要求

- Windows：仓库提供 `build_gui_exe.bat`，若存在 `vendor\ffmpeg\bin`，打包时会通过 `--add-data "vendor\ffmpeg\bin;ffmpeg\bin"` 自动内置 FFmpeg。
- macOS/Linux：使用 PyInstaller 打包时，请确保将 `vendor/ffmpeg/bin` 以数据文件形式打入产物，并在运行时由 `gui/precheck/ffmpeg_paths.py` 解析 `ffmpeg/bin`。
- 开发环境：如未准备好内置 FFmpeg，可临时设置 `FFMPEG_DEV_FALLBACK=1` 进行系统 PATH 兜底（不建议用于发布版本）。

### 统一启动策略（Bootstrap）

为确保各入口脚本（GUI、CLI、视频分离/替换工具、封面提取等）在相同策略下寻找 FFmpeg/FFprobe，项目提供了统一的引导函数：

示例代码：

```
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env

# 默认行为：优先使用内置 FFmpeg，开发环境允许系统兜底，并将捆绑目录写入 PATH 前端
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True)

# 严格要求存在 FFmpeg/FFprobe（若不存在则抛出错误）
bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True,
                     require_ffmpeg=True, require_ffprobe=True)

# 测试或自定义场景：覆盖捆绑目录并插入 PATH 前端（无需真实二进制，仅用于验证 PATH 注入）
bootstrap_ffmpeg_env(prefer_bundled=False, dev_fallback_env=False, modify_env=True,
                     override_bundled_dir="/tmp/fake_vendor/ffmpeg/bin")
```

说明：
- prefer_bundled=True：优先使用 `vendor/ffmpeg/bin` 或打包产物中的捆绑路径。
- dev_fallback_env=True：开发环境可通过 `FFMPEG_DEV_FALLBACK=1` 使用系统 FFmpeg 兜底。
- modify_env=True：将捆绑目录插入到 `PATH` 前端，保证子进程调用一致。
- require_ffmpeg / require_ffprobe：要求可执行存在，否则抛出 `FileNotFoundError`。
- override_bundled_dir：覆盖捆绑目录（测试专用），无需真实 ffmpeg 可执行。

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个工具。

## 📞 支持

如果遇到问题，请查看日志文件或提交Issue。