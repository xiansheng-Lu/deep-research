"""全局配置：基于 Pydantic Settings，从环境变量加载并校验。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderConfig(BaseSettings):
    """LLM 提供方配置（主备两路）。"""

    base_url: str = Field(default="", alias="base_url")
    api_key: SecretStr = Field(default=SecretStr(""), alias="api_key")
    model: str = Field(default="", alias="model")


class Settings(BaseSettings):
    """应用全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ===== 运行环境 =====
    app_env: Literal["dev", "staging", "prod"] = Field(default="dev", alias="APP_ENV")
    app_name: str = Field(default="deep-research-api", alias="APP_NAME")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", alias="LOG_LEVEL"
    )
    log_json: bool = Field(default=True, alias="LOG_JSON")
    log_collector_endpoint: str = Field(default="", alias="LOG_COLLECTOR_ENDPOINT")

    # ===== 安全 =====
    secret_key: SecretStr = Field(alias="SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_ttl_minutes: int = Field(default=30, alias="JWT_ACCESS_TTL_MINUTES")
    jwt_refresh_ttl_days: int = Field(default=7, alias="JWT_REFRESH_TTL_DAYS")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    encryption_key: SecretStr = Field(default=SecretStr(""), alias="ENCRYPTION_KEY")

    # ===== 数据库 =====
    db_async_url: str = Field(alias="DB_ASYNC_URL")
    db_sync_url: str = Field(alias="DB_SYNC_URL")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    # 编排检查点后端：postgres=AsyncPostgresSaver（生产默认，支持跨请求/跨进程
    # HITL 恢复）；memory=应用级单例 InMemorySaver（测试与无 DB 环境兜底）
    checkpointer_backend: Literal["postgres", "memory"] = Field(
        default="postgres", alias="CHECKPOINTER_BACKEND"
    )

    # ===== Redis / Celery =====
    redis_url: str = Field(alias="REDIS_URL")
    celery_broker_url: str = Field(alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(alias="CELERY_RESULT_BACKEND")

    # ===== 对象存储 =====
    object_storage_endpoint: str = Field(alias="OBJECT_STORAGE_ENDPOINT")
    object_storage_access_key: str = Field(alias="OBJECT_STORAGE_ACCESS_KEY")
    object_storage_secret_key: SecretStr = Field(alias="OBJECT_STORAGE_SECRET_KEY")
    object_storage_bucket: str = Field(alias="OBJECT_STORAGE_BUCKET")
    object_storage_secure: bool = Field(default=False, alias="OBJECT_STORAGE_SECURE")

    # ===== LLM 主备 =====
    llm_primary_base_url: str = Field(default="", alias="LLM_PRIMARY_BASE_URL")
    llm_primary_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_PRIMARY_API_KEY")
    llm_primary_model: str = Field(default="", alias="LLM_PRIMARY_MODEL")
    llm_backup_base_url: str = Field(default="", alias="LLM_BACKUP_BASE_URL")
    llm_backup_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_BACKUP_API_KEY")
    llm_backup_model: str = Field(default="", alias="LLM_BACKUP_MODEL")
    llm_timeout_seconds: int = Field(default=60, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_circuit_fail_threshold: int = Field(default=5, alias="LLM_CIRCUIT_FAIL_THRESHOLD")
    llm_circuit_reset_seconds: int = Field(default=60, alias="LLM_CIRCUIT_RESET_SECONDS")

    # ===== 检索 =====
    # 公域检索供应方选择：bocha（国内）/ tavily（海外）
    web_search_provider: Literal["bocha", "tavily"] = Field(
        default="bocha", alias="WEB_SEARCH_PROVIDER"
    )
    tavily_api_key: SecretStr = Field(default=SecretStr(""), alias="TAVILY_API_KEY")
    bocha_api_key: SecretStr = Field(default=SecretStr(""), alias="BOCHA_API_KEY")
    bocha_base_url: str = Field(default="https://api.bochaai.com/v1", alias="BOCHA_BASE_URL")
    bocha_timeout_seconds: float = Field(default=30.0, alias="BOCHA_TIMEOUT_SECONDS")

    # ===== 成本治理 =====
    quota_default_tier: Literal["quick", "standard", "deep", "extreme"] = Field(
        default="standard", alias="QUOTA_DEFAULT_TIER"
    )
    quota_tier_quick_tokens: int = Field(default=50_000, alias="QUOTA_TIER_QUICK_TOKENS")
    quota_tier_standard_tokens: int = Field(default=150_000, alias="QUOTA_TIER_STANDARD_TOKENS")
    quota_tier_deep_tokens: int = Field(default=400_000, alias="QUOTA_TIER_DEEP_TOKENS")
    quota_tier_extreme_tokens: int = Field(default=1_000_000, alias="QUOTA_TIER_EXTREME_TOKENS")

    # ===== 种子账号（仅 dev/staging 联调，prod 下脚本拒绝执行） =====
    seed_team_name: str = Field(default="默认团队", alias="SEED_TEAM_NAME")
    seed_user_email: str = Field(default="dev@example.com", alias="SEED_USER_EMAIL")
    seed_user_password: SecretStr = Field(
        default=SecretStr("Dev@123456"), alias="SEED_USER_PASSWORD"
    )
    seed_user_display_name: str = Field(default="联调开发者", alias="SEED_USER_DISPLAY_NAME")

    # ===== 实时通信 =====
    ws_heartbeat_seconds: int = Field(default=25, alias="WS_HEARTBEAT_SECONDS")
    sse_heartbeat_seconds: int = Field(default=15, alias="SSE_HEARTBEAT_SECONDS")

    # ===== 追踪 =====
    otel_enabled: bool = Field(default=True, alias="OTEL_ENABLED")
    otel_exporter_otlp_endpoint: str = Field(default="", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field(default="deep-research-api", alias="OTEL_SERVICE_NAME")
    llm_trace_enabled: bool = Field(default=True, alias="LLM_TRACE_ENABLED")
    llm_trace_sample_rate: float = Field(default=0.01, alias="LLM_TRACE_SAMPLE_RATE")

    # ===== 派生属性 =====
    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[4]

    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """惰性单例：首次访问时构建，后续命中缓存。"""
    return Settings()  # type: ignore[call-arg]
