#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
临时文件管理优化测试脚本
测试Demucs分离过程中临时文件的存储和清理功能
"""

import os
import sys
import time
import tempfile
import shutil
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from video_bgm_replacer import VideoBGMReplacer, SeparationConfig, SeparationStrategy


def test_temp_directory_creation():
    """测试临时目录创建"""
    print("🧪 测试1: 临时目录创建")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        video_dir = Path(temp_dir) / "videos"
        bgm_dir = Path(temp_dir) / "bgm"
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        replacer = None
        try:
            # 设置日志级别为ERROR以减少输出
            logging.getLogger().setLevel(logging.ERROR)
            
            replacer = VideoBGMReplacer(str(video_dir), str(bgm_dir))
            
            # 检查临时目录是否创建
            assert replacer.tmp_dir.exists(), "临时目录未创建"
            assert replacer.tmp_dir == video_dir / "tmp", "临时目录路径不正确"
            
            # 检查环境变量是否设置
            assert os.environ.get('TMPDIR') == str(replacer.tmp_dir), "TMPDIR环境变量未设置"
            assert os.environ.get('TEMP') == str(replacer.tmp_dir), "TEMP环境变量未设置"
            assert os.environ.get('TMP') == str(replacer.tmp_dir), "TMP环境变量未设置"
            
            print("✅ 临时目录创建测试通过")
            return True
            
        except Exception as e:
            print(f"❌ 临时目录创建测试失败: {e}")
            return False
        finally:
            # 确保清理资源
            if replacer:
                try:
                    # 关闭日志处理器
                    for handler in logging.getLogger().handlers[:]:
                        if hasattr(handler, 'close'):
                            handler.close()
                        logging.getLogger().removeHandler(handler)
                except:
                    pass


def test_temp_file_cleanup():
    """测试临时文件清理功能"""
    print("🧪 测试2: 临时文件清理功能")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        video_dir = Path(temp_dir) / "videos"
        bgm_dir = Path(temp_dir) / "bgm"
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        try:
            # 设置日志级别为ERROR以减少输出
            logging.getLogger().setLevel(logging.ERROR)
            
            replacer = VideoBGMReplacer(str(video_dir), str(bgm_dir))
            
            # 创建一些测试临时文件
            test_files = [
                replacer.tmp_dir / "test1.wav",
                replacer.tmp_dir / "test2.wav",
                replacer.tmp_dir / "test_separated.wav"
            ]
            
            for test_file in test_files:
                test_file.write_text("test content")
            
            # 创建测试子目录
            test_subdir = replacer.tmp_dir / "subdir"
            test_subdir.mkdir()
            (test_subdir / "subfile.wav").write_text("sub content")
            
            # 验证文件存在
            assert all(f.exists() for f in test_files), "测试文件创建失败"
            assert test_subdir.exists(), "测试子目录创建失败"
            
            # 执行清理
            replacer.cleanup_temp_files(keep_recent=False)
            
            # 验证文件已清理
            assert not any(f.exists() for f in test_files), "临时文件未清理"
            assert not test_subdir.exists(), "临时子目录未清理"
            
            print("✅ 临时文件清理测试通过")
            return True
            
        except Exception as e:
            print(f"❌ 临时文件清理测试失败: {e}")
            return False


def test_keep_recent_files():
    """测试保留最近文件功能"""
    print("🧪 测试3: 保留最近文件功能")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        video_dir = Path(temp_dir) / "videos"
        bgm_dir = Path(temp_dir) / "bgm"
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        try:
            # 设置日志级别为ERROR以减少输出
            logging.getLogger().setLevel(logging.ERROR)
            
            replacer = VideoBGMReplacer(str(video_dir), str(bgm_dir))
            
            # 创建新文件
            recent_file = replacer.tmp_dir / "recent.wav"
            recent_file.write_text("recent content")
            
            # 创建旧文件（模拟1小时前）
            old_file = replacer.tmp_dir / "old.wav"
            old_file.write_text("old content")
            
            # 修改旧文件的时间戳
            old_time = time.time() - 7200  # 2小时前
            os.utime(old_file, (old_time, old_time))
            
            # 执行保留最近文件的清理
            replacer.cleanup_temp_files(keep_recent=True)
            
            # 验证结果
            assert recent_file.exists(), "最近文件被错误删除"
            assert not old_file.exists(), "旧文件未被清理"
            
            print("✅ 保留最近文件测试通过")
            return True
            
        except Exception as e:
            print(f"❌ 保留最近文件测试失败: {e}")
            return False


def test_audio_separator_temp_usage():
    """测试音频分离器使用临时目录"""
    print("🧪 测试4: 音频分离器临时目录使用")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        video_dir = Path(temp_dir) / "videos"
        bgm_dir = Path(temp_dir) / "bgm"
        video_dir.mkdir()
        bgm_dir.mkdir()
        
        try:
            # 设置日志级别为ERROR以减少输出
            logging.getLogger().setLevel(logging.ERROR)
            
            replacer = VideoBGMReplacer(str(video_dir), str(bgm_dir))
            
            # 创建模拟音频文件
            test_audio = video_dir / "test.wav"
            test_audio.write_text("fake audio content")
            
            # 模拟separate_audio方法调用
            with patch.object(replacer.audio_separator, 'separate_audio') as mock_separate:
                # 设置模拟返回值
                expected_output = replacer.tmp_dir / "test_separated.wav"
                mock_separate.return_value = (expected_output, None)
                
                # 调用方法
                result_path, _ = replacer.audio_separator.separate_audio(test_audio, replacer.tmp_dir)
                
                # 验证调用参数
                mock_separate.assert_called_once_with(test_audio, replacer.tmp_dir)
                
                # 验证返回的路径在临时目录中
                assert str(result_path).startswith(str(replacer.tmp_dir)), "分离音频未保存到临时目录"
            
            print("✅ 音频分离器临时目录使用测试通过")
            return True
            
        except Exception as e:
            print(f"❌ 音频分离器临时目录使用测试失败: {e}")
            return False


def test_environment_variables():
    """测试环境变量设置"""
    print("🧪 测试5: 环境变量设置")
    
    # 保存原始环境变量
    original_env = {
        'TMPDIR': os.environ.get('TMPDIR'),
        'TEMP': os.environ.get('TEMP'),
        'TMP': os.environ.get('TMP')
    }
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_dir = Path(temp_dir) / "videos"
            bgm_dir = Path(temp_dir) / "bgm"
            video_dir.mkdir()
            bgm_dir.mkdir()
            
            # 设置日志级别为ERROR以减少输出
            logging.getLogger().setLevel(logging.ERROR)
            
            replacer = VideoBGMReplacer(str(video_dir), str(bgm_dir))
            
            # 验证环境变量设置
            expected_tmp_dir = str(replacer.tmp_dir)
            assert os.environ.get('TMPDIR') == expected_tmp_dir, "TMPDIR环境变量设置错误"
            assert os.environ.get('TEMP') == expected_tmp_dir, "TEMP环境变量设置错误"
            assert os.environ.get('TMP') == expected_tmp_dir, "TMP环境变量设置错误"
            
            print("✅ 环境变量设置测试通过")
            return True
            
    except Exception as e:
        print(f"❌ 环境变量设置测试失败: {e}")
        return False
    finally:
        # 恢复原始环境变量
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_all_tests():
    """运行所有测试"""
    print("🚀 开始临时文件管理优化测试")
    print("=" * 60)
    
    tests = [
        test_temp_directory_creation,
        test_temp_file_cleanup,
        test_keep_recent_files,
        test_audio_separator_temp_usage,
        test_environment_variables
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            print()
        except Exception as e:
            print(f"❌ 测试执行异常: {e}")
            print()
    
    print("=" * 60)
    print(f"📊 测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 所有测试通过！临时文件管理优化成功")
    else:
        print(f"⚠️ {total - passed} 个测试失败，需要进一步检查")
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)