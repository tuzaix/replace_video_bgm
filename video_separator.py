#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频分离模块
功能：
1. 提取无声视频（移除音频轨道）
2. 使用spleeter分离人声和背景音乐
3. 支持多种视频格式
"""

import os
import sys
import shutil
import tempfile
import time
import traceback
import psutil
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# Spleeter功能已移除，使用Demucs替代

try:
    import demucs.separate
    import torch
    DEMUCS_AVAILABLE = True
    TORCH_AVAILABLE = True
except ImportError:
    DEMUCS_AVAILABLE = False
    TORCH_AVAILABLE = False
    print("警告: demucs库未安装，AI音频分离功能将不可用")
    print("请运行: pip install demucs")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('video_separator.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ErrorType(Enum):
    """错误类型枚举"""
    MEMORY_ERROR = "memory_error"
    GPU_ERROR = "gpu_error"
    CUDA_ERROR = "cuda_error"
    FFMPEG_ERROR = "ffmpeg_error"
    DEMUCS_ERROR = "demucs_error"
    FILE_ERROR = "file_error"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"

class ErrorHandler:
    """增强的错误处理器"""
    
    def __init__(self):
        self.error_history = []
        self.retry_counts = {}
        self.max_retries = 3
    
    def classify_error(self, error: Exception, context: str = "") -> ErrorType:
        """分类错误类型"""
        error_msg = str(error).lower()
        
        # 内存相关错误
        if any(keyword in error_msg for keyword in [
            'out of memory', 'cuda out of memory', 'memory', 'oom',
            'allocation', 'insufficient memory'
        ]):
            return ErrorType.MEMORY_ERROR
        
        # GPU/CUDA相关错误
        if any(keyword in error_msg for keyword in [
            'cuda', 'gpu', 'device', 'nvidia', 'cudnn', 'cublas'
        ]):
            if 'cuda' in error_msg:
                return ErrorType.CUDA_ERROR
            return ErrorType.GPU_ERROR
        
        # FFmpeg相关错误
        if any(keyword in error_msg for keyword in [
            'ffmpeg', 'codec', 'format', 'stream'
        ]):
            return ErrorType.FFMPEG_ERROR
        
        # Demucs相关错误
        if any(keyword in error_msg for keyword in [
            'demucs', 'model', 'separation'
        ]):
            return ErrorType.DEMUCS_ERROR
        
        # 文件相关错误
        if any(keyword in error_msg for keyword in [
            'file', 'path', 'directory', 'permission', 'not found'
        ]):
            return ErrorType.FILE_ERROR
        
        # 网络相关错误
        if any(keyword in error_msg for keyword in [
            'network', 'connection', 'timeout', 'download'
        ]):
            return ErrorType.NETWORK_ERROR
        
        return ErrorType.UNKNOWN_ERROR
    
    def get_recovery_strategy(self, error_type: ErrorType, context: Dict = None) -> Dict[str, Any]:
        """获取恢复策略"""
        context = context or {}
        
        strategies = {
            ErrorType.MEMORY_ERROR: {
                'actions': ['reduce_batch_size', 'clear_cache', 'use_cpu'],
                'params': {'batch_size': 1, 'segment_size': 2},
                'message': '内存不足，尝试减少批处理大小和清理缓存'
            },
            ErrorType.GPU_ERROR: {
                'actions': ['fallback_to_cpu', 'reset_gpu'],
                'params': {'use_gpu': False},
                'message': 'GPU错误，回退到CPU模式'
            },
            ErrorType.CUDA_ERROR: {
                'actions': ['clear_cuda_cache', 'reset_cuda_context', 'use_cpu'],
                'params': {'use_gpu': False},
                'message': 'CUDA错误，清理CUDA缓存并回退到CPU'
            },
            ErrorType.FFMPEG_ERROR: {
                'actions': ['check_ffmpeg', 'try_different_codec'],
                'params': {'codec': 'libx264'},
                'message': 'FFmpeg错误，检查编解码器设置'
            },
            ErrorType.DEMUCS_ERROR: {
                'actions': ['reload_model', 'use_different_model'],
                'params': {'model': 'htdemucs'},
                'message': 'Demucs模型错误，尝试重新加载或使用其他模型'
            },
            ErrorType.FILE_ERROR: {
                'actions': ['check_permissions', 'create_directory'],
                'params': {},
                'message': '文件访问错误，检查路径和权限'
            },
            ErrorType.NETWORK_ERROR: {
                'actions': ['retry_download', 'use_local_model'],
                'params': {'timeout': 60},
                'message': '网络错误，尝试重新下载或使用本地模型'
            },
            ErrorType.UNKNOWN_ERROR: {
                'actions': ['log_detailed_error', 'use_safe_mode'],
                'params': {},
                'message': '未知错误，使用安全模式'
            }
        }
        
        return strategies.get(error_type, strategies[ErrorType.UNKNOWN_ERROR])
    
    def should_retry(self, error_type: ErrorType, operation: str) -> bool:
        """判断是否应该重试"""
        key = f"{error_type.value}_{operation}"
        current_count = self.retry_counts.get(key, 0)
        
        # 某些错误类型不应该重试
        no_retry_errors = [ErrorType.FILE_ERROR, ErrorType.FFMPEG_ERROR]
        if error_type in no_retry_errors:
            return False
        
        return current_count < self.max_retries
    
    def record_retry(self, error_type: ErrorType, operation: str):
        """记录重试次数"""
        key = f"{error_type.value}_{operation}"
        self.retry_counts[key] = self.retry_counts.get(key, 0) + 1
    
    def log_error(self, error: Exception, context: str, error_type: ErrorType = None):
        """记录错误详情"""
        if error_type is None:
            error_type = self.classify_error(error, context)
        
        error_info = {
            'timestamp': time.time(),
            'error_type': error_type.value,
            'error_message': str(error),
            'context': context,
            'traceback': traceback.format_exc(),
            'system_info': self._get_system_info()
        }
        
        self.error_history.append(error_info)
        
        # 记录到日志
        logger.error(f"[{error_type.value}] {context}: {error}")
        logger.debug(f"详细错误信息: {error_info}")
    
    def _get_system_info(self) -> Dict[str, Any]:
        """获取系统信息"""
        try:
            return {
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': psutil.virtual_memory().percent,
                'disk_usage': psutil.disk_usage('/').percent if os.name != 'nt' else psutil.disk_usage('C:').percent,
                'gpu_available': TORCH_AVAILABLE and torch.cuda.is_available() if 'torch' in globals() else False
            }
        except Exception:
            return {'error': 'Failed to get system info'}

class VideoSeparator:
    """视频分离器类"""
    
    # 支持的视频文件扩展名
    SUPPORTED_VIDEO_EXTENSIONS = {
        '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', 
        '.webm', '.m4v', '.3gp', '.ts', '.mts', '.m2ts'
    }
    
    # 支持的音频文件扩展名
    SUPPORTED_AUDIO_EXTENSIONS = {
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'
    }
    
    def __init__(self, ffmpeg_path: Optional[str] = None, temp_dir: Optional[str] = None, enable_gpu_acceleration: bool = True):
        """
        初始化视频分离器
        
        Args:
            ffmpeg_path: FFmpeg可执行文件路径，如果为None则使用系统PATH中的ffmpeg
            temp_dir: 临时文件目录，如果为None则使用系统默认临时目录
            enable_gpu_acceleration: 是否启用GPU加速
        """
        self.ffmpeg_path = ffmpeg_path or 'ffmpeg'
        self.temp_dir = temp_dir or os.path.join(os.path.dirname(__file__), 'temp')
        self.enable_gpu_acceleration = enable_gpu_acceleration
        
        # 错误处理器
        self.error_handler = ErrorHandler()
        
        # 性能监控
        self.performance_stats = {
            'total_operations': 0,
            'successful_operations': 0,
            'gpu_operations': 0,
            'cpu_fallbacks': 0,
            'average_processing_time': 0.0
        }
        
        # 检查FFmpeg
        self._check_ffmpeg()
        
        # 确保临时目录存在
        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)
        
        # 检测GPU支持
        self._detect_gpu_support()
        
        # 设置GPU环境
        self._setup_gpu_environment()
    
    def _check_ffmpeg(self) -> None:
        """
        检查FFmpeg是否可用
        """
        try:
            result = subprocess.run(
                [self.ffmpeg_path, '-version'], 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg不可用: {result.stderr}")
            logger.info("FFmpeg检查通过")
        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg检查超时")
        except FileNotFoundError:
            raise RuntimeError(f"找不到FFmpeg: {self.ffmpeg_path}")
    
    def _detect_gpu_support(self) -> None:
        """检测GPU支持情况"""
        self.gpu_available = False
        self.device = "cpu"
        self.selected_gpu_id = 0
        self.gpu_details = {}
        
        if not self.enable_gpu_acceleration:
            print("GPU加速已禁用，将使用CPU处理")
            return
            
        if not TORCH_AVAILABLE:
            print("PyTorch未安装，无法使用GPU加速")
            return
            
        try:
            # 检查CUDA可用性
            if not torch.cuda.is_available():
                print("未检测到可用的CUDA GPU，将使用CPU")
                print(f"CUDA版本: {torch.version.cuda if torch.version.cuda else 'N/A'}")
                return
            
            # 获取CUDA和驱动信息
            cuda_version = torch.version.cuda
            gpu_count = torch.cuda.device_count()
            
            print(f"CUDA版本: {cuda_version}")
            print(f"检测到 {gpu_count} 个GPU设备")
            
            # 评估所有GPU并选择最佳的
            best_gpu_id = 0
            best_score = 0
            
            for gpu_id in range(gpu_count):
                props = torch.cuda.get_device_properties(gpu_id)
                gpu_name = props.name
                gpu_memory = props.total_memory / 1024**3
                compute_capability = f"{props.major}.{props.minor}"
                
                # 计算GPU评分（内存 + 计算能力权重）
                memory_score = gpu_memory
                compute_score = props.major * 10 + props.minor
                total_score = memory_score + compute_score * 0.5
                
                print(f"GPU {gpu_id}: {gpu_name}")
                print(f"  - 内存: {gpu_memory:.1f}GB")
                print(f"  - 计算能力: {compute_capability}")
                print(f"  - 多处理器数量: {props.multi_processor_count}")
                
                # 检查最低要求
                if gpu_memory >= 2.0 and props.major >= 3:  # 至少2GB内存和计算能力3.0
                    if total_score > best_score:
                        best_score = total_score
                        best_gpu_id = gpu_id
                        
                        self.gpu_details = {
                            'name': gpu_name,
                            'memory_gb': gpu_memory,
                            'compute_capability': compute_capability,
                            'multi_processor_count': props.multi_processor_count,
                            'score': total_score
                        }
                else:
                    print(f"  - 不满足最低要求（内存>=2GB，计算能力>=3.0）")
            
            # 设置最佳GPU
            if best_score > 0:
                self.gpu_available = True
                self.device = f"cuda:{best_gpu_id}"
                self.selected_gpu_id = best_gpu_id
                
                print(f"\n✅ 选择GPU {best_gpu_id}: {self.gpu_details['name']}")
                print(f"   内存: {self.gpu_details['memory_gb']:.1f}GB")
                print(f"   计算能力: {self.gpu_details['compute_capability']}")
                print("   将优先使用此GPU进行Demucs音频分离")
                
                # 设置当前GPU设备
                torch.cuda.set_device(best_gpu_id)
            else:
                print("\n❌ 没有找到满足要求的GPU，将使用CPU")
                
        except Exception as e:
            print(f"GPU检测失败: {e}，将使用CPU")
            import traceback
            print(f"详细错误信息: {traceback.format_exc()}")
    
    def _setup_gpu_environment(self) -> None:
        """设置GPU环境变量以优化内存使用和性能"""
        if self.gpu_available:
            # 设置PyTorch内存优化环境变量
            os.environ['PYTORCH_NO_CUDA_MEMORY_CACHING'] = '1'
            os.environ['CUDA_LAUNCH_BLOCKING'] = '1'
            
            # 设置CUDA内存管理优化
            os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:128'
            os.environ['CUDA_VISIBLE_DEVICES'] = str(self.selected_gpu_id)
            
            # 设置cuDNN优化
            os.environ['CUDNN_BENCHMARK'] = '1'
            os.environ['CUDNN_DETERMINISTIC'] = '0'
            
            # 设置内存碎片整理
            os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'garbage_collection_threshold:0.6,max_split_size_mb:128'
            
            print(f"已设置GPU内存优化环境变量 (GPU {self.selected_gpu_id})")
            
            # 如果PyTorch可用，设置运行时优化
            if TORCH_AVAILABLE:
                try:
                    # 启用内存池
                    torch.backends.cudnn.benchmark = True
                    torch.backends.cudnn.deterministic = False
                    
                    # 设置内存分配策略
                    torch.cuda.set_per_process_memory_fraction(0.9, self.selected_gpu_id)
                    
                    print("已设置PyTorch GPU运行时优化")
                except Exception as e:
                    logger.warning(f"设置PyTorch GPU优化失败: {e}")
    
    def get_gpu_info(self) -> Dict[str, Any]:
        """获取GPU状态信息"""
        info = {
            'gpu_available': self.gpu_available,
            'device': self.device,
            'enable_gpu_acceleration': self.enable_gpu_acceleration
        }
        
        if self.gpu_available and TORCH_AVAILABLE:
            try:
                info.update({
                    'gpu_count': torch.cuda.device_count(),
                    'gpu_name': torch.cuda.get_device_name(0),
                    'gpu_memory_total': f"{torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f}GB",
                    'gpu_memory_allocated': f"{torch.cuda.memory_allocated(0) / 1024**3:.1f}GB" if torch.cuda.is_available() else "N/A",
                    'gpu_memory_cached': f"{torch.cuda.memory_reserved(0) / 1024**3:.1f}GB" if torch.cuda.is_available() else "N/A"
                })
            except Exception as e:
                info['gpu_error'] = str(e)
        
        return info
    
    def _check_gpu_memory_available(self) -> float:
        """检查GPU可用内存（GB）"""
        if not self.gpu_available or not TORCH_AVAILABLE:
            return 0.0
        
        try:
            torch.cuda.empty_cache()  # 清理缓存
            total_memory = torch.cuda.get_device_properties(self.selected_gpu_id).total_memory / 1024**3
            allocated_memory = torch.cuda.memory_allocated(self.selected_gpu_id) / 1024**3
            cached_memory = torch.cuda.memory_reserved(self.selected_gpu_id) / 1024**3
            
            available_memory = total_memory - max(allocated_memory, cached_memory)
            return max(0.0, available_memory)
        except Exception as e:
            logger.warning(f"检查GPU内存失败: {e}")
            return 0.0
    
    def _update_performance_stats(self, operation_start: float) -> None:
        """更新性能统计"""
        elapsed_time = time.time() - operation_start
        total_ops = self.performance_stats['total_operations']
        current_avg = self.performance_stats['average_processing_time']
        
        # 计算新的平均处理时间
        new_avg = ((current_avg * (total_ops - 1)) + elapsed_time) / total_ops
        self.performance_stats['average_processing_time'] = new_avg
    
    def _calculate_optimal_segment_size(self) -> int:
        """根据GPU内存计算最优segment大小"""
        if not self.gpu_available:
            return 4  # CPU默认值
        
        try:
            available_memory = self._check_gpu_memory_available()
            
            # 根据可用内存动态调整segment大小
            if available_memory >= 8.0:
                return 8  # 高内存GPU
            elif available_memory >= 6.0:
                return 6  # 中等内存GPU
            elif available_memory >= 4.0:
                return 4  # 标准内存GPU
            elif available_memory >= 2.0:
                return 2  # 低内存GPU
            else:
                return 1  # 极低内存GPU
        except Exception:
            return 4  # 默认值
    
    def _is_memory_error(self, error_message: str) -> bool:
        """判断是否为内存相关错误"""
        memory_keywords = [
            "out of memory", "cuda out of memory", "memory", 
            "allocation", "insufficient", "oom"
        ]
        error_lower = error_message.lower()
        return any(keyword in error_lower for keyword in memory_keywords)
    
    def _try_gpu_with_reduced_params(self, original_cmd: list, output_dir: Path, 
                                   audio_file: Path, model: str) -> bool:
        """使用降级参数重试GPU处理"""
        reduced_params = [
            {'segment': '2', 'overlap': '0.1'},  # 第一次降级
            {'segment': '1', 'overlap': '0.05'}, # 第二次降级
        ]
        
        for i, params in enumerate(reduced_params):
            try:
                logger.info(f"尝试降级参数 {i+1}: segment={params['segment']}, overlap={params['overlap']}")
                
                cmd = [
                    'python', '-m', 'demucs.separate',
                    '-n', model,
                    '-d', self.device,
                    '--segment', params['segment'],
                    '--overlap', params['overlap'],
                    '-v',
                    '-o', str(output_dir),
                    str(audio_file)
                ]
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=3600
                )
                
                if result.returncode == 0:
                    logger.info(f"降级参数 {i+1} 成功")
                    return True
                else:
                    logger.warning(f"降级参数 {i+1} 失败: {result.stderr}")
                    
            except Exception as e:
                logger.warning(f"降级参数 {i+1} 异常: {e}")
        
        return False
    
    def benchmark_gpu_performance(self) -> Dict[str, Any]:
        """GPU性能基准测试"""
        if not self.gpu_available or not TORCH_AVAILABLE:
            return {'error': 'GPU不可用或PyTorch未安装'}
        
        benchmark_results = {
            'gpu_id': self.selected_gpu_id,
            'gpu_name': self.gpu_details.get('name', 'Unknown'),
            'tests': {}
        }
        
        try:
            # 测试1: 内存带宽测试
            logger.info("开始GPU内存带宽测试...")
            memory_bandwidth = self._test_memory_bandwidth()
            benchmark_results['tests']['memory_bandwidth'] = memory_bandwidth
            
            # 测试2: 计算性能测试
            logger.info("开始GPU计算性能测试...")
            compute_performance = self._test_compute_performance()
            benchmark_results['tests']['compute_performance'] = compute_performance
            
            # 测试3: 音频处理性能测试（如果有测试音频）
            logger.info("开始音频处理性能测试...")
            audio_performance = self._test_audio_processing_performance()
            benchmark_results['tests']['audio_processing'] = audio_performance
            
            # 计算综合评分
            overall_score = self._calculate_performance_score(benchmark_results['tests'])
            benchmark_results['overall_score'] = overall_score
            
            # 推荐最优参数
            recommended_params = self._recommend_optimal_params(benchmark_results)
            benchmark_results['recommended_params'] = recommended_params
            
            logger.info(f"GPU基准测试完成，综合评分: {overall_score:.2f}")
            
        except Exception as e:
            logger.error(f"GPU基准测试失败: {e}")
            benchmark_results['error'] = str(e)
        
        return benchmark_results
    
    def _test_memory_bandwidth(self) -> Dict[str, float]:
        """测试GPU内存带宽"""
        try:
            # 创建大型张量进行内存传输测试
            size = 100 * 1024 * 1024  # 100MB
            
            # CPU到GPU传输测试
            start_time = time.time()
            data_cpu = torch.randn(size, dtype=torch.float32)
            data_gpu = data_cpu.to(f'cuda:{self.selected_gpu_id}')
            torch.cuda.synchronize()
            cpu_to_gpu_time = time.time() - start_time
            
            # GPU到CPU传输测试
            start_time = time.time()
            data_back = data_gpu.to('cpu')
            torch.cuda.synchronize()
            gpu_to_cpu_time = time.time() - start_time
            
            # GPU内存复制测试
            start_time = time.time()
            data_copy = data_gpu.clone()
            torch.cuda.synchronize()
            gpu_copy_time = time.time() - start_time
            
            # 计算带宽 (MB/s)
            data_size_mb = size * 4 / (1024 * 1024)  # float32 = 4 bytes
            
            return {
                'cpu_to_gpu_bandwidth': data_size_mb / cpu_to_gpu_time,
                'gpu_to_cpu_bandwidth': data_size_mb / gpu_to_cpu_time,
                'gpu_copy_bandwidth': data_size_mb / gpu_copy_time
            }
        except Exception as e:
            logger.warning(f"内存带宽测试失败: {e}")
            return {'error': str(e)}
    
    def _test_compute_performance(self) -> Dict[str, float]:
        """测试GPU计算性能"""
        try:
            # 矩阵乘法测试
            size = 2048
            iterations = 10
            
            a = torch.randn(size, size, device=f'cuda:{self.selected_gpu_id}')
            b = torch.randn(size, size, device=f'cuda:{self.selected_gpu_id}')
            
            # 预热
            for _ in range(3):
                torch.matmul(a, b)
            torch.cuda.synchronize()
            
            # 正式测试
            start_time = time.time()
            for _ in range(iterations):
                c = torch.matmul(a, b)
            torch.cuda.synchronize()
            total_time = time.time() - start_time
            
            # 计算GFLOPS
            ops_per_matmul = 2 * size ** 3  # 矩阵乘法的浮点运算数
            total_ops = ops_per_matmul * iterations
            gflops = (total_ops / total_time) / 1e9
            
            return {
                'gflops': gflops,
                'avg_time_per_matmul': total_time / iterations
            }
        except Exception as e:
            logger.warning(f"计算性能测试失败: {e}")
            return {'error': str(e)}
    
    def _test_audio_processing_performance(self) -> Dict[str, Any]:
        """测试音频处理性能（模拟）"""
        try:
            # 创建模拟音频数据
            sample_rate = 44100
            duration = 10  # 10秒
            channels = 2
            
            # 生成测试音频张量
            audio_data = torch.randn(
                channels, sample_rate * duration, 
                device=f'cuda:{self.selected_gpu_id}'
            )
            
            # 模拟音频处理操作
            start_time = time.time()
            
            # FFT变换测试
            fft_result = torch.fft.fft(audio_data)
            
            # 滤波操作测试
            filtered = audio_data * 0.8
            
            # 卷积操作测试
            kernel = torch.randn(1, 1, 1024, device=f'cuda:{self.selected_gpu_id}')
            conv_result = torch.nn.functional.conv1d(
                audio_data.unsqueeze(0), kernel, padding=512
            )
            
            torch.cuda.synchronize()
            processing_time = time.time() - start_time
            
            # 计算处理速度
            audio_duration = duration
            processing_speed = audio_duration / processing_time
            
            return {
                'processing_speed_ratio': processing_speed,
                'processing_time': processing_time,
                'audio_duration': audio_duration
            }
        except Exception as e:
            logger.warning(f"音频处理性能测试失败: {e}")
            return {'error': str(e)}
    
    def _calculate_performance_score(self, test_results: Dict) -> float:
        """计算综合性能评分"""
        score = 0.0
        weight_sum = 0.0
        
        # 内存带宽评分 (权重: 0.3)
        if 'memory_bandwidth' in test_results and 'error' not in test_results['memory_bandwidth']:
            bandwidth = test_results['memory_bandwidth'].get('cpu_to_gpu_bandwidth', 0)
            bandwidth_score = min(bandwidth / 10000, 100)  # 10GB/s为满分
            score += bandwidth_score * 0.3
            weight_sum += 0.3
        
        # 计算性能评分 (权重: 0.4)
        if 'compute_performance' in test_results and 'error' not in test_results['compute_performance']:
            gflops = test_results['compute_performance'].get('gflops', 0)
            compute_score = min(gflops / 10, 100)  # 10 TFLOPS为满分
            score += compute_score * 0.4
            weight_sum += 0.4
        
        # 音频处理评分 (权重: 0.3)
        if 'audio_processing' in test_results and 'error' not in test_results['audio_processing']:
            speed_ratio = test_results['audio_processing'].get('processing_speed_ratio', 0)
            audio_score = min(speed_ratio * 10, 100)  # 10倍实时速度为满分
            score += audio_score * 0.3
            weight_sum += 0.3
        
        return score / weight_sum if weight_sum > 0 else 0.0
    
    def _recommend_optimal_params(self, benchmark_results: Dict) -> Dict[str, Any]:
        """根据基准测试结果推荐最优参数"""
        score = benchmark_results.get('overall_score', 0)
        
        if score >= 80:
            return {
                'segment_size': 8,
                'overlap': 0.25,
                'batch_size': 4,
                'memory_fraction': 0.9,
                'performance_level': 'high'
            }
        elif score >= 60:
            return {
                'segment_size': 6,
                'overlap': 0.2,
                'batch_size': 2,
                'memory_fraction': 0.8,
                'performance_level': 'medium'
            }
        elif score >= 40:
            return {
                'segment_size': 4,
                'overlap': 0.15,
                'batch_size': 1,
                'memory_fraction': 0.7,
                'performance_level': 'low'
            }
        else:
            return {
                'segment_size': 2,
                'overlap': 0.1,
                'batch_size': 1,
                'memory_fraction': 0.6,
                'performance_level': 'minimal'
             }
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计信息"""
        stats = self.performance_stats.copy()
        
        # 计算成功率
        if stats['total_operations'] > 0:
            stats['success_rate'] = stats['successful_operations'] / stats['total_operations']
            stats['gpu_usage_rate'] = stats['gpu_operations'] / stats['total_operations']
            stats['cpu_fallback_rate'] = stats['cpu_fallbacks'] / stats['total_operations']
        else:
            stats['success_rate'] = 0.0
            stats['gpu_usage_rate'] = 0.0
            stats['cpu_fallback_rate'] = 0.0
        
        return stats
    
    def get_error_summary(self) -> Dict[str, Any]:
        """获取错误摘要"""
        if not self.error_handler.error_history:
            return {'total_errors': 0, 'error_types': {}, 'recent_errors': []}
        
        error_types = {}
        for error_info in self.error_handler.error_history:
            error_type = error_info['error_type']
            error_types[error_type] = error_types.get(error_type, 0) + 1
        
        # 获取最近的5个错误
        recent_errors = self.error_handler.error_history[-5:] if len(self.error_handler.error_history) > 5 else self.error_handler.error_history
        
        return {
            'total_errors': len(self.error_handler.error_history),
            'error_types': error_types,
            'recent_errors': [
                {
                    'timestamp': error['timestamp'],
                    'type': error['error_type'],
                    'message': error['error_message'],
                    'context': error['context']
                }
                for error in recent_errors
            ]
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self.performance_stats = {
            'total_operations': 0,
            'successful_operations': 0,
            'gpu_operations': 0,
            'cpu_fallbacks': 0,
            'average_processing_time': 0.0
        }
        self.error_handler.error_history.clear()
        self.error_handler.retry_counts.clear()
        logger.info("性能统计和错误历史已重置")
    
    def extract_silent_video(self, input_path: str, output_path: str, 
                           preserve_quality: bool = True) -> bool:
        """
        提取无声视频（移除音频轨道）
        
        Args:
            input_path: 输入视频文件路径
            output_path: 输出无声视频文件路径
            preserve_quality: 是否保持原始视频质量，默认为True
            
        Returns:
            是否成功
        """
        try:
            input_path = Path(input_path)
            output_path = Path(output_path)
            
            # 检查输入文件
            if not input_path.exists():
                logger.error(f"输入文件不存在: {input_path}")
                return False
            
            if input_path.suffix.lower() not in self.SUPPORTED_VIDEO_EXTENSIONS:
                logger.error(f"不支持的视频格式: {input_path.suffix}")
                return False
            
            # 确保输出目录存在
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 构建FFmpeg命令
            cmd = [
                self.ffmpeg_path,
                '-i', str(input_path),
                '-an',  # 移除音频轨道
                '-y'    # 覆盖输出文件
            ]
            
            if preserve_quality:
                # 保持原始视频质量
                cmd.extend(['-c:v', 'copy'])
            else:
                # 重新编码（可能会压缩）
                cmd.extend(['-c:v', 'libx264', '-crf', '23'])
            
            cmd.append(str(output_path))
            
            logger.info(f"开始提取无声视频: {input_path.name}")
            start_time = time.time()
            
            # 执行FFmpeg命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=3600  # 1小时超时
            )
            
            if result.returncode != 0:
                logger.error(f"FFmpeg执行失败: {result.stderr}")
                return False
            
            # 检查输出文件
            if not output_path.exists() or output_path.stat().st_size == 0:
                logger.error(f"输出文件生成失败: {output_path}")
                return False
            
            elapsed_time = time.time() - start_time
            logger.info(f"无声视频提取完成: {output_path.name} (耗时: {elapsed_time:.2f}秒)")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg执行超时: {input_path}")
            return False
        except Exception as e:
            logger.error(f"提取无声视频异常: {input_path} - {str(e)}")
            return False
    
    # separate_audio_with_spleeter方法已移除，使用separate_audio_with_demucs替代
    
    def _extract_audio_from_video(self, video_path: str, audio_path: str) -> bool:
        """
        从视频文件中提取音频
        
        Args:
            video_path: 视频文件路径
            audio_path: 输出音频文件路径
            
        Returns:
            是否成功
        """
        try:
            cmd = [
                self.ffmpeg_path,
                '-i', video_path,
                '-vn',  # 不包含视频
                '-acodec', 'pcm_s16le',  # 使用PCM编码
                '-ar', '44100',  # 采样率
                '-ac', '2',  # 双声道
                '-y',  # 覆盖输出文件
                audio_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=1800  # 30分钟超时
            )
            
            return result.returncode == 0
            
        except Exception as e:
            logger.error(f"提取音频异常: {video_path} - {str(e)}")
            return False
    
    def separate_audio_with_demucs(self, input_path: str, output_dir: str,
                                 model: str = 'htdemucs') -> Dict[str, str]:
        """
        使用Demucs分离音频中的人声和背景音乐
        
        Args:
            input_path: 输入音频/视频文件路径
            output_dir: 输出目录
            model: Demucs模型类型 ('htdemucs', 'htdemucs_ft', 'mdx_extra')
            
        Returns:
            分离结果文件路径字典 {'vocals': 'path', 'other': 'path', 'bass': 'path', 'drums': 'path'}
        """
        if not DEMUCS_AVAILABLE:
            logger.error("Demucs不可用，无法进行音频分离")
            return {}
        
        try:
            input_path = Path(input_path)
            output_dir = Path(output_dir)
            
            # 检查输入文件
            if not input_path.exists():
                logger.error(f"输入文件不存在: {input_path}")
                return {}
            
            # 确保输出目录存在
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # 如果输入是视频文件，先提取音频
            audio_file = input_path
            temp_audio_file = None
            
            if input_path.suffix.lower() in self.SUPPORTED_VIDEO_EXTENSIONS:
                temp_audio_file = output_dir / f"{input_path.stem}_temp.wav"
                if not self._extract_audio_from_video(str(input_path), str(temp_audio_file)):
                    logger.error(f"从视频提取音频失败: {input_path}")
                    return {}
                audio_file = temp_audio_file
            
            logger.info(f"开始Demucs音频分离: {input_path.name}")
            start_time = time.time()
            
            # 使用Demucs进行分离
            demucs_output_dir = output_dir / "demucs_temp"
            demucs_output_dir.mkdir(exist_ok=True)
            
            # 智能设备选择和自适应处理
            success = False
            device_used = "cpu"
            
            # 首先尝试GPU（如果可用）
            if self.gpu_available:
                # 动态检查GPU内存状态
                gpu_memory_available = self._check_gpu_memory_available()
                
                if gpu_memory_available:
                    # 根据GPU内存动态调整参数
                    segment_size = self._calculate_optimal_segment_size()
                    
                    try:
                        logger.info(f"尝试使用GPU进行Demucs音频分离: {self.device}")
                        logger.info(f"GPU内存状态: {gpu_memory_available:.1f}GB可用")
                        logger.info(f"使用segment大小: {segment_size}")
                        
                        cmd = [
                            'python', '-m', 'demucs.separate',
                            '-n', model,
                            '-d', self.device,
                            '--segment', str(segment_size),
                            '--overlap', '0.25',  # 重叠率优化
                            '-v',
                            '-o', str(demucs_output_dir),
                            str(audio_file)
                        ]
                        
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='ignore',
                            timeout=3600  # 1小时超时
                        )
                        
                        if result.returncode == 0:
                            success = True
                            device_used = self.device
                            self.performance_stats['successful_operations'] += 1
                            self.performance_stats['total_operations'] += 1
                            self._update_performance_stats(start_time)
                            logger.info(f"GPU加速Demucs分离成功")
                        else:
                            logger.warning(f"GPU加速失败，错误信息: {result.stderr}")
                            # 分析失败原因并尝试降级处理
                            if self._is_memory_error(result.stderr):
                                logger.info("检测到GPU内存不足，尝试降级参数")
                                success = self._try_gpu_with_reduced_params(cmd, demucs_output_dir, audio_file, model)
                                if success:
                                    device_used = self.device
                                    self.performance_stats['successful_operations'] += 1
                                    self.performance_stats['total_operations'] += 1
                                    self._update_performance_stats(start_time)
                            
                    except Exception as e:
                        logger.warning(f"GPU加速异常: {e}，将回退到CPU")
                else:
                    logger.info("GPU内存不足，直接使用CPU模式")
            
            # 如果GPU失败或不可用，使用CPU
            if not success:
                try:
                    logger.info("使用CPU进行Demucs音频分离")
                    self.performance_stats['cpu_fallbacks'] += 1
                    
                    cmd = [
                        'python', '-m', 'demucs.separate',
                        '-n', model,
                        '-d', 'cpu',
                        '-v',
                        '-o', str(demucs_output_dir),
                        str(audio_file)
                    ]
                    
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='ignore',
                        timeout=7200  # CPU模式给更长时间：2小时
                    )
                    
                    if result.returncode == 0:
                        success = True
                        device_used = "cpu"
                        logger.info("CPU模式Demucs分离成功")
                        self.performance_stats['successful_operations'] += 1
                        self.performance_stats['total_operations'] += 1
                        self._update_performance_stats(start_time)
                    else:
                        error_msg = result.stderr
                        error_type = self.error_handler.classify_error(Exception(error_msg), "CPU音频分离")
                        self.error_handler.log_error(Exception(error_msg), "CPU音频分离", error_type)
                        logger.error(f"CPU模式Demucs分离也失败: {error_msg}")
                        
                except Exception as e:
                    error_type = self.error_handler.classify_error(e, "CPU音频分离")
                    self.error_handler.log_error(e, "CPU模式异常", error_type)
                    logger.error(f"CPU模式Demucs分离异常: {e}")
            
            if not success:
                logger.error("GPU和CPU模式都失败，无法完成音频分离")
                return {}
            
            # 查找分离结果文件
            result_files = {}
            model_output_dir = demucs_output_dir / model / audio_file.stem
            
            if model_output_dir.exists():
                # 移动并重命名文件
                for stem_file in model_output_dir.glob('*.wav'):
                    stem_name = stem_file.stem
                    new_name = f"{input_path.stem}_{stem_name}.wav"
                    new_path = output_dir / new_name
                    shutil.move(str(stem_file), str(new_path))
                    result_files[stem_name] = str(new_path)
            
            # 清理临时文件
            if temp_audio_file and temp_audio_file.exists():
                temp_audio_file.unlink()
            if demucs_output_dir.exists():
                shutil.rmtree(demucs_output_dir)
            
            elapsed_time = time.time() - start_time
            logger.info(f"Demucs音频分离完成: {input_path.name} (设备: {device_used}, 耗时: {elapsed_time:.2f}秒)")
            
            return result_files
            
        except Exception as e:
            logger.error(f"Demucs音频分离异常: {input_path} - {str(e)}")
            return {}
    
    def separate_video_complete(self, input_path: str, output_dir: str,
                              extract_silent: bool = True,
                              separate_audio: bool = True) -> Dict[str, Any]:
        """
        完整的视频分离处理
        
        Args:
            input_path: 输入视频文件路径
            output_dir: 输出目录
            extract_silent: 是否提取无声视频
            separate_audio: 是否分离音频
            
        Returns:
            处理结果字典
        """
        try:
            input_path = Path(input_path)
            output_dir = Path(output_dir)
            
            # 确保输出目录存在
            output_dir.mkdir(parents=True, exist_ok=True)
            
            results = {
                'input_file': str(input_path),
                'output_dir': str(output_dir),
                'silent_video': None,
                'audio_separation': {},
                'success': False
            }
            
            logger.info(f"开始完整视频分离处理: {input_path.name}")
            
            # 1. 提取无声视频
            if extract_silent:
                silent_video_path = output_dir / f"{input_path.stem}_silent{input_path.suffix}"
                if self.extract_silent_video(str(input_path), str(silent_video_path)):
                    results['silent_video'] = str(silent_video_path)
                    logger.info(f"无声视频已保存: {silent_video_path}")
            
            # 2. 分离音频 (使用Demucs)
            if separate_audio:
                audio_results = {}
                
                # 使用Demucs进行音频分离
                if DEMUCS_AVAILABLE:
                    logger.info("使用Demucs进行音频分离")
                    audio_results = self.separate_audio_with_demucs(str(input_path), str(output_dir))
                    if audio_results:
                        logger.info(f"Demucs音频分离完成，文件数: {len(audio_results)}")
                    else:
                        logger.warning("Demucs音频分离失败")
                else:
                    logger.warning("音频分离功能不可用: Demucs未安装")
                
                results['audio_separation'] = audio_results
            
            # 判断整体成功状态
            results['success'] = (
                (not extract_silent or results['silent_video'] is not None) and
                (not separate_audio or bool(results['audio_separation']))
            )
            
            if results['success']:
                logger.info(f"视频分离处理完成: {input_path.name}")
            else:
                logger.warning(f"视频分离处理部分失败: {input_path.name}")
            
            return results
            
        except Exception as e:
            logger.error(f"完整视频分离异常: {input_path} - {str(e)}")
            return {
                'input_file': str(input_path),
                'output_dir': str(output_dir),
                'silent_video': None,
                'audio_separation': {},
                'success': False,
                'error': str(e)
            }
    
    def batch_separate_videos(self, input_dir: str, output_dir: str,
                            max_workers: int = 2,
                            extract_silent: bool = True,
                            separate_audio: bool = True) -> Dict[str, Any]:
        """
        批量处理视频分离
        
        Args:
            input_dir: 输入目录
            output_dir: 输出目录
            max_workers: 最大并发数
            extract_silent: 是否提取无声视频
            separate_audio: 是否分离音频
            
        Returns:
            批量处理结果
        """
        try:
            input_dir = Path(input_dir)
            output_dir = Path(output_dir)
            
            if not input_dir.exists():
                logger.error(f"输入目录不存在: {input_dir}")
                return {'success': False, 'error': '输入目录不存在'}
            
            # 扫描视频文件
            video_files = []
            for ext in self.SUPPORTED_VIDEO_EXTENSIONS:
                video_files.extend(input_dir.glob(f"*{ext}"))
                video_files.extend(input_dir.glob(f"*{ext.upper()}"))
            
            if not video_files:
                logger.warning(f"在目录中未找到支持的视频文件: {input_dir}")
                return {'success': False, 'error': '未找到支持的视频文件'}
            
            logger.info(f"找到 {len(video_files)} 个视频文件，开始批量处理")
            
            results = {
                'total_files': len(video_files),
                'processed_files': [],
                'failed_files': [],
                'success': False
            }
            
            # 并发处理
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {}
                
                for video_file in video_files:
                    # 为每个视频创建单独的输出目录
                    video_output_dir = output_dir / video_file.stem
                    future = executor.submit(
                        self.separate_video_complete,
                        str(video_file),
                        str(video_output_dir),
                        extract_silent,
                        separate_audio
                    )
                    future_to_file[future] = video_file
                
                # 收集结果
                for future in as_completed(future_to_file):
                    video_file = future_to_file[future]
                    try:
                        result = future.result()
                        if result['success']:
                            results['processed_files'].append(result)
                        else:
                            results['failed_files'].append({
                                'file': str(video_file),
                                'error': result.get('error', '未知错误')
                            })
                    except Exception as e:
                        logger.error(f"处理文件异常: {video_file} - {str(e)}")
                        results['failed_files'].append({
                            'file': str(video_file),
                            'error': str(e)
                        })
            
            # 统计结果
            success_count = len(results['processed_files'])
            failed_count = len(results['failed_files'])
            
            results['success'] = success_count > 0
            
            logger.info(f"批量处理完成: 成功 {success_count} 个，失败 {failed_count} 个")
            
            return results
            
        except Exception as e:
            logger.error(f"批量处理异常: {str(e)}")
            return {'success': False, 'error': str(e)}


def main():
    """
    主函数 - 命令行接口
    """
    import argparse
    
    parser = argparse.ArgumentParser(description='视频分离工具')
    parser.add_argument('input', help='输入视频文件或目录')
    parser.add_argument('output', help='输出目录')
    parser.add_argument('--mode', choices=['silent', 'audio', 'both'], default='both',
                       help='处理模式: silent(无声视频), audio(音频分离), both(两者都做)')
    parser.add_argument('--batch', action='store_true', help='批量处理模式')
    parser.add_argument('--workers', type=int, default=1, help='并发数量')
    parser.add_argument('--ffmpeg-path', help='FFmpeg可执行文件路径')
    
    args = parser.parse_args()
    
    try:
        # 初始化分离器
        separator = VideoSeparator(ffmpeg_path=args.ffmpeg_path)
        
        extract_silent = args.mode in ['silent', 'both']
        separate_audio = args.mode in ['audio', 'both']
        
        if args.batch:
            # 批量处理
            result = separator.batch_separate_videos(
                args.input,
                args.output,
                max_workers=args.workers,
                extract_silent=extract_silent,
                separate_audio=separate_audio
            )
        else:
            # 单文件处理
            result = separator.separate_video_complete(
                args.input,
                args.output,
                extract_silent=extract_silent,
                separate_audio=separate_audio
            )
        
        if result['success']:
            print("处理完成!")
            sys.exit(0)
        else:
            print(f"处理失败: {result.get('error', '未知错误')}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"程序异常: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()