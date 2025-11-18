# 调试用
# 打印调试信息
def xprint(*values: object, isDebug: bool = False) -> dict:
    if isDebug:
        print(*values)
    pass

__all__ = [
    "xprint",
]