from __future__ import annotations

import argparse
import sys
import traceback
import os

def _map_model_to_repo(size: str) -> str:
    normalized = size.strip().lower()
    mapping = {
        "large-v3": "Systran/faster-whisper-large-v3",
        "medium": "Systran/faster-whisper-medium",
        "small": "Systran/faster-whisper-small",
        "base": "Systran/faster-whisper-base",
        "tiny": "Systran/faster-whisper-tiny",
    }
    return mapping.get(normalized, f"Systran/faster-whisper-{normalized}")

def _default_local_dir(repo_id: str) -> str:
    tail = repo_id.split("/")[-1]
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "models", tail))

def download_faster_whisper_models(model_size: Optional[str] = None, proxy: Optional[str] = "127.0.0.1:7897", out_dir: Optional[str] = None) -> Dict[str, str]:
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except Exception:
        raise RuntimeError("未找到 huggingface_hub。请先安装：pip install huggingface_hub")

    sizes: List[str]
    if isinstance(model_size, str) and model_size.strip():
        if model_size.strip().lower() == "all":
            sizes = ["large-v3", "medium", "small", "base", "tiny"]
        else:
            sizes = [model_size.strip().lower()]
    else:
        sizes = ["large-v3", "medium", "small", "base", "tiny"]

    if proxy:
        url = proxy if proxy.startswith("http") else f"http://{proxy}"
        os.environ.setdefault("HTTP_PROXY", url)
        os.environ.setdefault("HTTPS_PROXY", url)

    base_dir = None
    if isinstance(out_dir, str) and out_dir.strip():
        base_dir = os.path.abspath(out_dir.strip())
    else:
        env_dir = os.environ.get("WHISPER_MODEL_DIR", "").strip()
        if env_dir:
            base_dir = os.path.abspath(env_dir)

    results: Dict[str, str] = {}
    for s in sizes:
        repo_id = _map_model_to_repo(s)
        head, tail = repo_id.split("/")
        target_dir = os.path.join(base_dir, head, tail) if base_dir else _default_local_dir(repo_id)
        print(f"下载模型 {repo_id} 到 {target_dir}")
        os.makedirs(target_dir, exist_ok=True)
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_dir,
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        results[s] = target_dir
    return results

def main() -> None:
    """命令行入口：下载 faster-whisper 模型到本地 models 目录。"""
    parser = argparse.ArgumentParser(
        description="下载 faster-whisper 模型（CTranslate2 格式）到本地。",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default="all",
        help="模型大小（large-v3/medium/small/base/tiny 或 all，默认 all）",
    )
    parser.add_argument(
        "--proxy",
        dest="proxy",
        type=str,
        default="127.0.0.1:7897",
        help="下载代理（默认 127.0.0.1:7897；不需要可传 none/空）",
    )
    parser.add_argument(
        "--out-dir",
        dest="out_dir",
        type=str,
        default=None,
        help="模型下载根目录（默认存于本模块 models/ 下；可覆盖）",
    )

    args = parser.parse_args()

    proxy = None if (args.proxy is None or str(args.proxy).lower() in {"", "none"}) else args.proxy
    print(f"准备下载模型: {args.model}")
    print(f"代理: {proxy or '未使用'}")
    print(f"输出目录: {args.out_dir or '默认（模块同级 models/）或 WHISPER_MODEL_DIR'}")
    print("-" * 30)

    try:
        results = download_faster_whisper_models(args.model, proxy=proxy, out_dir=args.out_dir)
    except RuntimeError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print(f"错误：下载失败: {e}", file=sys.stderr)
        sys.exit(1)

    print("下载完成，模型目录如下：")
    for size, path in results.items():
        print(f"  - {size}: {path}")


if __name__ == "__main__":
    main()