from sqlalchemy import Column, String, Boolean
from sqlalchemy.orm import relationship
from .base import BaseModel

class User(BaseModel):
    """用户模型"""
    __tablename__ = "users"

    username = Column(String(50), unique=True, index=True)
    email = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(200))
    is_active = Column(Boolean, default=True)
    
    # 关联关系
    messages = relationship("ChatMessage", back_populates="user")
    
    def __repr__(self):
        return f"<User {self.username}>" 