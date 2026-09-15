"""报告结构化领域包（M2-7 数据点级溯源）。

- ``schemas``：Reporter LLM 结构化输出的内部 Pydantic 模型；
- ``disputes``：分歧选择与双方口径文本的确定性原语（reporter Markdown 渲染与
  blocks 绑定引擎共用，禁止两处各写一份）；
- ``blocks``：blocks 计划校验、marker 分配、snippet 原文填充、缺源降级/数字
  断言审计、dispute 注入、outline 派生与引文关系行展开的纯函数引擎。
"""
