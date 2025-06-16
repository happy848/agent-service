from datetime import datetime
import pytz
from sqlalchemy import Column, Integer, DateTime
from ..base import Base

# 设置时区为 UTC+8
TIMEZONE = pytz.timezone('Asia/Shanghai')

def get_current_time():
    """获取当前 UTC+8 时间"""
    return datetime.now(TIMEZONE)

class BaseModel(Base):
    """基础模型类，包含共同字段"""
    __abstract__ = True

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), default=get_current_time)
    updated_at = Column(DateTime(timezone=True), default=get_current_time, onupdate=get_current_time)

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.id}>" 