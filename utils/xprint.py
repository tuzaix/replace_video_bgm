
import os
# 调试用
# 打印调试信息
def xprint(*values: object) -> dict:
    
    # 在系统上设置 DEBUG=1 来启用调试打印，默认不打印，
    # 例如：export DEBUG=1 或 set DEBUG=1
    isDebug = os.getenv("DEBUG", "0") == "1"
    if isDebug:
        print(*values)
    pass

__all__ = [
    "xprint",
]