# -*- coding: utf-8 -*-
"""原子写工具

先写同目录临时文件再 os.replace 顶替目标：断电/崩溃时目标文件要么是旧版本、
要么是新版本，不会出现半截 JSON。Windows 下 os.replace 在目标被其他进程占用时
会抛 PermissionError，此时回退为直接写（宁可降级也不丢功能），并记 warning。
"""
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPLACE_ATTEMPTS = 3     # Windows 上目标被杀软/备份进程瞬时占用时 os.replace 抛 WinError 5
_REPLACE_BACKOFF = (0.01, 0.05)


def write_text_atomic(path, text: str, *, encoding: str = "utf-8") -> bool:
    """原子写文本。返回 True=原子路径成功，False=回退直写。IOError 不吞。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if attempt >= _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF[min(attempt,
                                                len(_REPLACE_BACKOFF) - 1)])
        return False
    except OSError as e:
        logger.warning("原子写失败，回退直写 %s: %s", path, e)
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        path.write_text(text, encoding=encoding, newline="\n")
        return False


def write_json_atomic(path, data: Any, *, indent: int = 2,
                      ensure_ascii: bool = False) -> bool:
    """原子写 JSON（默认不转义中文，与项目其余落盘格式一致）"""
    return write_text_atomic(
        path, json.dumps(data, ensure_ascii=ensure_ascii, indent=indent))
