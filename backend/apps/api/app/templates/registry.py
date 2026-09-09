"""模板注册表占位：M1 阶段从内置 JSON 加载。"""

from app.templates.base import ProjectTemplate, ReportTemplate


class TemplateRegistry:
    """模板注册表占位。"""

    def list_project_templates(self) -> list[ProjectTemplate]:
        return []

    def list_report_templates(self) -> list[ReportTemplate]:
        return []