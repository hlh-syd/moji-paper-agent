"""
对话历史管理器
负责管理对话历史、生成摘要和持久化存储
"""
import json
import logging
import traceback
from typing import Dict, List, Optional, Any
from datetime import datetime
import hashlib

from src.config import get_config_manager
from src.text_utils import strip_think_blocks

logger = logging.getLogger(__name__)


class ConversationMessage:
    """
    对话消息
    """

    def __init__(self, role: str, content: str, timestamp: Optional[str] = None):
        """
        初始化对话消息

        Args:
            role (str): 消息的角色类型
            content (str): 消息的内容
            timestamp (Optional[str], optional): 消息的时间戳。默认使用当前时间。
        """
        self.role = role  # user, assistant, system, tool
        self.content = content
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """
        将消息对象转换为字典格式

        Returns:
            Dict[str, Any]: 包含角色、内容和时间戳的字典
        """
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationMessage':
        """
        从字典创建对话消息对象

        Args:
            data (Dict[str, Any]): 包含消息信息的字典

        Returns:
            ConversationMessage: 创建的消息对象
        """
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=data.get("timestamp")
        )


class ConversationHistory:
    """
    对话历史管理类
    """

    def __init__(self, session_id: str, config=None):
        """
        初始化对话历史

        Args:
            session_id (str): 会话标识符
            config: 配置对象，包含对话相关配置
        """
        self.session_id = session_id
        self.config = config

        # 从配置获取参数，如果没有配置则使用默认值
        if config:
            self.max_messages = getattr(config, 'max_messages', 50)
            self.summary_min_messages = getattr(config, 'summary_min_messages', 5)
        else:
            self.max_messages = 50
            self.summary_min_messages = 5

        self.messages: List[ConversationMessage] = []
        self.summary: str = ""
        self.last_summary_time: Optional[str] = None
        self.summary_version: int = 0

        logger.info(f"创建对话历史: {session_id}, 最大消息数: {self.max_messages}, 最小摘要消息数: {self.summary_min_messages}")

    def add_message(self, message: ConversationMessage):
        """添加消息到对话历史"""
        logger.info(f"添加消息到对话 {self.session_id}: {message.role} - {message.content[:50]}...")
        self.messages.append(message)

        # 限制消息数量
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
            logger.info(f"对话 {self.session_id} 消息数达到上限，保留最近 {self.max_messages} 条")

        logger.info(f"对话 {self.session_id} 当前消息数: {len(self.messages)}")

        # 打印所有消息用于调试
        for i, msg in enumerate(self.messages):
            logger.debug(f"  消息{i + 1}: {msg.role} - {msg.content[:30]}...")

    def get_recent_messages(self, count: int = 5) -> List[ConversationMessage]:
        """
        获取最近的几条消息

        Args:
            count (int, optional): 需要获取的消息数量。默认为5。

        Returns:
            List[ConversationMessage]: 最近的消息列表
        """
        return self.messages[-count:] if self.messages else []

    def get_formatted_history(self, include_summary: bool = True) -> str:
        """
        获取格式化的对话历史文本

        Args:
            include_summary (bool, optional): 是否包含摘要信息。默认为True。

        Returns:
            str: 格式化的对话历史字符串
        """
        parts = []

        # 添加摘要（如果有）
        if include_summary and self.summary:
            parts.append(f"对话摘要（{self.last_summary_time}）：\n{self.summary}\n")

        # 添加最近的对话历史
        parts.append("最近对话记录：")
        for msg in self.get_recent_messages(3):
            role_map = {
                "user": "用户",
                "assistant": "助手",
                "system": "系统",
                "tool": "工具"
            }
            role_name = role_map.get(msg.role, msg.role)
            parts.append(f"{role_name}: {msg.content[:100]}{'...' if len(msg.content) > 100 else ''}")

        return "\n".join(parts)

    def clear(self):
        """清空对话历史"""
        self.messages = []
        self.summary = ""
        self.last_summary_time = None
        self.summary_version = 0
        logger.info(f"清空对话历史: {self.session_id}")

    def to_dict(self) -> Dict[str, Any]:
        """
        将对话历史转换为字典格式

        Returns:
            Dict[str, Any]: 包含所有对话历史信息的字典
        """
        return {
            "session_id": self.session_id,
            "messages": [msg.to_dict() for msg in self.messages],
            "summary": self.summary,
            "last_summary_time": self.last_summary_time,
            "summary_version": self.summary_version,
            "created_at": datetime.now().isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationHistory':
        """
        从字典创建对话历史对象

        Args:
            data (Dict[str, Any]): 包含对话历史信息的字典

        Returns:
            ConversationHistory: 创建的对话历史对象
        """
        history = cls(
            session_id=data["session_id"],
            config=None
        )
        history.messages = [ConversationMessage.from_dict(msg) for msg in data.get("messages", [])]
        history.summary = data.get("summary", "")
        history.last_summary_time = data.get("last_summary_time")
        history.summary_version = data.get("summary_version", 0)
        return history


class ConversationManager:
    """
    对话管理器，负责整个应用中的对话生命周期管理
    """

    def __init__(self, config=None):
        """
        初始化对话管理器

        Args:
            config: 配置对象，如果为空则自动获取默认配置
        """
        self.config = config or get_config_manager().get_config()
        self.conversations: Dict[str, ConversationHistory] = {}
        self.summary_llm = None

        logger.info(f"初始化对话管理器，配置摘要最小消息数: {self.config.conversation.summary_min_messages}")
        self._initialize_llm()

    def _initialize_llm(self):
        """初始化用于生成摘要的LLM"""
        try:
            from src.llm_manager import LLMManager
            llm_manager = LLMManager(self.config)
            self.summary_llm = llm_manager.get_llm()
            logger.info("对话摘要LLM初始化完成")
        except Exception as e:
            logger.error(f"对话摘要LLM初始化失败: {e}")
            self.summary_llm = None

    def get_or_create_conversation(self, session_id: str) -> ConversationHistory:
        """获取或创建对话历史"""
        if session_id not in self.conversations:
            logger.info(f"创建新对话: {session_id}")
            self.conversations[session_id] = ConversationHistory(
                session_id,
                self.config.conversation
            )
        else:
            logger.debug(f"获取现有对话: {session_id}, 消息数: {len(self.conversations[session_id].messages)}")

        return self.conversations[session_id]

    def check_summary_service(self, session_id: str) -> Dict[str, Any]:
        """检查摘要生成服务的状态"""
        try:
            # 检查对话是否存在
            conversation = self.get_or_create_conversation(session_id)

            return {
                "session_exists": True,
                "message_count": len(conversation.messages),
                "llm_available": self.summary_llm is not None,
                "summary_exists": bool(conversation.summary),
                "summary_version": conversation.summary_version,
                "config": {
                    "summary_min_messages": getattr(self.config.conversation, 'summary_min_messages', 10),
                    "summary_interval": getattr(self.config.conversation, 'summary_interval', 5),
                    "enable_auto_summary": getattr(self.config.conversation, 'enable_auto_summary', True)
                }
            }
        except Exception as e:
            return {
                "session_exists": False,
                "error": str(e)
            }

    def add_message(self, session_id: str, role: str, content: str):
        """向指定会话添加消息"""
        try:
            if not session_id:
                logger.error("添加消息失败: session_id为空")
                return

            if not content or not content.strip():
                logger.error("添加消息失败: 内容为空")
                return

            conversation = self.get_or_create_conversation(session_id)
            message = ConversationMessage(role, content)
            conversation.add_message(message)

            logger.info(f"成功添加消息到对话 {session_id}，当前总消息数: {len(conversation.messages)}")

            # 打印所有对话状态用于调试
            self._log_all_conversations()

        except Exception as e:
            logger.error(f"添加消息失败: {e}")
            logger.error(traceback.format_exc())

    def _log_all_conversations(self):
        """记录所有对话状态用于调试"""
        logger.debug("=== 所有对话状态 ===")
        for sid, conv in self.conversations.items():
            logger.debug(f"对话 {sid}: {len(conv.messages)} 条消息")
            for i, msg in enumerate(conv.messages[-3:]):  # 只显示最后3条
                logger.debug(f"  消息{i + 1}: {msg.role} - {msg.content[:30]}...")

    async def generate_summary(self, session_id: str) -> str:
        """生成对话摘要"""
        try:
            logger.info(f"开始生成摘要，会话: {session_id}")

            # 获取对话
            conversation = self.get_or_create_conversation(session_id)

            # 检查消息数量
            min_messages = conversation.summary_min_messages
            current_messages = len(conversation.messages)

            logger.info(f"摘要生成检查: 需要 {min_messages} 条消息，当前有 {current_messages} 条")

            # 打印所有消息用于调试
            for i, msg in enumerate(conversation.messages):
                logger.info(f"消息{i + 1}: {msg.role} - {msg.content[:50]}...")

            if current_messages < min_messages:
                error_msg = f"消息数量不足，至少需要{min_messages}条消息才能生成摘要，当前有{current_messages}条"
                logger.warning(error_msg)
                return error_msg

            # 检查LLM
            if not self.summary_llm:
                error_msg = "LLM服务未初始化，无法生成摘要"
                logger.error(error_msg)
                return error_msg

            # 准备对话内容
            messages_text = "\n".join([
                f"{'用户' if msg.role == 'user' else '助手'}: {msg.content}"
                for msg in conversation.messages[-20:]  # 最多使用20条消息
            ])

            # 构建提示
            summary_prompt = f"""请根据以下对话内容，生成一个简洁的摘要，总结对话的主要内容和关键信息：

对话内容：
{messages_text}

请生成一个中文摘要，要求：
1. 简洁明了，不超过200字
2. 总结对话的主要话题和关键信息
3. 保留重要的决策和建议
4. 如果对话涉及计算或查询结果，请总结关键数据

对话摘要："""

            logger.info(f"发送摘要生成请求，提示长度: {len(summary_prompt)}")

            # 生成摘要（剥离思考块，避免思考过程污染摘要内容）
            response = await self.summary_llm.ainvoke(summary_prompt)
            summary = strip_think_blocks(response)

            # 更新对话历史
            conversation.summary = summary
            conversation.last_summary_time = datetime.now().isoformat()
            conversation.summary_version += 1

            logger.info(f"摘要生成成功，版本: {conversation.summary_version}")
            logger.info(f"摘要内容: {summary[:100]}...")

            return summary

        except Exception as e:
            error_msg = f"摘要生成失败: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return error_msg

    def get_stats(self, session_id: str) -> Dict[str, Any]:
        """获取对话统计信息"""
        if session_id not in self.conversations:
            logger.warning(f"获取统计信息失败: 会话 {session_id} 不存在")
            return {
                "session_id": session_id,
                "message_count": 0,
                "has_summary": False,
                "summary_version": 0,
                "last_summary_time": None
            }

        conversation = self.conversations[session_id]

        stats = {
            "session_id": session_id,
            "message_count": len(conversation.messages),
            "has_summary": bool(conversation.summary),
            "summary_version": conversation.summary_version,
            "last_summary_time": conversation.last_summary_time,
            "summary_min_messages": conversation.summary_min_messages
        }

        logger.info(f"获取对话统计: {stats}")
        return stats

    def get_conversation_context(self, session_id: str) -> str:
        """
        获取指定会话的上下文信息（包括摘要和最近的历史）

        Args:
            session_id (str): 会话ID

        Returns:
            str: 格式化的对话上下文字符串
        """
        conversation = self.get_or_create_conversation(session_id)
        return conversation.get_formatted_history()

    def clear_conversation(self, session_id: str):
        """清空对话历史"""
        if session_id in self.conversations:
            self.conversations[session_id].clear()
            logger.info(f"清空对话: {session_id}")
        else:
            logger.warning(f"尝试清空不存在的对话: {session_id}")

    def save_conversation(self, session_id: str, filepath: str):
        """
        将指定会话的对话历史保存到文件

        Args:
            session_id (str): 会话ID
            filepath (str): 文件路径
        """
        if session_id in self.conversations:
            conversation = self.conversations[session_id]
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(conversation.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"保存对话历史到文件: {filepath}")

    def load_conversation(self, session_id: str, filepath: str) -> bool:
        """
        从文件加载对话历史到指定会话

        Args:
            session_id (str): 目标会话ID
            filepath (str): 要加载的文件路径

        Returns:
            bool: 加载成功返回True，失败返回False
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if data["session_id"] == session_id:
                self.conversations[session_id] = ConversationHistory.from_dict(data)
                logger.info(f"从文件加载对话历史: {filepath}")
                return True
        except Exception as e:
            logger.error(f"加载对话历史失败: {e}")

        return False

    def get_stats(self, session_id: str) -> Dict[str, Any]:
        """
        获取指定会话的统计信息

        Args:
            session_id (str): 会话ID

        Returns:
            Dict[str, Any]: 包含各种统计信息的字典
        """
        if session_id not in self.conversations:
            return {"error": "对话不存在"}

        conversation = self.conversations[session_id]

        return {
            "session_id": session_id,
            "message_count": len(conversation.messages),
            "has_summary": bool(conversation.summary),
            "summary_version": conversation.summary_version,
            "last_summary_time": conversation.last_summary_time,
            "recent_messages": [
                {"role": msg.role, "content_preview": msg.content[:50]}
                for msg in conversation.get_recent_messages(3)
            ]
        }


# 全局对话管理器实例
_conversation_manager = None

def get_conversation_manager(config=None) -> ConversationManager:
    """获取全局对话管理器"""
    global _conversation_manager
    if _conversation_manager is None:
        _conversation_manager = ConversationManager(config)
    return _conversation_manager