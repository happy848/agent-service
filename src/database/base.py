from sqlalchemy.ext.declarative import declarative_base

# 创建基础模型类
Base = declarative_base()

# 导入所有模型，确保它们在Base.metadata中注册
from .models.user import User  # noqa
from .models.chat import ChatMessage  # noqa 