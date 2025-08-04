"""
Shared models for WhatsApp client functionality.
"""

from typing import Literal, List
from pydantic import BaseModel, Field


class MessageItem(BaseModel):
    """Message item model for WhatsApp messages."""
    type: Literal["received", "sent"] = Field(description="Type of the message.")
    sender: str = Field(description="Sender of the message.")
    content: str = Field(description="Content of the message.")
    datetime: str = Field(description="Datetime of the message.")
    timestamp: str = Field(description="Timestamp of the message.") 
    images: List[str] = Field(description="Images of the message.")