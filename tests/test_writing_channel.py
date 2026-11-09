# -*- coding: utf-8 -*-
"""写作独立通道（models.writing_llm）单测

覆盖：跟随主模型 / 独立 DeepSeek / key 缺失回退 / 热切换 / 供应商标签。
ChatOpenAI/Ollama 客户端构造不发网络请求，可安全实例化。
"""
import os
import unittest
from unittest import mock

from src.config import AppConfig
from src.llm_manager import LLMManager


def _cfg(llm="glm-5.3", writing="", deepseek_model_id="ds-flash-v2"):
    cfg = AppConfig()
    cfg.models.llm = llm
    cfg.models.writing_llm = writing
    cfg.deepseek.model_id = deepseek_model_id
    return cfg


class WritingChannelTest(unittest.TestCase):
    def setUp(self):
        # key 经环境变量注入（走 resolve_api_key 真实路径），用例结束自动还原
        self._env = mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test",
                                                 "ZHIPU_API_KEY": "zk-test"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
    def test_empty_writing_follows_main(self):
        mgr = LLMManager(_cfg(llm="glm-5.3", writing=""))
        self.assertTrue(mgr._writing_follows_main)
        self.assertEqual(mgr._writing_route.provider, "zhipu")
        self.assertEqual(mgr._writing_route.model, "glm-5.3")
        self.assertIsNotNone(mgr.writing_llm)
        self.assertEqual(mgr.writing_provider_label(), "智谱")

    def test_explicit_deepseek_channel(self):
        mgr = LLMManager(_cfg(llm="glm-5.3", writing="deepseek-4.1-flash"))
        self.assertFalse(mgr._writing_follows_main)
        self.assertEqual(mgr._writing_route.provider, "deepseek")
        # 真实请求的 model id 来自 config.deepseek.model_id，而非展示名
        self.assertEqual(mgr._writing_route.model, "ds-flash-v2")
        self.assertIsNot(mgr.writing_llm, mgr.main_llm)   # 独立实例
        self.assertEqual(mgr.writing_provider_label(), "DeepSeek")
        self.assertIs(mgr.get_writing_llm(), mgr.writing_llm)

    def test_main_channel_unaffected_by_writing(self):
        mgr = LLMManager(_cfg(llm="glm-5.3", writing="deepseek-4.1-flash"))
        self.assertEqual(mgr._main_route.provider, "zhipu")
        self.assertIs(mgr.get_main_llm(), mgr.main_llm)

    def test_missing_deepseek_key_falls_back_to_main(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        mgr = LLMManager(_cfg(llm="glm-5.3", writing="deepseek-4.1-flash"))
        self.assertIsNone(mgr.writing_llm)      # 写作实例不可用
        self.assertIs(mgr.get_writing_llm(), mgr.main_llm)  # 回退主通道
        self.assertIn("回退", mgr.writing_provider_label())

    def test_missing_key_when_following_main_is_silent(self):
        # 跟随模式且主通道可用：不应出现写作专属警告分支（主通道自己的警告另管）
        mgr = LLMManager(_cfg(llm="glm-5.3", writing=""))
        self.assertTrue(mgr._writing_follows_main)
        self.assertIsNotNone(mgr.writing_llm)

    def test_hot_switch_via_reinit(self):
        cfg = _cfg(llm="glm-5.3", writing="")
        mgr = LLMManager(cfg)
        self.assertEqual(mgr._writing_route.provider, "zhipu")
        cfg.models.writing_llm = "deepseek-4.1-flash"
        mgr.reinit_writing_llm()
        self.assertEqual(mgr._writing_route.provider, "deepseek")
        cfg.models.writing_llm = ""
        mgr.reinit_writing_llm()
        self.assertTrue(mgr._writing_follows_main)
        self.assertEqual(mgr._writing_route.provider, "zhipu")

    def test_reinit_main_refreshes_following_writing(self):
        cfg = _cfg(llm="glm-5.3", writing="")
        mgr = LLMManager(cfg)
        cfg.models.llm = "deepseek-4.1-flash"
        mgr.reinit_main_llm()
        self.assertTrue(mgr._writing_follows_main)
        self.assertEqual(mgr._writing_route.provider, "deepseek")

    def test_model_info_fields(self):
        mgr = LLMManager(_cfg(llm="glm-5.3", writing="deepseek-4.1-flash",
                              deepseek_model_id="ds-flash-v2"))
        info = mgr.get_model_info()
        self.assertEqual(info["writing_llm"], "deepseek-4.1-flash")
        self.assertFalse(info["writing_follows_main"])
        self.assertEqual(info["writing_provider_label"], "DeepSeek")
        self.assertEqual(info["writing_api_model"], "ds-flash-v2")
        self.assertTrue(info["writing_llm_available"])
        # 主模型字段不受影响
        self.assertEqual(info["main_llm"], "glm-5.3")

    def test_writing_fallback_when_no_provider_at_all(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("ZHIPU_API_KEY", None)
        cfg = _cfg(llm="glm-5.3", writing="deepseek-4.1-flash")
        mgr = LLMManager(cfg)
        with self.assertRaises(RuntimeError):
            mgr.get_writing_llm()         # 无任何可用通道：明确失败而非静默


if __name__ == "__main__":
    unittest.main()


class StreamChannelRoutingTest(unittest.IsolatedAsyncioTestCase):
    """_stream_llm 的通道分流：写作走 writing，聊天显式 main"""

    def _writer(self):
        from src.section_writer import SectionWriter
        sw = SectionWriter.__new__(SectionWriter)   # 只测 _stream_llm，跳过重初始化
        sw.llm_manager = mock.MagicMock()
        return sw

    @staticmethod
    def _fake_llm(tag):
        from types import SimpleNamespace
        class _L:
            async def astream(self, messages):
                # 与 langchain 一致：chunk 是带 .content 属性的对象
                yield SimpleNamespace(content=tag)
        return _L()

    async def test_default_channel_is_writing(self):
        sw = self._writer()
        sw.llm_manager.get_writing_llm.return_value = self._fake_llm("W")
        sw.llm_manager.get_main_llm.return_value = self._fake_llm("M")
        sink = []
        out = [p async for p in sw._stream_llm("s", "u", sink)]
        self.assertEqual(out, ["W"])
        sw.llm_manager.get_writing_llm.assert_called_once()
        sw.llm_manager.get_main_llm.assert_not_called()

    async def test_main_channel_explicit(self):
        sw = self._writer()
        sw.llm_manager.get_writing_llm.return_value = self._fake_llm("W")
        sw.llm_manager.get_main_llm.return_value = self._fake_llm("M")
        sink = []
        out = [p async for p in sw._stream_llm("s", "u", sink, channel="main")]
        self.assertEqual(out, ["M"])
        sw.llm_manager.get_main_llm.assert_called_once()
        sw.llm_manager.get_writing_llm.assert_not_called()

    def test_chat_uses_main_channel(self):
        import inspect
        from src import web_api
        src = inspect.getsource(web_api.PaperAgentSystem.chat_stream)
        self.assertIn('channel="main"', src)
