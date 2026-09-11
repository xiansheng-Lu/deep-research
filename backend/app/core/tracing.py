"""OpenTelemetry 追踪初始化。"""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import Settings


def configure_tracing(settings: Settings) -> None:
    """初始化全局 TracerProvider；追踪总开关关闭或 OTLP 端点未配置时退化为 NoOp。"""
    if not settings.otel_enabled:
        return
    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)

    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if endpoint:
        # M1 阶段再接入具体 Exporter；此处保留挂载位置
        provider.add_span_processor(BatchSpanProcessor(_build_otlp_exporter(endpoint)))

    trace.set_tracer_provider(provider)


def _build_otlp_exporter(endpoint: str):  # pragma: no cover - 留待 M1
    """构造 OTLP Exporter。M1 阶段根据端点协议选择 gRPC / HTTP。"""
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=endpoint, insecure=True)


def get_tracer(name: str):
    """获取命名 tracer，业务代码统一通过此接口获取。"""
    return trace.get_tracer(name)