"""模板数据结构。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProjectTemplate:
    """项目模板：定义研究目标 / 变量 / 报告大纲骨架。"""

    name: str
    description: str
    variables: list[dict[str, Any]] = field(default_factory=list)
    outline: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ReportTemplate:
    """报告模板：定义章节、引用样式、导出格式。"""

    name: str
    sections: list[dict[str, Any]] = field(default_factory=list)
    citation_style: str = "inline"