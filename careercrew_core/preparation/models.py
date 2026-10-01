"""Bounded preparation contracts. Identity always comes from authentication."""
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PreparationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and any(
            (ord(char) < 32 and char not in "\t\r\n")
            or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF)
            for char in value
        ):
            raise ValueError("文本包含不支持的控制字符")
        return value


class OpportunityInput(PreparationInput):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    jd: str = Field(min_length=1, max_length=30000)
    city: str = Field(default="", max_length=200)
    salary: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=100)
    url: str = Field(default="", max_length=2000)

    @field_validator("company", "title", "jd")
    @classmethod
    def required_text(cls, value):
        if not value.strip():
            raise ValueError("公司、岗位名称和岗位描述不能为空")
        return value

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or "\\" in value
                    or any(char.isspace() for char in value)):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("岗位链接必须是有效的 HTTP 或 HTTPS 地址") from None
        return value


class Opportunity(OpportunityInput):
    # Store rows arrive as plain JSON-safe dicts; response validation stays lax.
    model_config = ConfigDict(extra="forbid", strict=False)

    id: str
    created_at: datetime
    updated_at: datetime


class JdCaptureInput(PreparationInput):
    """JD 预取请求：只需要一个岗位链接（收藏前调用，此时还没有岗位记录）。"""

    url: str = Field(default="", max_length=2000)


class JdCaptureResult(BaseModel):
    """JD 采集结果：抓取失败不抛错，由 status 表达，message 可直接展示给用户。

    captured=已取到正文 / unsupported=来源不在可自动采集范围 /
    failed=采集失败（可稍后重试，或改为手动粘贴）。
    jd 仅在 captured 时非空；数据库对 jd 有 NOT NULL 约束，因此必须"先取到
    JD 再建岗位"，这也是本接口独立于创建接口的原因。
    """

    status: Literal["captured", "unsupported", "failed"]
    jd: str = ""
    message: str


class ResumeVersionInput(PreparationInput):
    label: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=50000)
    original_content: str = Field(default="", max_length=50000)

    @field_validator("label", "content")
    @classmethod
    def required_text(cls, value):
        if not value.strip():
            raise ValueError("版本名称和简历内容不能为空")
        return value


class ResumeVersion(ResumeVersionInput):
    model_config = ConfigDict(extra="forbid", strict=False)

    id: str
    opportunity_id: str
    created_at: datetime


class PreparationSessionInput(PreparationInput):
    module: Literal["resume", "interview"]
    resume_version_id: str = Field(min_length=1, max_length=100)


class PreparationSession(BaseModel):
    thread_id: str
    module: Literal["resume", "interview"]
    opportunity_id: str
    resume_version_id: str
    company: str
    title: str
    jd: str
    resume_content: str
    resume_label: str
