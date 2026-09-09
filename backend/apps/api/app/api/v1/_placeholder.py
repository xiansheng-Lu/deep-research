"""路由占位生成器：避免重复样板，集中定义通用的 prefix / tags 占位逻辑。

每个路由模块都通过 ``build_router`` 构造自己的 APIRouter；
M1 阶段起逐步用业务实现替换占位 handler。
"""

from fastapi import APIRouter

_PLACEHOLDER_BODY: dict[str, str] = {
    "auth": "鉴权相关接口：登录、刷新、登出、当前用户。",
    "users": "用户管理：注册、邀请、个人信息、密码重置。",
    "teams": "团队与成员：团队 CRUD、成员角色、邀请与移除。",
    "projects": "研究项目：项目 CRUD、模板应用、归档。",
    "runs": "研究执行：发起 Run、查询进度、人机介入、终止。",
    "conflicts": "冲突审视：冲突列表、评论、裁决、版本对比。",
    "reports": "研究报告：报告详情、流式生成、导出、分享。",
    "knowledge": "知识库：条目 CRUD、向量检索、上传与解析。",
    "connectors": "外部连接器：列表、配置、OAuth 绑定、同步。",
    "templates": "模板管理：项目模板、报告模板、变量校验。",
    "audit": "审计日志：操作流水、查询、导出。",
}


def build_router(name: str, prefix: str) -> APIRouter:
    """构造指定名称的占位 APIRouter。

    Args:
        name: 资源名，对应 ``_PLACEHOLDER_BODY`` 的键。
        prefix: 路由前缀，如 ``"/runs"``。
    """
    router = APIRouter(prefix=prefix, tags=[name])

    @router.get("", summary=f"{name} 占位接口")
    async def _placeholder() -> dict[str, str]:
        return {"resource": name, "status": "scaffold"}

    return router