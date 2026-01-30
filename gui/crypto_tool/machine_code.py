import wmi
import hashlib
import subprocess
import json
from typing import Optional
from utils.common_utils import get_subprocess_silent_kwargs

def get_stable_hardware_id():
    """
    获取稳定的硬件信息组合，用于生成机器码。
    我们选择主板序列号和C盘卷标号，这两个最不容易改变。
    """
    try:
        c = wmi.WMI()

        def _normalize_token(s: str) -> str:
            return "".join(ch for ch in s.lower().strip() if ch.isalnum())

        INVALID_TOKENS = {
            "defaultstring",
            "tobefilledbyoem",
            "tobefilledbyo.e.m",
            "none",
            "na",
            "n/a",
            "unknown",
            "undef",
            "undefined",
            "notapplicable",
            "serialnumber",
            "systemserialnumber",
            "",
            "00000000",
            "0000000000000000",
        }

        def _is_invalid(value: Optional[str]) -> bool:
            if not value:
                return True
            norm = _normalize_token(str(value))
            return norm in INVALID_TOKENS

        def _get_board_serial() -> str:
            """尽量获取可靠的主板序列号，带多重回退。"""
            # Win32_BaseBoard.SerialNumber
            try:
                val = c.Win32_BaseBoard()[0].SerialNumber.strip()
                if not _is_invalid(val):
                    return val
            except Exception:
                pass

            # Win32_SystemEnclosure.SerialNumber (机箱序列号)
            try:
                enclosures = c.Win32_SystemEnclosure()
                if enclosures:
                    val = (enclosures[0].SerialNumber or "").strip()
                    if not _is_invalid(val):
                        return val
            except Exception:
                pass

            # Win32_BIOS.SerialNumber
            try:
                val = c.Win32_BIOS()[0].SerialNumber.strip()
                if not _is_invalid(val):
                    return val
            except Exception:
                pass

            # Win32_ComputerSystemProduct.UUID (通常较稳定)
            try:
                val = c.Win32_ComputerSystemProduct()[0].UUID.strip()
                if not _is_invalid(val):
                    return val
            except Exception:
                pass

            # 组合 Manufacturer + Model 作为降级方案
            try:
                cs = c.Win32_ComputerSystem()[0]
                man = (cs.Manufacturer or "").strip()
                mod = (cs.Model or "").strip()
                combo = f"{man}-{mod}".strip("-")
                if not _is_invalid(combo):
                    return combo
            except Exception:
                pass

            # BaseBoard Product + Version 作为最后的降级方案
            try:
                bb = c.Win32_BaseBoard()[0]
                prod = (bb.Product or "").strip()
                ver = (bb.Version or "").strip()
                combo = f"{prod}-{ver}".strip("-")
                if not _is_invalid(combo):
                    return combo
            except Exception:
                pass

            # 子进程调用 wmic 作为额外回退（某些设备下 WMI 对象字段为空）
            try:
                out = subprocess.check_output(
                    ["wmic", "baseboard", "get", "serialnumber"],
                    stderr=subprocess.STDOUT,
                    shell=True,
                    text=True,
                    **get_subprocess_silent_kwargs()
                )
                lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                # 过滤标题行，取最后一行可能是值
                if len(lines) >= 2:
                    val = lines[-1]
                    if not _is_invalid(val):
                        return val
            except Exception:
                pass

            # PowerShell CIM 作为额外回退
            try:
                ps = (
                    "Get-CimInstance Win32_BaseBoard | Select-Object -ExpandProperty SerialNumber"
                )
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", ps],
                    stderr=subprocess.STDOUT,
                    shell=True,
                    text=True,
                    **get_subprocess_silent_kwargs()
                ).strip()
                if out and not _is_invalid(out):
                    return out
            except Exception:
                pass

            return "UNKNOWN_BOARD"

        # 1. 获取主板序列号 (更稳健，带多重回退)
        board_serial = _get_board_serial()

        # 2. 获取系统盘 (C:) 的卷标序列号 (也很稳定)
        try:
            disk_serial = c.Win32_LogicalDisk(DeviceID="C:")[0].VolumeSerialNumber.strip()
        except Exception:
            disk_serial = "UNKNOWN_DISK"
            
        # 3. （备选）获取 CPU ID
        try:
            cpu_id = c.Win32_Processor()[0].ProcessorId.strip()
        except Exception:
            cpu_id = "UNKNOWN_CPU"
        
        # 组合并哈希
        # 注意：这里的组合方式决定了您机器码的稳定性
        # 推荐：主板 + C盘。如果主板坏了，用户才需要联系您重新激活。
        raw_id = f"{board_serial}-{disk_serial}-{cpu_id}"
        info = {
            "board_serial": board_serial,
            "disk_serial": disk_serial,
            "cpu_id": cpu_id,
        }
        # print(f"原始硬件ID组合: {raw_id}")
        # print(f"硬件信息: {info}")
        
        info_json = json.dumps(info, ensure_ascii=False)
        hashed_id = hashlib.sha256(info_json.encode('utf-8')).hexdigest()
        return hashed_id

    except Exception as e:
        print(f"获取硬件信息失败: {e}")
        # 在真实应用中，这里应该给用户一个更友好的错误提示
        return None

if __name__ == "__main__":
    machine_code = get_stable_hardware_id()
    if machine_code:
        print(f"您的机器码是: {machine_code}")
        print("请将此机器码发送给开发者以获取许可证。")
        # 在您的软件中，您可以将这个码显示在UI上让用户复制
    else:
        print("无法生成机器码。请检查系统权限或联系技术支持。")