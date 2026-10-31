"""实验记录 - 领域常量与展示配置。"""

from __future__ import annotations

# 内置事件类型（名称, 颜色）。用户可在界面中追加自定义类型；内置类型不可删除。
BUILTIN_EVENT_TYPES: list[tuple[str, str]] = [
    ("方案设计", "#4C78A8"),
    ("样品制备", "#72B7B2"),
    ("试验执行", "#54A24B"),
    ("异常故障", "#E45756"),
    ("数据分析", "#EECA3B"),
    ("会议讨论", "#B279A2"),
    ("设备维护", "#FF9DA6"),
    ("其他", "#9D9D9D"),
]

PROJECT_STATUS: tuple[str, ...] = ("进行中", "已暂停", "已结题")

# 结构化记录字段（数据库列名, 中文标签）
RECORD_FIELDS: tuple[tuple[str, str], ...] = (
    ("purpose", "目的"),
    ("conditions", "条件参数"),
    ("steps", "步骤"),
    ("result", "结果"),
    ("conclusion", "结论"),
)

# 记录正文的自由补充字段（Markdown）
CONTENT_FIELD = "content"

# 记录发生时间的展示格式（本地时间，字典序即时间序）。
# 注意：Qt 格式占位符区分大小写，必须用小写 yyyy/dd，大写会被当作字面文字。
OCCURRED_FORMAT = "yyyy-MM-dd HH:mm"
