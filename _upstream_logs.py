# -*- coding: utf-8 -*-
"""静默 mijiaAPI 的原文日志，保留插件自身的 AstrBot 日志与 worker 进度。"""

import sys


class _SilentUpstreamLogger:
    """上游各级消息都可能包含认证数据、设备标识或控制值，不转发原文。"""

    @staticmethod
    def _discard(*_args, **_kwargs) -> None:
        pass

    debug = info = warning = error = exception = critical = log = _discard


def silence_mijiaapi_logs() -> None:
    """在导入固定版本 4.1.3 后、创建任何 API 实例前替换其模块日志入口。

    apis 与 devices 通过 from-import 各自持有日志对象，因此必须分别替换；
    同时替换上游导出的对象，供后续导入复用。无需导入或配置标准库日志，
    也不改变 AstrBot 或其他依赖的日志级别、处理器和输出流。
    """

    silent = _SilentUpstreamLogger()
    for name in ("mijiaAPI.logger", "mijiaAPI.apis", "mijiaAPI.devices"):
        module = sys.modules.get(name)
        if module is not None:
            module.logger = silent
