#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Caption Generator Module
根据视频字幕文件，调用 LLM API 生成封面文案。
"""

import os
import sys
import json
import re
import argparse
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.common_utils import is_video_file
# from utils.xprint import xprint

def xprint(*values: object) -> dict:
    # 在系统上设置 DEBUG=1 来启用调试打印，默认不打印，
    # 例如：linux: export DEBUG=1 或 win: $env:DEBUG=1
    print(*values)

class LLMProvider:
    """LLM 抽象基类，用于支持不同的 AI 平台"""
    def generate_caption(self, content: str) -> str:
        """根据内容生成封面文案"""
        raise NotImplementedError

class ZhipuLLMProvider(LLMProvider):
    """智谱 AI (GLM-4) 封面文案生成实现"""
    def __init__(self, api_key: str, model: str = "glm-4-flash-250414"):
        self.api_key = api_key
        self.model = model
        self.url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    def generate_caption(self, content: str) -> str:
        """根据字幕内容生成封面文案"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        prompt = (
            "你是一位拥有千万粉丝的短视频账号主理人，深谙用户心理与爆款逻辑。请根据我提供的【视频字幕信息】，提炼生成20条极具吸引力的视频封面文案。\n"
            "核心要求：\n"
            "1. 字数限制：每条文案严格控制在 2至6个字 之间，短促有力。\n"
            "2. 多维切入：请从以下三个维度各生成若干条，确保风格多样化：\n"
            "   - 情感共鸣类：侧重时光流逝的无奈与感伤，引发用户怀旧共鸣。\n"
            "   - 扎心痛点类：利用“损失厌恶”心理，强调时间的无情与紧迫，制造适度焦虑。\n"
            "   - 哲理金句类：升华主题，输出关于珍惜当下的人生智慧。\n"
            "3. 爆款技巧：\n"
            "   - 善用反差与动词，增强画面的动态感。\n"
            "   - 视觉友好，用词需考虑封面排版美观，避免生僻字。\n"
            "4. 输出形式：请将结果以 JSON 格式返回，JSON 数组包含多个对象，每个对象需包含 \"category\"（分类）和 \"content\"（文案内容）字段。\n"
            "格式示例：\n"
            "[\n"
            "  {\n"
            "    \"category\": \"情感共鸣\",\n"
            "    \"content\": \"时光匆匆\"\n"
            "  }\n"
            "]\n"
            "不要包含任何多余的解释、代码块标记或 Markdown 语法。"
        )
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"待分析字幕信息：\n\n{content}"}
            ],
            "temperature": 0.8,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"}
        }
        
        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            resp_data = response.json()
            
            # 提取回复内容
            if "choices" in resp_data and len(resp_data["choices"]) > 0:
                result_text = resp_data["choices"][0]["message"]["content"]
                return result_text
            else:
                xprint(f"[-] 智谱 AI 响应异常: {resp_data}")
                return ""
        except Exception as e:
            xprint(f"[-] 调用智谱 AI 异常: {e}")
            return ""

def parse_srt_to_text(srt_path: Path) -> str:
    """解析 SRT 文件并提取纯文本内容（去除时间戳和索引）"""
    if not srt_path.exists():
        return ""
    
    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 使用正则去除索引、时间戳和空行
        # 1. 去除序号
        # 2. 去除时间戳 00:00:00,000 --> 00:00:00,000
        # 3. 合并文本
        lines = content.splitlines()
        clean_lines = []
        for line in lines:
            line = line.strip()
            # 跳过数字序号
            if line.isdigit():
                continue
            # 跳过时间戳
            if '-->' in line:
                continue
            # 跳过空行
            if not line:
                continue
            clean_lines.append(line)
        
        return " ".join(clean_lines)
    except Exception as e:
        xprint(f"[-] 解析字幕文件异常 {srt_path.name}: {e}")
        return ""

def process_video_file(video_file: Path, provider: LLMProvider):
    """处理单个视频文件及其字幕"""
    # 查找同名字幕文件
    srt_path = video_file.with_suffix(".srt")
    if not srt_path.exists():
        # 如果没有 .srt，尝试 .ass 或 .vtt (虽然本脚本主要处理 api_subtitle_generator 生成的 .srt)
        for ext in [".ass", ".vtt"]:
            alt_path = video_file.with_suffix(ext)
            if alt_path.exists():
                srt_path = alt_path
                break
    
    if not srt_path.exists():
        # xprint(f"[-] 未找到同名字幕文件，跳过: {video_file.name}")
        return
    
    # 结果文件路径
    config_path = video_file.parent / f"{video_file.stem}_caption_config.json"
    if config_path.exists():
        # xprint(f"[!] 封面文案已存在，跳过: {config_path.name}")
        return

    # 提取内容
    content = parse_srt_to_text(srt_path)
    if not content:
        return

    # 调用 API 生成
    xprint(f"[*] 正在为 {video_file.name} 生成封面文案...")
    json_str = provider.generate_caption(content)
    
    if json_str:
        try:
            # 尝试解析 JSON 以确保格式正确
            # 有时模型会返回带有 ```json 的代码块，即使指定了 json_object 模式
            # 手动清理可能的 Markdown 标记
            clean_json = re.sub(r"```json\s*|\s*```", "", json_str).strip()
            data = json.loads(clean_json)
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            xprint(f"[+] 封面文案保存成功: {config_path.name}")
        except Exception as e:
            xprint(f"[-] 保存封面文案失败 {video_file.name}: {e}")
            # 保存原始结果作为备份
            try:
                with open(config_path.with_suffix(".raw.txt"), 'w', encoding='utf-8') as f:
                    f.write(json_str)
            except:
                pass

def main():
    parser = argparse.ArgumentParser(description="根据字幕内容批量生成视频封面文案")
    parser.add_argument("input_path", help="视频文件路径或包含视频文件的目录")
    parser.add_argument("--api_key", help="智谱 AI API Key (也可通过环境变量 ZHIPU_API_KEY 设置)")
    parser.add_argument("--model", default="glm-4-flash", help="智谱 AI 模型名称 (默认: glm-4-flash)")
    parser.add_argument("--workers", type=int, default=2, help="并发调用数 (默认: 2)")
    
    args = parser.parse_args()
    
    input_path = Path(args.input_path).resolve()
    if not input_path.exists():
        xprint(f"[-] 输入路径不存在: {input_path}")
        sys.exit(1)

    api_key = args.api_key or os.environ.get("ZHIPU_API_KEY")
    if not api_key:
        xprint("[-] 错误: 请提供智谱 AI API Key。")
        xprint("    使用方式: python video_caption_generator.py <input_path> --api_key XXX")
        xprint("    或者设置环境变量: ZHIPU_API_KEY")
        sys.exit(1)

    provider = ZhipuLLMProvider(api_key, args.model)

    # 扫描视频文件
    video_files = []
    if input_path.is_file():
        if is_video_file(input_path.name):
            video_files.append(input_path)
        else:
            xprint(f"[-] 文件不是有效的视频格式: {input_path.name}")
            return
    else:
        video_files = [f for f in input_path.iterdir() if f.is_file() and is_video_file(f.name)]
    
    if not video_files:
        xprint(f"[-] 未找到可处理的视频文件: {input_path}")
        return

    xprint(f"[*] 找到 {len(video_files)} 个视频文件，准备生成封面文案...")

    # 如果只有一个文件，不使用线程池以方便调试
    if len(video_files) == 1:
        process_video_file(video_files[0], provider)
    else:
        # 并发处理
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(process_video_file, vf, provider): vf for vf in video_files}
            for future in as_completed(futures):
                vf = futures[future]
                try:
                    future.result()
                except Exception as e:
                    xprint(f"[-] 处理 {vf.name} 时发生未知异常: {e}")

    xprint("[+] 全部任务处理完成。")

if __name__ == "__main__":
    main()
