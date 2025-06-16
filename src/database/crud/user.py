from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.security import get_password_hash
from ..models.user import User
from .base import CRUDBase
from schema.user import UserCreate, UserUpdate

class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    """用户CRUD操作类"""
    
    async def get_by_email(self, db: AsyncSession, *, email: str) -> Optional[User]:
        """根据邮箱获取用户"""
        query = select(User).where(User.email == email)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_username(self, db: AsyncSession, *, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        query = select(User).where(User.username == username)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, *, obj_in: UserCreate) -> User:
        """创建新用户"""
        db_obj = User(
            email=obj_in.email,
            username=obj_in.username,
            hashed_password=get_password_hash(obj_in.password),
            is_active=True
        )
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_active_users(
        self, 
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100
    ) -> List[User]:
        """获取活跃用户列表"""
        query = select(User).where(User.is_active == True).offset(skip).limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def update_password(
        self, 
        db: AsyncSession,
        *,
        user: User,
        new_password: str
    ) -> User:
        """更新用户密码"""
        hashed_password = get_password_hash(new_password)
        user.hashed_password = hashed_password
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

# 创建CRUD实例
user = CRUDUser(User) 