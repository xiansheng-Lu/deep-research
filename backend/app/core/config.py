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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
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

    # ===== M2-8b 长任务接管（worker/跨进程） =====
    # true：RealtimeHub 走 Redis Pub/Sub、registry 走租约/控制键（生产 worker 必须）；
    # false：退回进程内内存 Hub/registry（仅离线测试/单进程）
    worker_enable_redis: bool = Field(default=True, alias="WORKER_ENABLE_REDIS")
    # worker 标识（多副本时由部署注入；缺省用主机名+pid 兜底）
    worker_id: str = Field(default="", alias="WORKER_ID")
    run_lease_ttl_seconds: int = Field(default=30, alias="RUN_LEASE_TTL_SECONDS")
    run_lease_heartbeat_seconds: int = Field(default=10, alias="RUN_LEASE_HEARTBEAT_SECONDS")
    run_orphan_grace_seconds: int = Field(default=60, alias="RUN_ORPHAN_GRACE_SECONDS")
    # worker 启动时清扫孤儿 run；周期 beat 默认不开（仅启动扫一次）
    run_orphan_sweep_enabled: bool = Field(default=True, alias="RUN_ORPHAN_SWEEP_ENABLED")
    celery_task_max_retries: int = Field(default=2, alias="CELERY_TASK_MAX_RETRIES")

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
    web_search_provider: Literal["bocha", "tavily"] = Field(default="bocha", alias="WEB_SEARCH_PROVIDER")
    tavily_api_key: SecretStr = Field(default=SecretStr(""), alias="TAVILY_API_KEY")
    bocha_api_key: SecretStr = Field(default=SecretStr(""), alias="BOCHA_API_KEY")
    bocha_base_url: str = Field(default="https://api.bochaai.com/v1", alias="BOCHA_BASE_URL")
    bocha_timeout_seconds: float = Field(default=30.0, alias="BOCHA_TIMEOUT_SECONDS")

    # ===== 信源元数据页面补采（M2-6） =====
    # Provider 不返回发布时间时，是否抓取页面 HTML 经 trafilatura 补采元数据
    source_page_metadata_enabled: bool = Field(default=True, alias="SOURCE_PAGE_METADATA_ENABLED")
    # 单页抓取超时（秒），超时静默跳过
    source_page_fetch_timeout_seconds: float = Field(default=5.0, alias="SOURCE_PAGE_FETCH_TIMEOUT_SECONDS")
    # 每子问题补采页面数上限（只补缺发布时间者，0 等价关闭）
    source_page_fetch_per_subquestion: int = Field(default=3, alias="SOURCE_PAGE_FETCH_PER_SUBQUESTION")

    # ===== 埋点接收（M2-8a） =====
    # 稳定采样率（0~1，按 user_id 哈希）；0 等价关闭落库（端点仍 204）
    telemetry_sample_rate: float = Field(default=1.0, alias="TELEMETRY_SAMPLE_RATE")
    # 每用户每分钟批量请求上限，超限 429
    telemetry_rate_limit_per_min: int = Field(default=12, alias="TELEMETRY_RATE_LIMIT_PER_MIN")

    # ===== 结构化报告生成（M2-7） =====
    # 终稿 blocks 计划单次结构化调用硬超时（秒）；超时/异常走机械映射降级
    report_llm_timeout_seconds: float = Field(default=45.0, alias="REPORT_LLM_TIMEOUT_SECONDS")
    # blocks 计划输出 token 上限
    report_llm_max_tokens: int = Field(default=4096, alias="REPORT_LLM_MAX_TOKENS")

    # ===== 成本治理 =====
    quota_default_tier: Literal["quick", "standard", "deep", "extreme"] = Field(
        default="standard", alias="QUOTA_DEFAULT_TIER"
    )
    quota_tier_quick_tokens: int = Field(default=50_000, alias="QUOTA_TIER_QUICK_TOKENS")
    quota_tier_standard_tokens: int = Field(default=150_000, alias="QUOTA_TIER_STANDARD_TOKENS")
    quota_tier_deep_tokens: int = Field(default=400_000, alias="QUOTA_TIER_DEEP_TOKENS")
    quota_tier_extreme_tokens: int = Field(default=1_000_000, alias="QUOTA_TIER_EXTREME_TOKENS")
    # M2-3 实时成本：token.usage.update 最小帧间隔（毫秒）与预警阈值；
    # danger 阈值同时是成本挂起闸门的唯一事实源（edges 经 quota.tiers 读取）
    cost_update_interval_ms: int = Field(default=1000, alias="COST_UPDATE_INTERVAL_MS")
    cost_warning_ratio: float = Field(default=0.70, alias="COST_WARNING_RATIO")
    cost_danger_ratio: float = Field(default=0.90, alias="COST_DANGER_RATIO")

    # ===== 运维可观测 =====
    # M2-3 Prometheus 指标端点开关：false 时不注册 GET /metrics
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")

    # ===== HITL 用户介入（M2-5）=====
    # 澄清挂起的展示超时秒数：仅写入 interrupt.requested 帧与 RunResponse.interrupt
    # 供前端呈现倒计时；后端不做超时自动续跑
    clarification_expires_seconds: int = Field(default=900, alias="CLARIFICATION_EXPIRES_SECONDS")

    # ===== 种子账号（仅 dev/staging 联调，prod 下脚本拒绝执行） =====
    seed_team_name: str = Field(default="默认团队", alias="SEED_TEAM_NAME")
    seed_user_email: str = Field(default="dev@example.com", alias="SEED_USER_EMAIL")
    seed_user_password: SecretStr = Field(default=SecretStr("Dev@123456"), alias="SEED_USER_PASSWORD")
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
