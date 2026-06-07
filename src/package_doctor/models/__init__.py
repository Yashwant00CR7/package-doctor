from typing import Optional
from pydantic import BaseModel, Field


class PackageInfo(BaseModel):
    name: str
    latest_version: Optional[str] = None
    installed_version: Optional[str] = None
    status: str = "active"  # active | deprecated | abandoned
    deprecation_message: Optional[str] = None
    alternative: Optional[str] = None
    python_requires: Optional[str] = None
    last_release_date: Optional[str] = None
    migration_notes: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class ConflictEntry(BaseModel):
    package: str
    required_spec: str
    installed_version: str
    required_by: str
    severity: str = "error"  # error | warning


class VenvHealth(BaseModel):
    total_packages: int
    conflicts: list[ConflictEntry] = Field(default_factory=list)
    deprecated_packages: list[PackageInfo] = Field(default_factory=list)
    ranked_fix_commands: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PreInstallResult(BaseModel):
    package_name: str
    would_conflict: bool
    conflicting_packages: list[ConflictEntry] = Field(default_factory=list)
    resolution_commands: list[str] = Field(default_factory=list)
    package_info: Optional[PackageInfo] = None
    warnings: list[str] = Field(default_factory=list)


class ModelStatus(BaseModel):
    model_id: str
    provider: Optional[str] = None
    status: str  # current | warning | error
    eol_date: Optional[str] = None
    successor_model: Optional[str] = None
    last_checked: str
    source_url: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
