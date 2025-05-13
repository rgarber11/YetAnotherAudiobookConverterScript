from __future__ import annotations

from os import PathLike
from typing import cast

from .cue import Interpreter, Lark_StandAlone, Token, Tree, VisitError, v_args
from .models import Cuesheet, File, FileType, Track, TrackFlag, TrackType

lark_parser = Lark_StandAlone()


def cuetime_to_secs(value: str):
    comps = value.split(":")
    return int(comps[0], 10) * 60 + int(comps[1], 10) + int(comps[2], 10) / 75


def make_flag(value: str) -> TrackFlag:
    value = value.upper()
    if value == "DCP":
        return TrackFlag.DCP
    elif value == "4CH":
        return TrackFlag.FOURCH
    elif value == "PRE":
        return TrackFlag.PRE
    elif value == "SCMS":
        return TrackFlag.SCMS
    return TrackFlag.NONE


def make_track_type(value: str) -> TrackType:
    value = value.upper()
    if value == "AUDIO":
        return TrackType.AUDIO
    elif value == "CDG":
        return TrackType.CDG
    elif value == "MODE1/2048":
        return TrackType.MODE12048
    elif value == "MODE1/2352":
        return TrackType.MODE12352
    elif value == "MODE2/2336":
        return TrackType.MODE22336
    elif value == "MODE2/2352":
        return TrackType.MODE22352
    elif value == "CDI/2336":
        return TrackType.CDI2336
    elif value == "CDI/2352":
        return TrackType.CDI2352
    raise ValueError("Invalid Track Type")


def make_file_type(value: str) -> FileType:
    value = value.upper()
    if value == "WAVE":
        return FileType.WAVE
    elif value == "MP3":
        return FileType.MP3
    elif value == "AIFF":
        return FileType.AIFF
    elif value == "BINARY":
        return FileType.BINARY
    elif value == "MOTOROLA":
        return FileType.MOTOROLA
    raise ValueError("Invalid File Type")


def unquote(quote: Token) -> str:
    return str(quote[1:-1]) if quote.type == "QUOTED_STRING" else str(quote)


class CueInterpreter(Interpreter):
    @v_args(inline=True)
    def track_line(self, number: str, typer: str) -> tuple[int, TrackType]:
        return (int(number, 10), make_track_type(typer))

    @v_args(inline=True)
    def title_line(self, title: Token) -> str:
        return unquote(title)

    @v_args(inline=True)
    def performer_line(self, performer: Token) -> str:
        return unquote(performer)

    @v_args(inline=True)
    def catalog_line(self, catalog: Token) -> str:
        return unquote(catalog)

    @v_args(inline=True)
    def cdtextfile_line(self, cdtextfile: Token) -> str:
        return unquote(cdtextfile)

    @v_args(inline=True)
    def flag_line(self, flag: Token) -> TrackFlag:
        return make_flag(flag)

    @v_args(inline=True)
    def isrc_line(self, isrc: Token) -> str:
        return unquote(isrc)

    @v_args(inline=True)
    def file_line(self, name: Token, typer: str) -> tuple[str, FileType]:
        return (unquote(name), make_file_type(typer))

    @v_args(inline=True)
    def rem_line(self, k: Token, v: Token) -> tuple[str, str]:
        return (unquote(k), unquote(v))

    @v_args(inline=True)
    def index_line(self, number: Token, time: Token) -> tuple[int, float]:
        return (int(number, 10), cuetime_to_secs(time))

    @v_args(inline=True)
    def pregap_line(self, time: Token) -> float:
        return cuetime_to_secs(time)

    @v_args(inline=True)
    def postgap_line(self, time: Token) -> float:
        return cuetime_to_secs(time)

    @v_args(tree=True)
    def track(self, tree: Tree) -> Track:
        track_number, track_type = cast(
            tuple[int, TrackType], self.track_line(*tree.children[0].children)
        )
        title: str | None = None
        performer: str | None = None
        pregap: float | None = None
        postgap: float | None = None
        flags: TrackFlag = TrackFlag.NONE
        isrc: str | None = None
        rems: dict[str, list[str]] = {}
        indices: dict[int, float] = {}
        for child in tree.children[1:]:
            if child.data == "title_line":
                if title is not None:
                    raise VisitError("track", child, "Multiple titles cannot be given")
                title = cast(str, self.title_line(child.children[0]))
            elif child.data == "performer_line":
                if performer is not None:
                    raise VisitError(
                        "track", child, "Multiple performers cannot be given"
                    )
                performer = cast(str, self.performer_line(child.children[0]))
            elif child.data == "flag_line":
                flags = flags | cast(TrackFlag, self.flag_line(child.children[0]))
            elif child.data == "isrc_line":
                if isrc is not None:
                    raise VisitError("track", child, "Multiple isrc cannot be given")
                isrc = cast(str, self.isrc_line(child.children[0]))
            elif child.data == "rem_line":
                k, v = cast(tuple[str, str], self.rem_line(*child.children))
                if k not in rems:
                    rems[k] = []
                rems[k].append(v)
            elif child.data == "index_line":
                index, time = cast(tuple[int, float], self.index_line(*child.children))
                if index in indices:
                    raise ValueError(
                        "track",
                        child,
                        "Multiple indices with the same index cannot be given",
                    )
                indices[index] = time
            elif child.data == "postgap_line":
                if postgap is not None:
                    raise VisitError(
                        "track", child, "Multiple postgaps cannot be given"
                    )
                postgap = cast(float, self.postgap_line(child.children[0]))
            elif child.data == "pregap_line":
                if pregap is not None:
                    raise VisitError("track", child, "Multiple pregaps cannot be given")
                pregap = cast(float, self.pregap_line(child.children[0]))
        if 1 not in indices:
            raise VisitError("track", tree, "INDEX 01 ... Line needed for each track.")
        return Track(
            track_number,
            track_type,
            title,
            performer,
            isrc,
            rems,
            indices,
            pregap,
            postgap,
            flags,
        )

    def file(self, tree: Tree) -> File:
        file_name, file_type = cast(
            tuple[str, FileType], self.file_line(*tree.children[0].children)
        )
        rems: dict[str, list[str]] = {}
        performer = None
        title = None
        tracks: list[Track] = []
        for child in tree.children[1:]:
            if child.data == "track":
                tracks.append(cast(Track, self.track(child)))
                if len(tracks) > 2 and tracks[-1].number != tracks[-2].number + 1:
                    raise VisitError("file", child, "Track numbers should be in order")
            elif child.data == "rem_line":
                k, v = cast(tuple[str, str], self.rem_line(*child.children))
                if k not in rems:
                    rems[k] = []
                rems[k].append(v)
            elif child.data == "performer_line":
                if performer is not None:
                    raise VisitError(
                        "file", child, "Multiple performers cannot be given"
                    )
                performer = cast(str, self.visit(child.children[0]))
            elif child.data == "title_line":
                if title is not None:
                    raise VisitError("file", child, "Multiple titles cannot be given")
                title = cast(str, self.title_line(child.children[0]))
        if not tracks:
            raise VisitError("file", tree, "All files should contain tracks")
        return File(file_name, file_type, rems, performer, title, tracks)

    def cuesheet(self, tree: Tree) -> Cuesheet:
        catalog = None
        cdtextfile = None
        rems: dict[str, list[str]] = {}
        performer = None
        title = None
        files: list[File] = []
        for child in tree.children:
            if child.data == "catalog_line":
                if catalog is not None:
                    raise VisitError(
                        "cuesheet", child, "Multiple catalogs cannot be given"
                    )
                catalog = cast(str, self.catalog_line(child.children[0]))
            elif child.data == "cdtextfile_line":
                if cdtextfile is not None:
                    raise VisitError(
                        "cuesheet", child, "Mutlitple CD Text Files cannot be given"
                    )
                cdtextfile = cast(str, self.cdtextfile_line(child.children[0]))
            elif child.data == "rem_line":
                k, v = cast(tuple[str, str], self.rem_line(*child.children))
                if k not in rems:
                    rems[k] = []
                rems[k].append(v)
            elif child.data == "performer_line":
                if performer is not None:
                    raise VisitError(
                        "cuesheet", child, "Multiple performers cannot be given"
                    )
                performer = cast(str, self.performer_line(child.children[0]))
            elif child.data == "title_line":
                if title is not None:
                    raise VisitError(
                        "cuesheet", child, "Multiple titles cannot be given"
                    )
                title = cast(str, self.title_line(child.children[0]))
            elif child.data == "file":
                files.append(self.file(child))
                if (
                    len(files) > 1
                    and files[-1].tracks[0].number != files[-2].tracks[-1].number + 1
                ):
                    raise VisitError(
                        "cuesheet", child, "File tracks should be in order"
                    )
                elif files[0].tracks[0].number != 1:
                    raise VisitError(
                        "cuesheet", child, "First file should contain track 1"
                    )
        return Cuesheet(catalog, cdtextfile, rems, performer, title, files)

    @v_args(inline=True)
    def start(self, value: Tree) -> Cuesheet:
        return self.cuesheet(value)


def parse_cue_str(content: str) -> Cuesheet:
    return CueInterpreter().visit(lark_parser.parse(f"{content}\n", start="start"))


def parse_file_portion(content: str) -> File:
    return CueInterpreter().visit(
        lark_parser.parse(f"{content.lstrip()}\n", start="file")
    )


def parse_track(content: str) -> File:
    return CueInterpreter().visit(
        lark_parser.parse(f"{content.lstrip()}\n", start="track")
    )


def parse_cuefile(file_name: PathLike) -> Cuesheet:
    with open(file_name, "r") as f:
        return CueInterpreter().visit(lark_parser.parse(f"{f.read()}\n", start="start"))
