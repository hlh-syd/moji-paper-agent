# -*- coding: utf-8 -*-
"""溢写存储（spill）

超长文本「全文落盘 + 行内只留头尾」，对应 harness 的 spillStore：
- **内容寻址**：文件名取 sha256 前 20 位，同一全文只存一份，天然幂等、无覆盖风险；
- **按用户隔离**：根目录经 scoping.spill_dir() 取，多用户互不可见；
- **有界**：文件数 / 总字节双上限，超限按 mtime 删最旧，只删自家命名模式；
- **失败即降级**：任何 IO 异常返回 None，调用方保留原文继续。
"""
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.atomic_io import write_text_atomic

logger = logging.getLogger(__name__)

_NS_RE = re.compile(r"[^A-Za-z0-9_\-]+")
_LOCATOR_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}/\d{6}/[0-9a-f]{20}\.txt$")
_FILE_RE = re.compile(r"^[0-9a-f]{20}\.txt$")


@dataclass(frozen=True)
class SpillRef:
    locator: str
    sha256: str
    chars: int


class SpillStore:
    def __init__(self, root, *, enabled: bool = True, max_files: int = 2000,
                 max_total_mb: int = 2048):
        # root 可以是 Path，也可以是返回 Path 的可调用对象（按请求 owner 动态解析）
        self._root_spec = root
        self.enabled = bool(enabled)
        self.max_files = max(10, int(max_files or 0))
        self.max_total_bytes = max(16, int(max_total_mb or 0)) * 1024 * 1024
        self._gc_at = 0.0

    @property
    def root(self) -> Path:
        spec = self._root_spec
        return Path(spec() if callable(spec) else spec)

    # ---------- 写 ----------
    def save_text(self, text: str, namespace: str = "chat") -> Optional[SpillRef]:
        if not self.enabled or not text:
            return None
        try:
            data = text.encode("utf-8")
            digest = hashlib.sha256(data).hexdigest()[:20]
            ns = self._namespace(namespace)
            yyyymm = time.strftime("%Y%m")
            rel = f"{ns}/{yyyymm}/{digest}.txt"
            path = self.root / ns / yyyymm / f"{digest}.txt"
            if not path.exists():
                write_text_atomic(path, text)
            self._maybe_gc()
            return SpillRef(locator=rel, sha256=digest, chars=len(text))
        except Exception as e:  # noqa: BLE001 - 留档是尽力而为
            logger.warning("溢写失败（保留行内内容）: %s", e)
            return None

    def _namespace(self, namespace: str) -> str:
        ns = _NS_RE.sub("_", (namespace or "chat").strip())[:32].strip("_")
        return ns or "chat"

    # ---------- 读 ----------
    def load_text(self, locator: str) -> Optional[str]:
        """按 locator 取回全文；locator 不合法或文件不存在返回 None。

        读 bytes 再解码：read_text 的 universal newlines 会把 \\r\\n 归一化成
        \\n，破坏「存什么读什么」的契约。
        """
        if not locator or not _LOCATOR_RE.match(locator):
            logger.warning("拒绝非法溢写定位符: %r", locator[:80])
            return None
        path = (self.root / locator)
        try:
            resolved = path.resolve()
            root = self.root.resolve()
            if root not in resolved.parents and resolved != root:
                logger.warning("溢写路径越界，已拒绝: %s", locator)
                return None
            return resolved.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.info("溢写读取失败 %s: %s", locator, e)
            return None

    # ---------- 回收 ----------
    def _iter_files(self):
        if not self.root.exists():
            return
        for path in self.root.rglob("*.txt"):
            if not _FILE_RE.match(path.name):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            yield path, st.st_mtime, st.st_size

    def _maybe_gc(self) -> None:
        now = time.monotonic()
        if now - self._gc_at < 30:
            return
        self._gc_at = now
        self.gc()

    def gc(self) -> int:
        """删最旧的文件直到低于上限的 80%；返回删除数量。永不抛异常。"""
        deleted = 0
        try:
            files = sorted(self._iter_files(), key=lambda x: x[1])
            total = sum(size for _, _, size in files)
            over_count = len(files) - int(self.max_files * 0.8)
            over_bytes = total - int(self.max_total_bytes * 0.8)
            for path, _, size in files:
                if over_count <= 0 and over_bytes <= 0:
                    break
                try:
                    path.unlink()
                    deleted += 1
                    over_count -= 1
                    over_bytes -= size
                except OSError:
                    continue
        except Exception as e:  # noqa: BLE001
            logger.warning("溢写回收异常（忽略）: %s", e)
        return deleted

    def stats(self) -> dict:
        try:
            files = list(self._iter_files())
            return {
                "enabled": self.enabled,
                "root": str(self.root),
                "files": len(files),
                "bytes": sum(size for _, _, size in files),
                "max_files": self.max_files,
                "max_total_mb": self.max_total_bytes // (1024 * 1024),
            }
        except Exception as e:  # noqa: BLE001
            return {"enabled": self.enabled, "error": str(e)}
