"""
License verification tool with CLI support.

This module verifies a license.dat file by:
- Reading JSON license data and signature separated by a known delimiter.
- Verifying the RSA PKCS#1 v1.5 signature (SHA-256) with a public key.
- Validating hardware binding (machine code) and expiry time.
- Optionally performing anti-time rollback checks via a timestamp file.

Use the CLI to configure paths and options instead of hardcoding values.
"""

import json
import wmi  # 用于实时获取硬件码
import hashlib  # 用于实时获取硬件码
import datetime
import argparse
import sys
import os
from pathlib import Path

# Prefer the same machine code routine used by the GUI to avoid mismatches.
try:
    from . import machine_code as _mc
except Exception:
    _mc = None

try:
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from Crypto.Hash import SHA256
    _pycrypto_available = True
except Exception:
    RSA = None  # type: ignore
    pkcs1_15 = None  # type: ignore
    SHA256 = None  # type: ignore
    _pycrypto_available = False

# === 关键：将您的公钥硬编码到代码中 ===
# (这是 public.pem 文件的内容)
PUBLIC_KEY_PEM = """
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAy... (这里是您的公钥全部内容)
...
...
-----END PUBLIC KEY-----
"""

def _runtime_base_dir() -> Path:
    """返回运行时资源的基准目录。

    - 冻结（PyInstaller）模式：优先使用 sys._MEIPASS；若没有则使用 exe 所在目录。
    - 开发模式：项目根目录（从本文件向上两级）。
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent.parent


def _resource_path(relpath: str) -> Path:
    """将项目内的相对路径（如 "gui/secretkey/public.pem"）转换为运行时的绝对路径。"""
    return _runtime_base_dir() / relpath.replace("/", os.sep)

def _require_pycrypto() -> None:
    """Ensure PyCryptodome is available."""
    if not _pycrypto_available:
        raise RuntimeError("PyCryptodome is not installed. Please run: pip install pycryptodome")


def get_current_machine_code() -> str | None:
    """Get current machine code using the project's stable routine.

    This function first tries to use crypto_tool.machine_code.get_stable_hardware_id()
    to ensure consistency between license issuance and verification. If that fails,
    it falls back to a simple WMI-based approach.

    Returns
    -------
    str | None
        Machine fingerprint string, or None on failure.
    """
    # Preferred: robust multi-source routine
    try:
        if _mc and hasattr(_mc, "get_stable_hardware_id"):
            code = _mc.get_stable_hardware_id()
            if code:
                return code
    except Exception:
        pass
    # Fallback: minimal WMI composition
    try:
        c = wmi.WMI()
        board_serial = c.Win32_BaseBoard()[0].SerialNumber.strip()
        disk_serial = c.Win32_LogicalDisk(DeviceID="C:")[0].VolumeSerialNumber.strip()
        cpu_id = c.Win32_Processor()[0].ProcessorId.strip()
        raw_id = f"{board_serial}-{disk_serial}-{cpu_id}"
        return hashlib.sha256(raw_id.encode('utf-8')).hexdigest()
    except Exception:
        return None

# 用于防范用户回调系统时间
def check_tamper_proof_time(license_expiry_time: datetime.datetime,
                            timestamp_file: str = "last_run.dat",
                            allowed_skew: datetime.timedelta = datetime.timedelta(minutes=5)) -> bool:
    """(高级功能) 检查并更新防篡改时间戳。

    Parameters
    ----------
    license_expiry_time : datetime.datetime
        License expiry time (UTC). If current time is beyond this, license invalid.
    timestamp_file : str
        Path to a timestamp file used to detect time rollback.
    allowed_skew : datetime.timedelta
        Allowed system clock skew when comparing current and last run times.
    """
    current_time = datetime.datetime.now(datetime.timezone.utc)

    # 1. 检查是否已过期 (常规检查)
    if current_time > license_expiry_time:
        print("许可证已过期。")
        return False

    # 2. 检查时间回调
    last_run_time = None
    try:
        with open(timestamp_file, "r") as f:
            # 在真实应用中，应对此文件内容进行简单加密
            last_run_time_str = f.read()
            last_run_time = datetime.datetime.fromisoformat(last_run_time_str)
    except FileNotFoundError:
        pass  # 首次运行，忽略
    except Exception:
        pass  # 文件损坏，当做首次运行

    if last_run_time and (current_time < (last_run_time - allowed_skew)):
        print(f"检测到系统时间回调！当前时间: {current_time}, 上次运行时间: {last_run_time}")
        return False

    # 3. 更新时间戳
    try:
        # 写入当前时间，用于下次启动时检查
        with open(timestamp_file, "w") as f:
            f.write(current_time.isoformat())
    except Exception:
        pass  # 写入失败 (例如权限问题)，忽略此次写入

    return True


def _load_public_key(pub_key_path: Path | None) -> object:
    """加载用于许可证验证的 RSA 公钥。

    优先级：
    1) 如果提供了 pub_key_path，直接使用该路径。
    2) 使用打包在 exe 内且相对路径保持不变的文件：gui/secretkey/public.pem。
    3) 回退到内嵌的 PUBLIC_KEY_PEM 常量。
    """
    _require_pycrypto()
    if pub_key_path and pub_key_path.exists():
        try:
            return RSA.import_key(pub_key_path.read_bytes())
        except Exception as e:
            raise RuntimeError(f"无法加载公钥文件 '{pub_key_path}': {e}")
    # 默认位置：打包资源中的 gui/secretkey/public.pem（保持相对路径不变）
    default_pub = _resource_path("gui/secretkey/public.pem")
    if default_pub.exists():
        try:
            return RSA.import_key(default_pub.read_bytes())
        except Exception as e:
            # 若默认路径读取失败（例如权限问题），回退到内嵌常量
            try:
                return RSA.import_key(PUBLIC_KEY_PEM)
            except Exception:
                raise RuntimeError(f"无法加载默认公钥 '{default_pub}': {e}")
    # 若默认路径不存在或不可用，则尝试内嵌常量
    try:
        return RSA.import_key(PUBLIC_KEY_PEM)
    except Exception:
        raise RuntimeError("未提供公钥路径且内置公钥无效，请使用 --pub 指定 public.pem")


def _read_license_file(license_path: Path) -> tuple[bytes, bytes]:
    """Read and split license file into (data_bytes, signature)."""
    SEPARATOR = b"---SIGNATURE_BELOW---"
    try:
        content = license_path.read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError(f"未找到许可证文件 '{license_path}'.")
    parts = content.split(b"\n" + SEPARATOR + b"\n")
    if len(parts) != 2:
        raise ValueError("许可证文件格式错误")
    return parts[0], parts[1]


def verify_license(license_path: Path | None = None,
                   pub_key_path: Path | None = None,
                   timestamp_file: Path | None = None,
                   allow_skew_minutes: int = 60,
                   machine_code_override: str | None = None,
                   print_details: bool = False,
                   debug: bool = False) -> bool:
    """Verify license with configurable inputs.

    Parameters
    ----------
    license_path : Path | None
        Path to license.dat; defaults to gui/license.dat.
    pub_key_path : Path | None
        Path to public.pem; if None, try built-in constant or default.
    timestamp_file : Path | None
        Path to anti-rollback timestamp file; defaults to 'last_run.dat'.
    allow_skew_minutes : int
        Allowed system clock skew in minutes (default 5).
    machine_code_override : str | None
        Override current machine code (for offline verification or tests).
    print_details : bool
        Print license JSON details if verification succeeds.
    debug : bool
        Print detailed errors (traceback) on failure.
    """
    try:
        _require_pycrypto()
        # Resolve defaults
        if license_path is None:
            license_path = Path(__file__).resolve().parent.parent / "license.dat"
        pub_key = _load_public_key(pub_key_path)

        # Read and split
        data_bytes, signature = _read_license_file(license_path)

        # Signature verification
        hash_obj = SHA256.new(data_bytes)
        verifier = pkcs1_15.new(pub_key)
        try:
            verifier.verify(hash_obj, signature)
        except (ValueError, TypeError):
            print("许可证签名无效！文件可能已被篡改。")
            return False

        # Parse JSON
        license_data = json.loads(data_bytes.decode('utf-8'))

        # Hardware binding
        current_code = machine_code_override or get_current_machine_code()
        license_code = license_data.get("hardware_id")
        if not current_code or current_code != license_code:
            print(f"硬件不匹配！许可证用于: {license_code}, 当前机器: {current_code}")
            return False

        # Time checks
        expires_at_str = license_data.get("expires_at")
        expires_at_time = datetime.datetime.fromisoformat(expires_at_str)
        ts_file = str(timestamp_file) if timestamp_file else "last_run.dat"
        skew = datetime.timedelta(minutes=max(0, int(allow_skew_minutes)))
        if not check_tamper_proof_time(expires_at_time, timestamp_file=ts_file, allowed_skew=skew):
            print("时间验证失败 (可能已过期或系统时间被回调)")
            return False

        # Optional details
        if print_details:
            print("--- 许可证数据 ---")
            try:
                # 格式化输出
                print(json.dumps(license_data, ensure_ascii=False, indent=2))
            except Exception:
                print(license_data)

        print("--- 许可证有效！---")
        print(f"欢迎使用，授权将于 {expires_at_str} 到期。")
        return True
    except FileNotFoundError as e:
        print(str(e))
        return False
    except Exception as e:
        print(f"许可证验证失败，发生未知错误: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return False

def main(argv: list[str] | None = None) -> int:
    """Command-line interface for license verification.

    Parameters
    ----------
    argv : list[str] | None
        Command-line arguments list; defaults to sys.argv[1:].

    Returns
    -------
    int
        Exit code (0 on success, non-zero on failure).
    """
    parser = argparse.ArgumentParser(
        prog="verify_license",
        description="Verify license.dat using RSA public key and machine binding",
    )
    parser.add_argument("--license", type=str, help="Path to license.dat")
    parser.add_argument("--pub", type=str, help="Path to public.pem (PEM)")
    parser.add_argument("--machine", type=str, help="Override machine code (for tests)")
    parser.add_argument("--timestamp-file", type=str, help="Path to anti-rollback timestamp file")
    parser.add_argument("--skew", type=int, default=5, help="Allowed system clock skew in minutes (default 5)")
    parser.add_argument("--allow-skew", type=int, default=5, help="Allowed clock skew in minutes (default 5)")
    parser.add_argument("--print-details", action="store_true", help="Print license JSON after verification")
    parser.add_argument("--debug", action="store_true", help="Show detailed errors and traceback")

    args = parser.parse_args(argv)

    lic = Path(args.license) if args.license else None
    pub = Path(args.pub) if args.pub else None
    tsf = Path(args.timestamp_file) if args.timestamp_file else None

    ok = verify_license(
        license_path=lic,
        pub_key_path=pub,
        timestamp_file=tsf,
        allow_skew_minutes=args.allow_skew,
        machine_code_override=args.machine,
        print_details=args.print_details,
        debug=args.debug,
    )

    if ok:
        print("软件正在运行...")
        return 0
    else:
        print("激活失败，软件将退出。")
        return 1


if __name__ == "__main__":
    sys.exit(main())