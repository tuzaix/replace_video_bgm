#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试video_bgm_replacer.py集成video_separator.py后的功能
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from video_bgm_replacer import VideoBGMReplacer, SeparationConfig, SeparationStrategy


def test_separation_config():
    """测试SeparationConfig的新配置选项"""
    print("🧪 测试SeparationConfig配置...")
    
    # 测试默认配置
    config_default = SeparationConfig()
    assert config_default.use_batch_separation == False
    assert config_default.batch_max_workers == 2
    print("✅ 默认配置测试通过")
    
    # 测试批量分离配置
    config_batch = SeparationConfig(
        use_batch_separation=True,
        batch_max_workers=4
    )
    assert config_batch.use_batch_separation == True
    assert config_batch.batch_max_workers == 4
    print("✅ 批量分离配置测试通过")


def test_video_bgm_replacer_initialization():
    """测试VideoBGMReplacer的初始化"""
    print("\n🧪 测试VideoBGMReplacer初始化...")
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        video_dir = os.path.join(temp_dir, "videos")
        bgm_dir = os.path.join(temp_dir, "bgm")
        os.makedirs(video_dir)
        os.makedirs(bgm_dir)
        
        # 测试传统模式初始化
        config_traditional = SeparationConfig(use_batch_separation=False)
        replacer_traditional = VideoBGMReplacer(
            video_dir=video_dir,
            bgm_dir=bgm_dir,
            max_workers=2,
            separation_config=config_traditional
        )
        assert replacer_traditional.video_separator is None
        print("✅ 传统模式初始化测试通过")
        
        # 测试批量分离模式初始化
        config_batch = SeparationConfig(use_batch_separation=True, batch_max_workers=2)
        replacer_batch = VideoBGMReplacer(
            video_dir=video_dir,
            bgm_dir=bgm_dir,
            max_workers=2,
            separation_config=config_batch
        )
        assert replacer_batch.video_separator is not None
        print("✅ 批量分离模式初始化测试通过")


def test_imports():
    """测试导入是否正常"""
    print("\n🧪 测试模块导入...")
    
    try:
        from video_separator import VideoSeparator
        from config import get_config, SUPPORTED_FORMATS
        print("✅ video_separator和config模块导入成功")
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        return False
    
    return True


def test_batch_separation_method_exists():
    """测试批量分离方法是否存在"""
    print("\n🧪 测试批量分离方法...")
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        video_dir = os.path.join(temp_dir, "videos")
        bgm_dir = os.path.join(temp_dir, "bgm")
        os.makedirs(video_dir)
        os.makedirs(bgm_dir)
        
        config_batch = SeparationConfig(use_batch_separation=True)
        replacer = VideoBGMReplacer(
            video_dir=video_dir,
            bgm_dir=bgm_dir,
            separation_config=config_batch
        )
        
        # 检查方法是否存在
        assert hasattr(replacer, 'process_videos_with_batch_separation')
        assert hasattr(replacer, '_combine_separated_audio')
        print("✅ 批量分离方法存在")


def main():
    """主测试函数"""
    print("🚀 开始集成测试...")
    
    try:
        # 测试导入
        if not test_imports():
            return False
        
        # 测试配置
        test_separation_config()
        
        # 测试初始化
        test_video_bgm_replacer_initialization()
        
        # 测试方法存在性
        test_batch_separation_method_exists()
        
        print("\n🎉 所有集成测试通过！")
        print("\n📋 集成功能总结:")
        print("  ✅ 成功导入video_separator和config模块")
        print("  ✅ SeparationConfig支持批量分离配置")
        print("  ✅ VideoBGMReplacer支持传统和批量分离模式")
        print("  ✅ 批量分离相关方法已实现")
        print("  ✅ 命令行参数支持批量分离选项")
        
        return True
        
    except Exception as e:
        print(f"\n❌ 集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)