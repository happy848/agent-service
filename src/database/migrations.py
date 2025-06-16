import os
from pathlib import Path
import typer
from alembic.config import Config
from alembic import command

app = typer.Typer()

def get_alembic_config() -> Config:
    """获取 Alembic 配置"""
    # 获取项目根目录
    root_dir = Path(__file__).parent.parent.parent
    config_file = os.path.join(root_dir, "alembic.ini")
    
    config = Config(config_file)
    return config

@app.command()
def init() -> None:
    """初始化迁移环境"""
    config = get_alembic_config()
    command.init(config, "src/database/migrations")
    typer.echo("迁移环境已初始化")

@app.command()
def create(message: str) -> None:
    """创建新的迁移版本"""
    config = get_alembic_config()
    command.revision(config, message=message, autogenerate=True)
    typer.echo(f"已创建新的迁移版本: {message}")

@app.command()
def upgrade(revision: str = "head") -> None:
    """升级数据库到指定版本"""
    config = get_alembic_config()
    command.upgrade(config, revision)
    typer.echo(f"数据库已升级到版本: {revision}")

@app.command()
def downgrade(revision: str = "-1") -> None:
    """回滚数据库到指定版本"""
    config = get_alembic_config()
    command.downgrade(config, revision)
    typer.echo(f"数据库已回滚到版本: {revision}")

@app.command()
def history() -> None:
    """显示迁移历史"""
    config = get_alembic_config()
    command.history(config)

@app.command()
def current() -> None:
    """显示当前版本"""
    config = get_alembic_config()
    command.current(config)

if __name__ == "__main__":
    app() 