import argparse
import dataclasses
import pathlib
from enum import Enum, auto


@dataclasses.dataclass
class Chapter:
    title: str
    duration: float


@dataclasses.dataclass
class DiscoveredMetadata:
    title: str
    artist: str
    performer: str
    publisher: str
    genre: str
    date: str


@dataclasses.dataclass
class FileInfo:
    filename: pathlib.Path
    performer: str
    cuesheet: str
    chapters: list[Chapter]
    bit_rate: int
    title: str
    album: str
    genre: str
    date: str
    publisher: str
    track: int | None
    disc: int | None
    duration: float
    artist: str
    cover_codec: str


@dataclasses.dataclass(frozen=True)
class DispatchArgs:
    media_locations: list[pathlib.Path]
    metadata_file: pathlib.Path | None
    cuesheet: pathlib.Path | None
    cover_image: pathlib.Path | None
    auto_chapters: bool
    output_file: pathlib.Path
    bitrate: str | None
    delete_originals: bool


class CoverStatus(Enum):
    NONE_FOUND = auto()
    ATTACHMENT_FAILED = auto()
    SUCCESS = auto()


class CommandParserArgs(argparse.Namespace):
    input: list[str]
    auto: list[str]
    delete: bool
    output: str
    metadata: str
    metadatachapter: str
    bitrate: str
    cuesheet: str
    cover: str
