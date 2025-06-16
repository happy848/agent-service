from sqlalchemy import Column, String, Integer, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from .base import BaseModel

class ChatMessage(BaseModel):
    """聊天消息模型"""
    __tablename__ = "chat_messages"

    user_id = Column(Integer, ForeignKey("users.id"))
    platform = Column(String(50))  # whatsapp, telegram 等
    content = Column(Text)
    message_type = Column(String(20))  # text, image, voice 等
    status = Column(String(20))  # pending, processed, failed 等
    metadata = Column(JSON, nullable=True)  # 额外的消息元数据
    
    # 关联关系
    user = relationship("User", back_populates="messages")
    
    def __repr__(self):
        return f"<ChatMessage {self.id} from {self.platform}>" 