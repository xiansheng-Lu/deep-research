"""联调种子数据脚本：创建默认团队与首个可登录用户。

M1 阶段没有注册接口，全新数据库经 ``alembic upgrade head`` 后不存在任何用户，
前端无法通过 ``POST /api/v1/auth/login`` 登录。本脚本在迁移完成后执行一次，
为本地 / 联调环境写入一个默认可用账号。

幂等约定（重复执行不会产生重复数据，也不会覆盖既有密码）：
- 团队按名称查找，已存在则复用；
- 用户按邮箱查找，已存在则跳过。

用法::

    uv run python -m app.db.seed
    uv run deep-research-seed

账号参数来自环境变量（见 ``app.core.config.Settings``）：
``SEED_TEAM_NAME`` / ``SEED_USER_EMAIL`` / ``SEED_USER_PASSWORD`` /
``SEED_USER_DISPLAY_NAME``，默认值仅用于本地联调。
``APP_ENV=prod`` 时脚本直接拒绝执行，避免向生产库写入公开默认账号。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db.models.identity import Team, User


async def _get_or_create_team(session: AsyncSession, settings: Settings) -> tuple[Team, bool]:
    """按名称查找团队，不存在则创建；返回团队与"是否新建"标记。"""
    team = await session.scalar(select(Team).where(Team.name == settings.seed_team_name))
    if team is not None:
        return team, False

    team = Team(name=settings.seed_team_name, plan="free", settings={})
    session.add(team)
    await session.flush()
    return team, True


async def _create_user_if_absent(session: AsyncSession, team: Team, settings: Settings) -> bool:
    """按邮箱查找用户，不存在则创建 owner 角色账号；返回"是否新建"标记。"""
    existing = await session.scalar(
        select(User).where(User.email == settings.seed_user_email)
    )
    if existing is not None:
        return False

    user = User(
        team_id=team.id,
        email=settings.seed_user_email,
        hashed_password=hash_password(settings.seed_user_password.get_secret_value()),
        display_name=settings.seed_user_display_name,
        role="owner",
    )
    session.add(user)
    return True


async def _seed() -> None:
    """执行种子写入主流程。"""
    settings = get_settings()
    if settings.app_env == "prod":
        raise RuntimeError("禁止在 prod 环境执行联调种子脚本")

    # 脚本使用独立短生命周期引擎，不复用应用全局单例
    engine = create_async_engine(settings.db_async_url, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as session:
            team, team_created = await _get_or_create_team(session, settings)
            user_created = await _create_user_if_absent(session, team, settings)
            await session.commit()

        if user_created:
            print(
                "种子账号写入完成："
                f"团队={settings.seed_team_name}（{'新建' if team_created else '已存在'}），"
                f"登录邮箱={settings.seed_user_email}，"
                "密码为 SEED_USER_PASSWORD 配置值（本地默认 Dev@123456）"
            )
        else:
            print(f"种子账号已存在，跳过写入：登录邮箱={settings.seed_user_email}")
    finally:
        await engine.dispose()


def run() -> None:
    """console script 入口（``deep-research-seed``）。"""
    asyncio.run(_seed())


if __name__ == "__main__":
    run()
