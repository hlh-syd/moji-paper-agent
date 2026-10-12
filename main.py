"""
论文写作智能体 主程序入口
"""
import os
import sys
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import get_config_manager
from src.web_api import run_server


def setup_logging(config):
    """全链路日志初始化（trace_id/user 自动附加 + 按大小轮转 + LLM 调用观测）"""
    from src import observability
    observability.configure_logging(config.logging)
    return logging.getLogger(__name__)


def load_env():
    """加载 .env（智谱 API key 等）"""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
    except ImportError:
        pass


def main():
    parser = argparse.ArgumentParser(description="墨记 论文智能体")
    parser.add_argument("--mode", choices=["api", "web"], default="web",
                        help="运行模式: api/web (Web服务)")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--host", help="服务器主机")
    parser.add_argument("--port", type=int, help="服务器端口")
    parser.add_argument("--build-kb", choices=["methods", "writing", "all"],
                        help="构建知识库后退出（methods=方法原论文, writing=写作教学）")
    args = parser.parse_args()

    load_env()
    config_manager = get_config_manager(args.config)
    config = config_manager.get_config()

    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port

    logger = setup_logging(config)
    logger.info("=" * 46)
    logger.info(f"  {config.system.name} v{config.system.version}")
    logger.info("=" * 46)
    logger.info(f"主模型: {config.models.llm} | 小模型: {config.models.small_llm} | 嵌入: {config.models.embedding}")

    if args.build_kb:
        from ingestion.build_methods_kb import build as build_methods
        import ingestion.build_writing_kb as wb

        if args.build_kb in ("methods", "all"):
            logger.info("构建方法原论文知识库...")
            build_methods(rebuild=True)
        if args.build_kb in ("writing", "all"):
            logger.info("构建写作教学知识库...")
            wb.collect_skills()
            wb.collect_web()
            wb.collect_bilibili()
            wb.ingest_collection(rebuild=True)
        logger.info("知识库构建完成")
        return

    logger.info(f"启动服务: http://localhost:{config.server.port}")
    run_server()


if __name__ == "__main__":
    main()
