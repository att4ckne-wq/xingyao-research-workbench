from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


QualityMode = Literal["quality", "balanced"]
JobState = Literal["queued", "running", "paused", "completed", "failed", "cancelled"]
ReuseStatus = Literal["safe_open", "attribution_required", "citation_only", "unknown"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    quality_mode: QualityMode = "quality"
    purpose: Literal["document", "research"] = "document"


class ProjectQualityModeUpdate(BaseModel):
    quality_mode: QualityMode


class Project(BaseModel):
    id: str
    name: str
    quality_mode: QualityMode
    status: str
    progress: int = 0
    created_at: datetime
    updated_at: datetime
    updated: datetime | None = None
    trashed_at: datetime | None = None


class Document(BaseModel):
    id: str
    project_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    parser: str
    language: str = "mixed"
    created_at: datetime


class Anchor(BaseModel):
    id: str
    project_id: str
    document_id: str
    section: str
    page: int | None = None
    paragraph_index: int
    text: str
    text_hash: str
    bbox: list[float] | None = None
    ocr_confidence: float | None = None
    ocr_rotation: int | None = None
    ocr_confirmed: bool = False
    sensitivity: list[str] = Field(default_factory=list)


class LicenseRecord(BaseModel):
    name: str
    url: str | None = None
    attribution_required: bool = False
    verified: bool = False
    restrictions: list[str] = Field(default_factory=list)
    verification_source: Literal["provider_metadata", "manual"] | None = None


class AssetCandidate(BaseModel):
    id: str
    project_id: str | None = None
    anchor_id: str | None = None
    title: str
    provider: str
    source_url: str
    preview_url: str | None = None
    author: str | None = None
    license: LicenseRecord
    accessed_at: datetime
    score: float
    reuse_status: ReuseStatus
    checksum: str | None = None
    rationale: str
    decision_action: str | None = None
    citation_action: str | None = None
    doi: str | None = None
    year: int | None = None
    journal: str | None = None
    verification_status: str | None = None


class DatasetProfile(BaseModel):
    id: str
    project_id: str
    filename: str
    rows: int
    columns: list[dict]
    recommended_charts: list[str]
    selected_sheet: str | None = None
    sheets: list[dict] = Field(default_factory=list)
    created_at: datetime | None = None


class ChartSpec(BaseModel):
    dataset_id: str
    sheet_name: str | None = Field(default=None, max_length=31)
    anchor_id: str | None = Field(default=None, max_length=64)
    chart_type: Literal["grouped_bar", "line", "scatter", "heatmap", "box"]
    x: str
    y: str
    group: str | None = None
    title: str = "Scientific figure"
    independent_unit: str | None = None
    error_bar: str | None = None
    statistical_test: str | None = None
    width_mm: float = Field(default=180, ge=60, le=300)
    height_mm: float = Field(default=110, ge=45, le=240)
    font_family: Literal["Arial", "Calibri", "Times New Roman", "Microsoft YaHei"] = "Arial"
    font_size_pt: float = Field(default=8, ge=5, le=20)
    title_size_pt: float = Field(default=11, ge=7, le=28)
    x_label: str | None = Field(default=None, max_length=160)
    y_label: str | None = Field(default=None, max_length=160)
    grid_mode: Literal["none", "x", "y", "both"] = "none"
    x_scale: Literal["linear", "log"] = "linear"
    y_scale: Literal["linear", "log"] = "linear"
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    tick_count: int = Field(default=6, ge=2, le=12)
    show_minor_ticks: bool = False
    legend_position: Literal["auto", "right", "left", "top", "bottom", "inside_upper_right", "none"] = "auto"
    palette: Literal["nature", "office", "colorblind", "grayscale"] = "nature"
    line_width: float = Field(default=1.5, ge=0.5, le=4)
    marker: Literal["auto", "circle", "square", "triangle", "none"] = "auto"
    show_data_labels: bool = False

    @model_validator(mode="after")
    def validate_axis_ranges(self) -> "ChartSpec":
        for name, lower, upper in (("x", self.x_min, self.x_max), ("y", self.y_min, self.y_max)):
            if lower is not None and upper is not None and lower >= upper:
                raise ValueError(f"{name}_min must be smaller than {name}_max")
        if self.x_scale == "log" and self.x_min is not None and self.x_min <= 0:
            raise ValueError("x_min must be positive for a log scale")
        if self.y_scale == "log" and self.y_min is not None and self.y_min <= 0:
            raise ValueError("y_min must be positive for a log scale")
        return self


class FigurePanelSpec(BaseModel):
    kind: Literal["asset", "chart"]
    source_id: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=8)
    crop_zoom: float = Field(default=1.0, ge=1.0, le=3.0)


class FigureComposition(BaseModel):
    anchor_id: str | None = Field(default=None, max_length=64)
    caption: str = Field(default="", max_length=2000)
    template: Literal["1x2", "2x2", "hero_side", "vertical"] = "2x2"
    asset_ids: list[str] = Field(default_factory=list)
    chart_ids: list[str] = Field(default_factory=list)
    panels: list[FigurePanelSpec] = Field(default_factory=list, max_length=6)
    labels: list[str] = Field(default_factory=list)
    gap_px: int = Field(default=8, ge=0, le=48)
    aspect_ratio: Literal["auto", "1:1", "4:3", "16:9"] = "auto"
    width_mm: Literal[89, 180] = 180
    dpi: Literal[300, 600] = 600


class Job(BaseModel):
    id: str
    project_id: str
    kind: str
    state: JobState
    stage: str
    progress: int
    message: str | None = None
    project_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DecisionRecord(BaseModel):
    id: str
    project_id: str
    target_type: str
    target_id: str
    action: str
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class Checkpoint(BaseModel):
    id: str
    project_id: str
    label: str
    manifest_path: str
    created_at: datetime


class ExportRequest(BaseModel):
    override_statistics: bool = False
    override_reason: str | None = None

    @model_validator(mode="after")
    def require_override_reason(self) -> "ExportRequest":
        if self.override_statistics and len((self.override_reason or "").strip()) < 5:
            raise ValueError("强制导出必须填写至少 5 个字符的风险确认理由")
        return self


class ExportManifest(BaseModel):
    id: str
    project_id: str
    archive_path: str
    formats: list[str]
    warnings: list[str]
    created_at: datetime
    download_url: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=240)


class DecisionCreate(BaseModel):
    project_id: str
    target_type: str
    target_id: str
    action: Literal["accept", "reject", "override", "restore"]
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BulkAssetRejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class JobAction(BaseModel):
    action: Literal["pause", "resume", "cancel", "retry"]


class ModelSettings(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


class ModelProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["local", "online"] = "local"
    base_url: str = Field(min_length=8, max_length=1000)
    model: str = Field(min_length=1, max_length=240)
    api_key: str | None = Field(default=None, max_length=2000)


class AccountLogin(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class AccountPreferencesUpdate(BaseModel):
    annotations_enabled: bool | None = None
    last_screen: Literal["home", "workspace", "library", "trash", "settings", "paper"] | None = None


class AccountPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class SuggestionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    section: str = ""
    send_redacted: bool = True
    confirmed: bool = False


class ProjectRootSettings(BaseModel):
    path: str = Field(min_length=3, max_length=500)
    migrate_existing: bool = True
    confirmed: bool = False


class ReferenceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    source_url: str = Field(min_length=1, max_length=2000)
    author: str | None = Field(default=None, max_length=500)


class AssetUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    author: str | None = Field(default=None, max_length=500)
    license_name: str | None = Field(default=None, max_length=240)
    license_url: str | None = Field(default=None, max_length=2000)
    reuse_status: ReuseStatus | None = None
    license_verified: bool = False
    reason: str = Field(min_length=3, max_length=1000)


class AssistantChatRequest(BaseModel):
    task: Literal["topic", "outline", "writing", "review", "reading", "translation", "diagram"] = "review"
    selection: str | None = Field(default=None, max_length=50000)
    topic: str | None = Field(default=None, max_length=1000)
    preview_hash: str | None = Field(default=None, max_length=128)
    project_id: str
    anchor_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    send_redacted: bool = True
    use_cloud: bool = False
    confirmed: bool = False


class AnchorUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    reason: str = Field(min_length=3, max_length=1000)


class OCRConfirmation(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=50000)
    reason: str = Field(default="人工核对 OCR 文本", min_length=3, max_length=1000)


class AnchorContentUpdate(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=50000)


class DocumentContentUpdate(BaseModel):
    document_id: str | None = None
    anchors: list[AnchorContentUpdate] = Field(min_length=1, max_length=10000)
    reason: str = Field(min_length=3, max_length=1000)


class AcademicDraftRequest(BaseModel):
    title: str = Field(default="学术工作稿", min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=200000)
    format: Literal["md", "docx", "pdf"] = "md"


class AcademicDiagramRequest(BaseModel):
    title: str = Field(default="研究图示", min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=12000)


class WorkingDraftRequest(BaseModel):
    content: str = Field(max_length=200000)
    expected_version: int = Field(ge=0, strict=True)


class AnalyzeDocumentRequest(BaseModel):
    document_id: str | None = None

class ManuscriptSelection(BaseModel):
    document_id: str

class CitationPlacementRequest(BaseModel):
    anchor_id: str
    reference_id: str
    action: Literal["accept", "reject"]
