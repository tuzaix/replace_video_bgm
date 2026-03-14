#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Subtitle Generator Module
使用大模型平台 API (如豆包语音) 为视频生成 SRT 字幕。
"""

import os
import sys
import json
import time
import subprocess
import shutil
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import urllib.request
import urllib.parse

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.common_utils import get_subprocess_silent_kwargs, format_srt_timestamp, is_video_file
from utils.bootstrap_ffmpeg import bootstrap_ffmpeg_env
# from utils.xprint import xprint

# Bootstrap FFmpeg
env = bootstrap_ffmpeg_env(prefer_bundled=True, dev_fallback_env=True, modify_env=True, require_ffmpeg=True)
ffmpeg_bin = env.get("ffmpeg_path") or shutil.which("ffmpeg")

def xprint(*values: object) -> dict:
    # 在系统上设置 DEBUG=1 来启用调试打印，默认不打印，
    # 例如：linux: export DEBUG=1 或 win: $env:DEBUG=1
    print(*values)



class ASRProvider:
    """ASR 抽象基类，用于支持不同的 AI 平台"""
    def transcribe(self, audio_path: Path) -> str:
        """识别音频并返回 SRT 格式字符串"""
        raise NotImplementedError

class VolcengineASR(ASRProvider):
    """火山引擎豆包语音识别实现 (Volcengine Doubao Voice)"""
    def __init__(self, appid: str, token: str):
        """
        参数
        ----
        appid: str
            火山引擎应用标识。
        token: str
            火山引擎访问令牌。
        """
        self.appid = appid
        self.token = token
        self.submit_url = "https://openspeech.bytedance.com/api/v1/vc/submit"
        self.query_url = "https://openspeech.bytedance.com/api/v1/vc/query"

    def transcribe(self, audio_path: Path) -> str:
        """执行语音识别流程：提交 -> 轮询 -> 解析"""
        # 1. 提交识别任务
        task_id = self._submit(audio_path)
        if not task_id:
            raise RuntimeError("火山引擎 ASR 任务提交失败")
        
        xprint(f"[*] ASR 任务提交成功，任务 ID: {task_id}")

        # 2. 轮询识别结果
        result = self._poll_result(task_id)
        if not result:
            raise RuntimeError("火山引擎 ASR 任务查询失败或超时")

        # 3. 解析 JSON 结果并生成 SRT 内容
        return self._parse_to_srt(result)

    def _submit(self, audio_path: Path) -> Optional[str]:
        """提交音频二进制数据到识别接口"""
        params = {
            "appid": self.appid,
            "language": "zh-CN",
            "words_per_line": 12,
            "use_itn": "False",
            "use_punc": "False",
            "max_lines": 1,
            "words_per_line": 15,
        }
        query_str = urllib.parse.urlencode(params)
        url = f"{self.submit_url}?{query_str}"
        
        try:
            with open(audio_path, "rb") as f:
                data = f.read()
            
            headers = {
                "Content-Type": "audio/wav",
                "Authorization": f"Bearer; {self.token}"
            }
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            
            with urllib.request.urlopen(req) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                if str(resp_data.get("code")) == "0":
                    return resp_data.get("id")
                else:
                    xprint(f"[-] 火山引擎 ASR 提交错误: {resp_data.get('message')} (Code: {resp_data.get('code')})")
        except Exception as e:
            xprint(f"[-] 火山引擎 ASR 提交异常: {e}")
        return None

    def _poll_result(self, task_id: str, timeout: int = 600) -> Optional[Dict[str, Any]]:
        """轮询识别任务结果"""
        params = {
            "appid": self.appid,
            "id": task_id,
        }
        query_str = urllib.parse.urlencode(params)
        url = f"{self.query_url}?{query_str}"
        
        headers = {
            "Authorization": f"Bearer; {self.token}"
        }
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req) as response:
                    resp_data = json.loads(response.read().decode("utf-8"))
                    code = int(resp_data.get("code", -1))
                    
                    if code == 0:
                        # 成功完成
                        return resp_data
                    
                    # 检查是否为进行中 (2000)
                    if code == 2000:
                        time.sleep(5)
                        continue
                    else:
                        xprint(f"[-] 火山引擎 ASR 查询失败: {resp_data.get('message')} (Code: {code})")
                        return None
            except Exception as e:
                xprint(f"[-] 火山引擎 ASR 查询异常: {e}")
                time.sleep(5)
        
        xprint("[-] 火山引擎 ASR 识别超时")
        return None

    def _parse_to_srt(self, result: Dict[str, Any]) -> str:
        """将 API 返回的分句结果转换为 SRT 格式"""
        utterances = result.get("utterances", [])
        srt_lines = []
        for i, utt in enumerate(utterances, 1):
            start_ms = utt.get("start_time", 0)
            end_ms = utt.get("end_time", 0)
            text = utt.get("text", "")
            
            # format_srt_timestamp 接受秒作为参数
            start_str = format_srt_timestamp(start_ms / 1000.0)
            end_str = format_srt_timestamp(end_ms / 1000.0)
            
            srt_lines.append(f"{i}")
            srt_lines.append(f"{start_str} --> {end_str}")
            srt_lines.append(text)
            srt_lines.append("")
        
        return "\n".join(srt_lines)

def extract_audio_for_asr(video_path: Path, output_dir: Path) -> Path:
    """
    使用 FFmpeg 提取音频为 ASR 识别优化的格式。
    格式：16kHz, 单声道, 16bit PCM WAV。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{video_path.stem}.wav"
    # 判断文件是否已经存在，如果存在则直接返回即可
    if audio_path.exists():
        xprint(f"[*] 音频文件已存在: {audio_path}")
        return audio_path
    
    # 提取音频参数：
    # -vn: 禁用视频
    # -ar 16000: 采样率 16kHz (ASR 常用)
    # -ac 1: 单声道
    # -acodec pcm_s16le: 16bit PCM 编码
    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(video_path),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-acodec", "pcm_s16le",
        str(audio_path)
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, **get_subprocess_silent_kwargs())
        return audio_path
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
        xprint(f"[-] FFmpeg 提取音频失败: {err_msg}")
        raise RuntimeError(f"FFmpeg 提取音频失败: {err_msg}")

def run_subtitle_generation(video_path: str, provider: ASRProvider):
    """主执行流程：提取音频 -> 调用 ASR -> 保存字幕"""
    v_path = Path(video_path).resolve()
    if not v_path.exists():
        xprint(f"[-] 视频文件不存在: {video_path}")
        return

    # 0. 检查字幕文件是否已存在
    srt_path = v_path.with_suffix(".srt")
    if srt_path.exists() and srt_path.stat().st_size > 0:
        xprint(f"[!] 字幕文件已存在，跳过处理: {srt_path.name}")
        return

    # 1. 提取音频到同目录下的 audio 子目录
    audio_dir = v_path.parent / "audio"
    xprint(f"[*] 正在为识别准备音频: {v_path.name}")
    try:
        audio_path = extract_audio_for_asr(v_path, audio_dir)
        xprint(f"[+] 音频提取完成: {audio_path}")

        # 2. 调用 API 进行语音识别
        xprint(f"[*] 正在调用 API 识别字幕...")
        srt_content = provider.transcribe(audio_path)
        
        # 3. 保存 SRT 字幕文件到视频同级目录
        srt_path = v_path.with_suffix(".srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        xprint(f"[+] 字幕生成成功: {srt_path}")
        
    except Exception as e:
        xprint(f"[-] 处理过程中出现错误: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="API 视频字幕生成工具 (支持火山引擎/豆包语音)")
    parser.add_argument("input_path", help="待处理的视频文件路径或视频所在的目录路径")
    parser.add_argument("--appid", help="火山引擎 AppID (也可通过环境变量 VOLC_APPID 设置)")
    parser.add_argument("--token", help="火山引擎 Access Token (也可通过环境变量 VOLC_TOKEN 设置)")
    
    args = parser.parse_args()
    
    # 获取认证信息
    appid = args.appid or os.environ.get("VOLC_APPID")
    token = args.token or os.environ.get("VOLC_TOKEN")
    
    if not appid or not token:
        xprint("[-] 错误: 请提供 AppID 和 Token。")
        xprint("    使用方式: python api_subtitle_generator.py <input_path> --appid XXX --token YYY")
        xprint("    或者设置环境变量: VOLC_APPID 和 VOLC_TOKEN")
        sys.exit(1)
        
    # 初始化火山引擎识别服务
    provider = VolcengineASR(appid, token)
    
    # 确定待处理的视频文件列表
    input_path = Path(args.input_path).resolve()
    if not input_path.exists():
        xprint(f"[-] 输入路径不存在: {input_path}")
        sys.exit(1)

    video_files = []
    if input_path.is_file():
        if is_video_file(input_path.name):
            video_files.append(input_path)
        else:
            xprint(f"[-] 文件不是有效的视频格式: {input_path}")
            sys.exit(1)
    elif input_path.is_dir():
        xprint(f"[*] 正在扫描目录中的视频文件: {input_path}")
        for file in input_path.iterdir():
            if file.is_file() and is_video_file(file.name):
                video_files.append(file)
        
        if not video_files:
            xprint(f"[-] 目录中没有找到有效的视频文件: {input_path}")
            sys.exit(1)
        
        xprint(f"[+] 找到 {len(video_files)} 个视频文件待处理。")

    # 开始生成字幕
    for idx, video_file in enumerate(video_files, 1):
        if len(video_files) > 1:
            xprint(f"\n[进度 {idx}/{len(video_files)}] 正在处理: {video_file.name}")
        run_subtitle_generation(str(video_file), provider)
