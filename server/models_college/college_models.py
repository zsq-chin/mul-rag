from typing import List, Optional

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import datetime

from server.models_college import Base

class Users(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    username: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)
    last_login: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    guide_records: Mapped[List['GuideRecords']] = relationship('GuideRecords', back_populates='user')


class GuideRecords(Base):
    __tablename__ = 'guide_records'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], name='guide_records_ibfk_1'),
        Index('user_id', 'user_id')
    )

    id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    guide_id: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(INTEGER(11))
    updatetime: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime)

    user: Mapped['Users'] = relationship('Users', back_populates='guide_records')
