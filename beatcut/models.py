from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Marker(BaseModel):
    time: float = Field(ge=0, allow_inf_nan=False)
    enabled: bool = True
    forced: bool = False


class ClipChoice(BaseModel):
    id: str
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    conform: Literal["fit", "fill"] = "fill"


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    name: str = Field(default="我的卡点剪辑", min_length=1, max_length=120)
    music_id: str
    bpm: float | None = Field(default=None, ge=20, le=400, allow_inf_nan=False)
    markers: list[Marker] = Field(default_factory=list, max_length=30000)
    ranges: list[tuple[float, float]] = Field(default_factory=list, max_length=100)
    clips: list[ClipChoice] = Field(default_factory=list, max_length=500)
    first_id: str | None = None
    last_id: str | None = None
    interval: Literal[1, 2, 4, 8, 16] = 4
    pace: Literal["flat", "crescendo", "valley", "rise-valley"] = "flat"
    order: Literal["sequence", "shuffle"] = "sequence"
    seed: int = 1
    fps: Literal["24", "25", "30", "50", "60", "24000/1001", "30000/1001", "60000/1001"] = "25"
    width: int = Field(default=1920, ge=240, le=7680, multiple_of=2)
    height: int = Field(default=1080, ge=240, le=7680, multiple_of=2)
    mode: Literal["markers", "placeholders", "clips"] = "markers"


class ShortenRequest(BaseModel):
    project: Project
    target: float = Field(gt=1, le=7200, allow_inf_nan=False)
    tolerance: float = Field(default=2, ge=0, le=30, allow_inf_nan=False)
