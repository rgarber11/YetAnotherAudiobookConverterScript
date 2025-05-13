import dataclasses
from enum import Enum, Flag, auto

__all__ = ["TrackType", "FileType", "TrackFlag", "Track", "Flag", "Cuesheet"]


class TrackType(Enum):
    AUDIO = auto()
    CDG = auto()
    MODE12048 = auto()
    MODE12352 = auto()
    MODE22336 = auto()
    MODE22352 = auto()
    CDI2336 = auto()
    CDI2352 = auto()


class FileType(Enum):
    WAVE = auto()
    MP3 = auto()
    AIFF = auto()
    BINARY = auto()
    MOTOROLA = auto()


class TrackFlag(Flag):
    NONE = 0
    DCP = auto()
    FOURCH = auto()
    PRE = auto()
    SCMS = auto()


@dataclasses.dataclass()
class Track:
    number: int
    track_type: TrackType
    title: str | None
    performer: str | None
    isrc: str | None
    rems: dict[str, list[str]]
    indices: dict[int, float]
    pregap: float | None
    postgap: float | None
    flags: TrackFlag = TrackFlag.NONE

    def get_title(self) -> str:
        if self.title is not None:
            return self.title
        return f"Chapter {self.number}"


@dataclasses.dataclass()
class File:
    filename: str
    file_type: FileType
    rems: dict[str, list[str]]
    performer: str | None
    title: str | None
    tracks: list[Track]


@dataclasses.dataclass()
class Cuesheet:
    catalog: str | None
    cdtextfile: str | None
    rems: dict[str, list[str]]
    performer: str | None
    title: str | None
    files: list[File]
