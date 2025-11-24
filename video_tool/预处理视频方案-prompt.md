这是一个针对**“保持原分辨率”**需求定制的完整方案。

### 核心策略变化

与之前的方案相比，本次策略的核心调整如下：
1.  **分辨率策略**：**不进行任何缩放（No Scaling）**。输入是 4K 输出就是 4K，输入是竖屏输出就是竖屏。
2.  **容错处理**：虽然保持分辨率，但 H.264 编码器要求视频的长宽必须是 **2 的倍数**（偶数）。如果原视频是 `1080x1919` 这种奇数分辨率，直接转码会报错。因此脚本会自动检测并补齐边缘（Padding），确保长宽为偶数，但视觉上看不出变化。
3.  **统一标准**：所有视频统一为 `MP4` 容器、`H.264` 编码、`AAC` 音频、`yuv420p` 色彩空间（兼容性之王）。

---

### 1. 清晰度归一化模式选择

根据短视频的生产链路，我们提供三种基于“码率控制”的模式，供用户选择：

| 模式 | 适用场景 | 参数逻辑 (CPU/GPU) | 预期效果 |
| :--- | :--- | :--- | :--- |
| **1. High (无损/后期)** | **剪辑素材归一化**。后续还要放入 PR/剪映 二次剪辑。 | CRF 18 / CQ 19 / High Profile | 画质几乎无损，文件体积较大，保留所有细节。 |
| **2. Standard (发布)** | **成品归一化**。直接上传抖音/YouTube/B站。 | CRF 23 / CQ 23 / Main Profile | **推荐**。视觉无损，体积适中，平台转码后画质最佳。 |
| **3. Lite (预览)** | **样片流转**。微信/钉钉传输给客户确认内容。 | CRF 28 / CQ 28 / Baseline | 画质尚可，体积非常小，传输快。 |

---

### 2. 完整 Python 自动化脚本

请创建文件 `keep_res_convert.py`，粘贴以下代码：

```python
import os
import subprocess
import sys
import platform
import shutil

# ================= 配置区域 =================

# 支持处理的视频后缀
VIDEO_EXTENSIONS = ('.mp4', '.mov', '.mkv', '.avi', '.flv', '.wmv', '.ts', '.m4v', '.webm')

class AutoNormalizer:
    def __init__(self, input_dir):
        self.input_dir = input_dir
        self.output_dir = os.path.join(input_dir, "output_normalized")
        self.gpu_type = self.detect_hardware()
        print(f"\n✨ 硬件检测结果: \033[92m{self.gpu_type.upper()}\033[0m 加速已开启")

    def detect_hardware(self):
        """
        检测 FFmpeg 支持的硬件编码器
        """
        try:
            # 获取编码器列表
            result = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], 
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            output = result.stdout
            
            # 优先级判定：NVIDIA > Apple > Intel > CPU
            if 'h264_nvenc' in output:
                return 'nvidia'
            elif 'h264_videotoolbox' in output and platform.system() == 'Darwin':
                return 'mac'
            elif 'h264_qsv' in output:
                return 'intel'
            else:
                return 'cpu'
        except FileNotFoundError:
            print("❌ 错误: 未在系统中找到 ffmpeg，请先安装并配置环境变量。")
            sys.exit(1)

    def get_params(self, mode):
        """
        根据模式和硬件生成参数
        核心逻辑：保持分辨率 + 偶数化处理 + 统一H264/AAC
        """
        
        # 1. 基础参数：H.264 + AAC + yuv420p(兼容性最好) + faststart(利于网络播放)
        # pad=ceil(iw/2)*2:ceil(ih/2)*2 意思是：如果长宽是奇数，补齐1像素为偶数，如果是偶数则不变
        vf_filter = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
        
        cmd_base = [
            '-c:a', 'aac', '-b:a', '320k' if mode == 'high' else '192k', '-ar', '48000',
            '-pix_fmt', 'yuv420p', 
            '-movflags', '+faststart',
            '-vf', vf_filter
        ]

        # 2. 编码器特定参数
        video_params = []
        
        if self.gpu_type == 'nvidia':
            # NVENC
            encoder = 'h264_nvenc'
            # p1-p7, p7最慢质量最好
            preset = 'p7' if mode == 'high' else 'p4' 
            # CQ (Constant Quality) 模式
            cq = '19' if mode == 'high' else ('23' if mode == 'standard' else '28')
            # high profile 兼容性稍差但画质好，main 兼容性好
            profile = 'high' if mode == 'high' else 'main'
            
            video_params = [
                '-c:v', encoder, 
                '-preset', preset, 
                '-rc', 'vbr',       # 动态码率
                '-cq', cq,          # 质量控制因子
                '-profile:v', profile
            ]

        elif self.gpu_type == 'mac':
            # Apple VideoToolbox
            encoder = 'h264_videotoolbox'
            # 质量 0-100
            q_val = '75' if mode == 'high' else ('65' if mode == 'standard' else '50')
            video_params = ['-c:v', encoder, '-q:v', q_val]

        elif self.gpu_type == 'intel':
            # Intel QSV
            encoder = 'h264_qsv'
            q_val = '18' if mode == 'high' else ('23' if mode == 'standard' else '28')
            video_params = ['-c:v', encoder, '-global_quality', q_val, '-preset', 'medium']

        else:
            # CPU (libx264) - 兜底方案
            encoder = 'libx264'
            crf = '18' if mode == 'high' else ('23' if mode == 'standard' else '28')
            preset = 'slow' if mode == 'high' else ('medium' if mode == 'standard' else 'veryfast')
            video_params = ['-c:v', encoder, '-crf', crf, '-preset', preset]

        return video_params + cmd_base

    def run(self, mode):
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

        files = [f for f in os.listdir(self.input_dir) if f.lower().endswith(VIDEO_EXTENSIONS)]
        total = len(files)
        
        if total == 0:
            print("⚠️  目录下没有找到视频文件。")
            return

        print(f"\n🚀 准备处理 {total} 个视频文件...")
        print(f"📂 输出路径: {self.output_dir}")
        print(f"🔧 当前模式: {mode.upper()} (保持原分辨率)\n")

        params = self.get_params(mode)

        for i, filename in enumerate(files):
            input_path = os.path.join(self.input_dir, filename)
            # 输出文件名统一改为mp4
            file_name_no_ext = os.path.splitext(filename)[0]
            output_path = os.path.join(self.output_dir, f"{file_name_no_ext}.mp4")

            print(f"[{i+1}/{total}] 正在处理: {filename}")
            
            try:
                # 组装命令
                cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', input_path] + params + [output_path]
                
                # 执行
                subprocess.run(cmd, check=True)
                print(f"   ✅ 成功")
            except subprocess.CalledProcessError:
                print(f"   ❌ 失败 (该文件可能已损坏或格式不支持)")
            except Exception as e:
                print(f"   ❌ 错误: {e}")

        print("\n🎉 全部处理完成！")

def main():
    print("==================================================")
    print("       🎥 视频归一化工具 (分辨率透传版)")
    print("       自动检测 GPU | 统一 H.264 MP4 AAC")
    print("==================================================")

    # 获取路径
    path_input = input("请输入视频文件夹路径: ").strip().replace('"', '').replace("'", "")
    
    if not os.path.exists(path_input):
        print("❌ 路径不存在")
        return

    # 选择模式
    print("\n请选择归一化清晰度模式:")
    print("1. \033[96mStandard\033[0m (推荐) - 视觉无损，适合上传平台")
    print("2. \033[93mHigh\033[0m     (素材) - 接近原画，适合后期剪辑")
    print("3. \033[90mLite\033[0m     (预览) - 小体积，适合网络传输")
    
    choice = input("\n请输入序号 (1/2/3) [默认1]: ").strip()
    mode_map = {'1': 'standard', '2': 'high', '3': 'lite'}
    selected_mode = mode_map.get(choice, 'standard')

    app = AutoNormalizer(path_input)
    app.run(selected_mode)

if __name__ == "__main__":
    main()
```

---

### 3. 方案技术细节解析

#### 为什么需要 `pad` 滤镜？
这是本方案中最关键的技术细节。虽然需求是“保持分辨率”，但 H.264 编码标准是基于宏块（Macroblock）的，通常要求像素长宽能被 2 整除。
*   **问题**：如果用户扔进去一个 `1079x1920` 的视频，FFmpeg 会报错 `width not divisible by 2`。
*   **解决**：代码中使用了 `pad=ceil(iw/2)*2:ceil(ih/2)*2`。
    *   如果是 `1920x1080` -> `1920x1080` (不变)。
    *   如果是 `1919x1080` -> `1920x1080` (在边缘补 1 像素黑边)。
    *   这确保了脚本能处理任意来源的视频而不会崩溃。

#### 统一参数说明
无论原视频是 AVI, MKV 还是 MOV，处理后将获得完全一致的技术指标，极大方便后续批处理：
*   **Container**: `.mp4` (最通用的封装)。
*   **Video Codec**: `H.264 (High/Main Profile)`。
*   **Pixel Format**: `yuv420p` (解决部分手机拍摄视频在 PR/AE 里颜色过曝或无法解码的问题)。
*   **Audio**: `AAC, 48000Hz` (标准广播级音频参数)。
*   **Faststart**: 开启 Web 优化（元数据移到文件头），使得视频上传到网页后能边下边播。

#### 硬件加速参数映射
针对不同清晰度，GPU 的参数做了精细映射，保证利用率最大化：

*   **NVIDIA**: 使用 `-cq` (Constant Quality) 而非固定的 `-b:v` 码率，这样简单的画面码率低，复杂的画面码率高，节省空间。
*   **Intel QSV**: 使用 `-global_quality` 控制 ICQ。
*   **CPU**: 使用经典的 `-crf` 算法。

### 4. 如何使用

1.  确保电脑安装了 `FFmpeg` 和 `Python`。
2.  保存上方代码为 `convert.py`。
3.  在终端运行：`python convert.py`。
4.  将包含乱七八糟参数视频的**文件夹路径**拖入终端。
5.  选择模式（通常选 **1** 即可）。
6.  等待处理完成，结果会在该文件夹下的 `output_normalized` 目录中。