from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import event
from sqlalchemy.engine import Engine

from core.settings import settings

# 创建异步数据库引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO_LOG,
    future=True,
    poolclass=NullPool,
    # 设置时区为 UTC+8
    connect_args={"timezone": "Asia/Shanghai"}
)

# 创建异步会话工厂
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# 数据库依赖注入
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话的依赖注入函数
    
    Yields:
        AsyncSession: 异步数据库会话
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

# 初始化数据库
async def init_db() -> None:
    """
    初始化数据库，创建所有表
    """
    from .base import Base
    
    async with engine.begin() as conn:
        # 设置会话时区
        await conn.execute("SET timezone TO 'Asia/Shanghai';")
        await conn.run_sync(Base.metadata.create_all)

# 关闭数据库连接
async def close_db() -> None:
    """
    关闭数据库连接
    """
    await engine.dispose() 