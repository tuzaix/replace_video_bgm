import os
import argparse
from typing import List, Optional
from huggingface_hub import snapshot_download


def _apply_proxy(proxy: Optional[str]) -> None:
    """Configure HTTP(S) proxy via environment variables.

    Parameters
    ----------
    proxy : Optional[str]
        Proxy URL like "http://127.0.0.1:7897". If None, proxies are unset.
    """
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY"):
            if k in os.environ:
                os.environ.pop(k, None)


def _apply_mirror(use_mirror: bool) -> None:
    """Configure Hugging Face endpoint mirror.

    Parameters
    ----------
    use_mirror : bool
        When True, sets HF_ENDPOINT to a mirror for faster/better connectivity.
    """
    if use_mirror:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    else:
        if "HF_ENDPOINT" in os.environ:
            os.environ.pop("HF_ENDPOINT", None)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for model download."""
    p = argparse.ArgumentParser(description="Florence-2 model downloader (with proxy & mirror support)")
    p.add_argument("--repo-id", default="microsoft/Florence-2-large", help="Model repository id on Hugging Face")
    p.add_argument("--local-dir", default=os.path.join(".", "models", "Florence-2-large"), help="Local directory to save the model")
    p.add_argument("--use-mirror", action="store_true", help="Use HF mirror endpoint (hf-mirror.com)")
    p.add_argument("--no-proxy", action="store_true", help="Disable HTTP(S) proxy even if default is set")
    p.add_argument("--proxy", default="http://127.0.0.1:7897", help="Proxy URL, e.g., http://127.0.0.1:7897")
    p.add_argument("--token", default=None, help="Hugging Face access token if needed")
    p.add_argument(
        "--ignore-patterns",
        nargs="*",
        default=["*.msgpack", "*.h5", ".gitattributes", "README.md"],
        help="Patterns to ignore during snapshot download",
    )
    p.add_argument(
        "--allow-patterns",
        nargs="*",
        default=[
            "*.json",
            "*.bin",
            "*.safetensors",
            "*.py",
            "tokenizer.json",
            "vocab.json",
            "merges.txt",
            "preprocessor_config.json",
            "processor_config.json",
            "special_tokens_map.json",
        ],
        help="Patterns to allow for minimal inference files",
    )
    p.add_argument("--full", action="store_true", help="Download full repository without allow-patterns restriction")
    p.add_argument("--symlinks", action="store_true", help="Use symlinks in local_dir instead of real files")
    p.add_argument("--force", action="store_true", help="Force re-download even if cached")
    p.add_argument("--resume", action="store_true", help="Resume partial downloads when available")
    return p.parse_args()


def download_model(repo_id: str, local_dir: str, token: Optional[str], ignore_patterns: List[str], allow_patterns: Optional[List[str]], use_symlinks: bool, force: bool, resume: bool) -> None:
    """Download a model repository snapshot.

    Parameters
    ----------
    repo_id : str
        Hugging Face repository id.
    local_dir : str
        Local directory to save model files.
    token : Optional[str]
        Access token for private repos.
    ignore_patterns : List[str]
        Glob-like patterns to ignore.
    use_symlinks : bool
        Whether to use symlinks in local_dir.
    force : bool
        Force re-download.
    resume : bool
        Resume partial downloads.
    """
    os.makedirs(local_dir, exist_ok=True)
    print(f"Starting download: {repo_id}")
    print(f"Saving to: {os.path.abspath(local_dir)}")
    print(f"Ignore patterns: {ignore_patterns}")
    snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=use_symlinks,
        resume_download=resume,
        force_download=force,
        ignore_patterns=ignore_patterns,
        allow_patterns=allow_patterns,
        token=token,
    )
    print("Download completed.")


def main() -> None:
    """Entry point for Florence-2 model downloader CLI."""
    args = _parse_args()
    _apply_mirror(bool(args.use_mirror))
    proxy = None if bool(args.no_proxy) else str(args.proxy or "")
    _apply_proxy(proxy)
    try:
        download_model(
            repo_id=str(args.repo_id),
            local_dir=str(args.local_dir),
            token=str(args.token) if args.token else None,
            ignore_patterns=list(args.ignore_patterns or []),
            allow_patterns=None if bool(args.full) else list(args.allow_patterns or []),
            use_symlinks=bool(args.symlinks),
            force=bool(args.force),
            resume=bool(args.resume),
        )
    except Exception as e:
        print(f"Failed: {e}")


if __name__ == "__main__":
    main()
