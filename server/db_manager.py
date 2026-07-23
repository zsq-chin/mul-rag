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
from src.utils import logger

class DBManager:
    """数据库管理器 - 只提供基础的数据库连接和会话管理"""

    def __init__(self):
        self.db_path = os.path.join(config.save_dir, "data", "server.db")
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

# 创建全局数据库管理器实例
db_manager = DBManager()
