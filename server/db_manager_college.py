import os
import pathlib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from src import config
from server.models_college import Base
from server.models.user_model import User
from src.utils import logger

class DBManagerCollege:
    """数据库管理器 - 只提供基础的数据库连接和会话管理"""

    def __init__(self):

        # 创建SQLAlchemy引擎
        DATABASE_URL = "mysql+pymysql://root:CUPer123456@mysql:3306/test"
        self.engine = create_engine(DATABASE_URL, echo=True)

        # 创建会话工厂
        self.Session = sessionmaker(bind=self.engine)

        # 确保表存在
        self.create_tables()

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


# 创建全局数据库管理器实例
db_manager_college = DBManagerCollege()
