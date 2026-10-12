"""
文本处理工具
提供对模型响应的通用清理功能（如剥离 qwen3 思考模型的 <think> 推理块）
"""
import re
from typing import Optional

_THINK_OPEN = '<think>'
_THINK_CLOSE = '</think>'
# 匹配完整的 <think>...</think> 块
_THINK_PATTERN = re.compile(re.escape(_THINK_OPEN) + r'.*?' + re.escape(_THINK_CLOSE), re.DOTALL)


def strip_think_blocks(text: Optional[str]) -> str:
    """
    剥离文本中的 <think>...</think> 推理块

    qwen3:4b（2507 instruct）默认不输出思考内容，此函数作为防御性处理：
    即使误用了 thinking 变体模型，也能保证返回干净的正文。

    处理规则：
    1. 完整的 <think>...</think> 块整体删除
    2. 未闭合的 <think>（流式截断场景）：删除 <think> 及其后的全部内容

    Args:
        text (Optional[str]): 原始文本

    Returns:
        str: 剥离思考块后的文本
    """
    if not text or _THINK_OPEN not in text:
        return text or ""

    text = _THINK_PATTERN.sub('', text)

    # 处理未闭合的 <think>（如响应被截断）
    open_idx = text.find(_THINK_OPEN)
    if open_idx != -1:
        text = text[:open_idx]

    return text.strip()


class ThinkStreamFilter:
    """
    流式输出的 <think> 块过滤器

    在流式传输中逐块过滤思考内容，正确处理标签被切分在两个数据块边界的情况。
    用法：每收到一个 chunk 调用 feed() 取回安全输出，流结束后调用 flush() 取回尾部。
    """

    def __init__(self):
        self._buffer = ""
        self._in_think = False

    def feed(self, chunk: str) -> str:
        """
        送入一个流式数据块，返回可以安全下发的文本

        为避免 "</th" + "ink>" 这类被截断的标签漏过，会暂存标签长度以内的尾部内容。

        Args:
            chunk (str): 新到达的文本片段

        Returns:
            str: 过滤后可立即输出的文本（可能为空字符串）
        """
        if not chunk:
            return ""

        self._buffer += chunk
        output = []

        while self._buffer:
            tag = _THINK_CLOSE if self._in_think else _THINK_OPEN
            idx = self._buffer.find(tag)

            if self._in_think:
                if idx == -1:
                    # 结束标签未出现：截断到最多 keep 个字符，保留可能是截断标签的尾部
                    keep = len(_THINK_CLOSE) - 1
                    self._buffer = self._buffer[-keep:] if len(self._buffer) > keep else self._buffer
                    break
                # 跳过思考块，继续在剩余内容中查找下一个开始标签
                self._buffer = self._buffer[idx + len(_THINK_CLOSE):]
                self._in_think = False
            else:
                if idx == -1:
                    # 开始标签未出现：输出除可能的截断标签尾部外的所有内容
                    keep = len(_THINK_OPEN) - 1
                    if len(self._buffer) > keep:
                        output.append(self._buffer[:-keep])
                        self._buffer = self._buffer[-keep:]
                    break
                output.append(self._buffer[:idx])
                self._buffer = self._buffer[idx + len(_THINK_OPEN):]
                self._in_think = True

        return "".join(output)

    def flush(self) -> str:
        """
        流结束时调用，取回暂存的尾部内容

        Returns:
            str: 剩余可输出的文本；若流停在未闭合的思考块内则返回空字符串
        """
        remainder = self._buffer
        self._buffer = ""
        if self._in_think:
            return ""
        return remainder
