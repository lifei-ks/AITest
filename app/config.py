"""全局配置：pydantic-settings，从 .env 与环境变量读取。

决策：database_url / output_dir 默认值基于项目根目录的绝对路径。
理由：TASK_BOOK 10.2 已知坑——SQLite 相对路径随工作目录漂移导致"重启后数据丢了"。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AICase Core"

    # 数据库：默认 SQLite 绝对路径，可用环境变量切 MySQL/临时库
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'aicase.db').as_posix()}"

    # 执行证据输出目录
    output_dir: str = str(BASE_DIR / "outputs")

    # 静态分析引擎：ast（默认，失败自动回退 regex） / regex
    analyzer_engine: str = "ast"

    # 视觉审核图片预算：普通场景默认 6 张，有失败步骤时放宽
    vision_max_images: int = 6
    vision_max_images_failed: int = 10
    vision_two_pass_review: bool = True

    # 文本 LLM（需求测试点/用例措辞）
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # 多模态 LLM（执行截图审核）；任一项为空则回退到文本 LLM 的对应配置
    llm_vision_api_key: str = ""
    llm_vision_base_url: str = ""
    llm_vision_model: str = ""

    # 被测站点默认地址（演示靶站）
    target_url_default: str = "http://127.0.0.1:8200"

    # 预留：本项目不做用户体系（TASK_BOOK 2.2 明确 OUT），字段仅为结构统一
    auth_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """进程内单例。测试若需注入 DATABASE_URL，须在首次 import app 模块前设置环境变量。"""
    return Settings()
