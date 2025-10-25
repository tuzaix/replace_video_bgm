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

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个工具。

## 📞 支持

如果遇到问题，请查看日志文件或提交Issue。