import os
import pathlib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from src import config
from server.models import Base
from server.models.user_model import User
from server.models.thread_model import Thread
from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode
from server.models.statistics_model import Question, Discussion, HelpRequest
from server.models.user_model_credential import UserModelCredential
from server.models.feedback_model import AnswerFeedback
from server.models.governance_model import KnowledgeGovernance, KnowledgeDocumentVersion
from server.models.evaluation_model import EvaluationSuite, EvaluationCase
from server.models.operations_model import (
    ConfigChangeHistory,
    BackupJob,
    AlertRule,
    AlertEvent,
)
from src.utils import logger

class DBManager:
    """数据库管理器 - 只提供基础的数据库连接和会话管理"""

    def __init__(self):
        # SAGE_DB_PATH 允许把 SQLite 库放到 FUSE bind mount 之外（例如 Docker 命名卷），
        # 避免 Docker Desktop 的 bind mount 对 server.db 路径的陈旧句柄导致 “unable to open database file”。
        # 未设置时保持原行为（saves/data/server.db），不改变本机/测试运行路径。
        self.db_path = os.environ.get("SAGE_DB_PATH") or os.path.join(
            config.save_dir, "data", "server.db"
        )
        self.ensure_db_dir()

        # 创建SQLAlchemy引擎
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={
                "timeout": 30,
                "check_same_thread": False,
            },
            pool_pre_ping=True,
        )

        # Set SQLite pragmas on every new connection
        from sqlalchemy import event

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        # 创建会话工厂
        self.Session = sessionmaker(bind=self.engine)

        # 确保表存在
        self.create_tables()

    def ensure_db_dir(self):
        """确保数据库目录存在"""
        db_dir = os.path.dirname(self.db_path)
        pathlib.Path(db_dir).mkdir(parents=True, exist_ok=True)

    def create_tables(self):
        """创建数据库表"""
        # 确保所有表都会被创建，SQLAlchemy会自动扫描所有继承自Base的类并注册它们
        Base.metadata.create_all(self.engine)
        logger.info("Database tables created/checked")

    def get_session(self):
        """获取数据库会话"""
        return self.Session()

    @contextmanager
    def get_session_context(self):
        """获取数据库会话的上下文管理器"""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database operation failed: {e}")
            raise
        finally:
            session.close()

    def check_first_run(self):
        """检查是否首次运行"""
        session = self.get_session()
        try:
            # 检查是否有任何用户存在
            return session.query(User).count() == 0
        finally:
            session.close()

    def close(self):
        """关闭引擎并释放全部 SQLite 连接（9.1.4：应用 shutdown 不再残留 unclosed connection）。"""
        try:
            self.engine.dispose()
        except Exception as exc:  # 关闭阶段异常不允许打断进程退出
            logger.warning("DB engine dispose 失败: %s", exc)

# 创建全局数据库管理器实例
db_manager = DBManager()
