"""
HypoMux UI 页面包（第三阶段 Fluent 换装）

包含四个子页面：
- home_page     首页数据看板
- routing_page  路由规则
- tools_page    网络体检
- settings_page 系统设置

以及一个跨版本安全的 FluentIcon 解析器 resolve_icon()，
避免不同 qfluentwidgets 版本缺失某个图标枚举导致整个程序崩溃。
"""

from qfluentwidgets import FluentIcon


def resolve_icon(*names):
    """按优先级返回第一个在当前 qfluentwidgets 版本中存在的 FluentIcon。

    用法: resolve_icon("GLOBAL", "GLOBE", "IOT") —— 依次尝试，
    全部缺失时回退到 FluentIcon.APPLICATION（必然存在），保证不抛异常。
    """
    for name in names:
        icon = getattr(FluentIcon, name, None)
        if icon is not None:
            return icon
    return getattr(FluentIcon, "APPLICATION", getattr(FluentIcon, "TILES", None))


__all__ = ["resolve_icon"]
