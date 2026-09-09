"""OAuth 流程占位：M1 阶段实现授权 URL 生成、回调处理、token 刷新。"""


def build_authorize_url(provider: str, *, redirect_uri: str, state: str) -> str:
    """占位：直接返回占位 URL。"""
    return f"https://example.com/oauth/{provider}?redirect_uri={redirect_uri}&state={state}"


async def exchange_code(provider: str, code: str) -> dict[str, str]:
    """占位：返回空 token 字典。"""
    _ = (provider, code)
    return {}