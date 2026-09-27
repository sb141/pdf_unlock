from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, FiniteFloat


class FileStatus(str, Enum):
    success = "success"
    failed = "failed"


class FileResult(BaseModel):
    original_name: str
    status: FileStatus
    error_code: Optional[str] = None
    download_token: Optional[str] = None


class Summary(BaseModel):
    total: int
    succeeded: int
    failed: int


class UnlockResponse(BaseModel):
    request_id: str
    summary: Summary
    results: list[FileResult]
    batch_download_available: bool = False


class PageSize(BaseModel):
    width: float
    height: float


class EditSessionResponse(BaseModel):
    request_id: str
    source_token: str
    pages: list[PageSize]


class Redaction(BaseModel):
    page: int = Field(ge=0)
    x0: FiniteFloat
    y0: FiniteFloat
    x1: FiniteFloat
    y1: FiniteFloat


PdfFontName = Literal["helv", "hebo", "heit", "hebi", "tiro", "tibo", "tiit", "tibi", "cour", "cobo", "coit", "cobi"]


class AddedText(BaseModel):
    page: int = Field(ge=0)
    x: FiniteFloat
    y: FiniteFloat
    text: str = Field(min_length=1, max_length=500)
    font_size: FiniteFloat = Field(ge=6, le=72)
    font_name: PdfFontName = "helv"
    color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")


class TextReplacement(BaseModel):
    page: int = Field(ge=0)
    span_id: int = Field(ge=0)
    text: str = Field(max_length=500)
    font_size: FiniteFloat | None = Field(default=None, ge=6, le=72)
    font_name: PdfFontName | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class TextSpanInfo(BaseModel):
    span_id: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    origin_x: float
    origin_y: float
    font_size: float
    font_name: PdfFontName
    color: str


class EditRequest(BaseModel):
    redactions: list[Redaction] = Field(default_factory=list, max_length=100)
    texts: list[AddedText] = Field(default_factory=list, max_length=100)
    replacements: list[TextReplacement] = Field(default_factory=list, max_length=100)


class EditResponse(BaseModel):
    request_id: str
    download_token: str
