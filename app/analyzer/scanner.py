"""文件收集 + 技术栈识别（纯规则）。

- 递归收集源码文件，排除构建产物与依赖目录
- 技术栈判定：有 pom.xml / build.gradle → java-spring；有 package.json 且含 .vue → vue
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

EXCLUDE_DIRS = {".git", "node_modules", "target", "build", "dist", ".venv", "__pycache__", ".idea", "outputs"}

# 需要参与解析的扩展名
JAVA_EXTS = {".java"}
VUE_EXTS = {".vue", ".js"}


@dataclass
class ScanResult:
    root: str
    files: list[str] = field(default_factory=list)
    backend: str | None = None  # java-spring
    frontend: str | None = None  # vue
    has_pom: bool = False
    has_gradle: bool = False
    has_package_json: bool = False
    has_vue: bool = False

    @property
    def tech_stack(self) -> dict:
        return {"backend": self.backend, "frontend": self.frontend}


def scan(root: str) -> ScanResult:
    result = ScanResult(root=os.path.abspath(root))
    if not os.path.isdir(root):
        return result

    for dirpath, dirnames, filenames in os.walk(root):
        # 就地过滤，避免进入构建/依赖目录
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn == "pom.xml":
                result.has_pom = True
            elif fn == "build.gradle" or fn == "build.gradle.kts":
                result.has_gradle = True
            elif fn == "package.json":
                result.has_package_json = True
            elif fn.endswith(".vue"):
                result.has_vue = True

            full = os.path.join(dirpath, fn)
            result.files.append(os.path.relpath(full, root))

    if result.has_pom or result.has_gradle:
        result.backend = "java-spring"
    if result.has_package_json and result.has_vue:
        result.frontend = "vue"

    return result


def list_by_ext(result: ScanResult, exts: set[str]) -> list[str]:
    """返回绝对路径列表，仅保留指定扩展名的文件。"""
    return [
        os.path.join(result.root, f)
        for f in result.files
        if os.path.splitext(f)[1].lower() in exts
    ]
