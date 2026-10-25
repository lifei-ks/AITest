"""静态分析结果 dataclass：结构与 TASK_BOOK 4.2 / 6.1.1 严格对齐。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Endpoint:
    id: str
    method: str
    path: str
    controller: str
    methodName: str
    file: str
    line: int
    # 增量对比用的方法参数与鉴权注解（旧分析结果没有这两个字段时默认为空）
    params: list[dict] = field(default_factory=list)
    auth: list[str] = field(default_factory=list)


@dataclass
class FieldConstraint:
    field: str
    rule: str
    value: object = None


@dataclass
class EntityField:
    name: str
    type: str


@dataclass
class Entity:
    name: str
    file: str
    fields: list[EntityField] = field(default_factory=list)
    fieldConstraints: list[FieldConstraint] = field(default_factory=list)


@dataclass
class EnumValue:
    name: str
    label: str = ""


@dataclass
class EnumDef:
    name: str
    values: list[EnumValue] = field(default_factory=list)


@dataclass
class BusinessRule:
    id: str
    description: str
    guard: str
    file: str
    line: int


@dataclass
class BackendResult:
    language: str = "java-spring"
    endpoints: list[Endpoint] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    enums: list[EnumDef] = field(default_factory=list)
    businessRules: list[BusinessRule] = field(default_factory=list)
    uiTexts: list[str] = field(default_factory=list)


@dataclass
class FormField:
    label: str
    prop: str
    type: str
    required: bool = False
    placeholder: str = ""
    rules: list[str] = field(default_factory=list)


@dataclass
class Form:
    fields: list[FormField] = field(default_factory=list)


@dataclass
class Page:
    name: str
    route: str
    file: str
    forms: list[Form] = field(default_factory=list)


@dataclass
class Selector:
    type: str
    value: str
    page: str


@dataclass
class FrontendResult:
    language: str = "vue"
    pages: list[Page] = field(default_factory=list)
    selectors: list[Selector] = field(default_factory=list)
    uiTexts: list[str] = field(default_factory=list)
    components: list = field(default_factory=list)


def to_json_dict(obj) -> dict:
    return asdict(obj)
