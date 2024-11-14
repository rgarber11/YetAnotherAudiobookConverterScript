#!/bin/env python
from __future__ import annotations

import argparse
import dataclasses
import json
import multiprocessing
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

VERSION = "0.2.0"
audio_files = ("mp3", "m4a", "m4b", "ogg", "flac", "wav", "aiff")
image_files = ("jpg", "png", "tiff", "jpeg")


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


# The command parser is recursive. This makes that possible
class CustomPrintingArgParse(argparse.ArgumentParser):
    def print_help(self, file=None):
        if not file:
            file = sys.stdout
        if hasattr(self, "modded_help_output"):
            _ = file.write(self.modded_help_output)
        else:
            super().print_help(file)


def get_metadata(music_file: pathlib.Path) -> dict[str, Any]:
    json_string = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-of",
            "json",
            "-show_entries",
            "stream:format",
            "-show_chapters",
            str(music_file),
        ],
        capture_output=True,
    ).stdout.decode("utf-8")
    metadata = json.loads(json_string)
    if music_file.suffix == ".opus":  # FFMpeg maps opus tags wrong (11/13/24)
        for k, v in metadata["streams"][0]["tags"]:
            metadata["format"]["tags"][k] = v
    return metadata


def add_cue(
    music_file: pathlib.Path, cue_file: pathlib.Path, temp_directory: pathlib.Path
) -> pathlib.Path | None:
    actual_file = temp_directory.joinpath(f"{music_file.stem}.mka")
    temp_file = temp_directory.joinpath(f"{music_file.stem}.temp.mka")
    temporary = subprocess.run(
        ["mkvmerge", str(music_file), "--chapters", str(cue_file), "-o", str(temp_file)]
    )
    if temporary.returncode != 0:
        return None
    # FFMpeg does not read chapters correctly when reencoding to opus from mka
    final = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-i",
            str(temp_file),
            "-i",
            str(music_file),
            "-map",
            "0",
            "-map_chapters",
            "0",
            "-map_metadata",
            "1",
            "-c",
            "copy",
            str(actual_file),
        ]
    )
    temp_file.unlink()
    if final.returncode != 0:
        return None
    return actual_file


def get_performer(metadata: Any) -> str:  # Most formats don't have a performer tag
    if "performer" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["performer"]
    if "narratedby" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["narratedby"]
    if "album_artist" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["album_artist"]
    if "composer" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["composer"]
    return ""


def final_conversion(
    init_file: pathlib.Path,
    output_file: pathlib.Path,
    metadata_file: pathlib.Path | None,
    auto_chapters: bool,
    bitrate: str,
    performer: str,
) -> bool:
    args = ["ffmpeg", "-v", "quiet", "-i", str(init_file)]
    if metadata_file:
        args.extend(
            ["-f", "ffmetadata", "-i", str(metadata_file), "-map_metadata", "1"]
        )
        if auto_chapters:
            args.extend(["-map_chapters", "0"])
        else:
            args.extend(["-map_chapters", "1"])
    else:
        args.extend(["-map_metadata", "0", "-map_chapters", "0"])
        if performer:
            args.extend(["-metadata", f"performer={performer}"])
    if init_file.suffix != ".opus" and bitrate == "-1":
        args.extend(["-c", "copy", str(output_file)])
    else:
        args.extend(
            [
                "-c:v",
                "libopus",
                "-b:v",
                bitrate.replace("|", ""),
                "-vbr",
                "on",
                "-compression_level",
                "10",
                "-application",
                "voip",
                str(output_file),
            ]
        )
    conversion = subprocess.run(args)
    return conversion.returncode == 0


# FFMPEG cannot map covers to opus (11/13/24)
def attach_image(output_file: pathlib.Path, cover_image: pathlib.Path) -> bool:
    attachment = subprocess.run(
        ["opustags", str(output_file), "-i", "--set_cover", str(cover_image)]
    )
    return attachment.returncode == 0


def extract_embedded_image(
    media_file: pathlib.Path, temp_dir: pathlib.Path, codec: str
) -> pathlib.Path | None:
    outfile = temp_dir.joinpath(f"{media_file.stem}.{codec}")
    extraction = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-i",
            str(media_file),
            "-map",
            "0:v:0",
            "-vcodec",
            "copy",
            "outfile",
        ]
    )
    if extraction.returncode != 0:
        return None
    return outfile


def generate_chapters_for_folder(
    file_metadata: list[Any], chapter_file: pathlib.Path
) -> pathlib.Path:
    chapters: list[Chapter] = []
    for file in file_metadata:
        if "title" in file["format"]["tags"]:
            chapters.append(
                Chapter(
                    file["format"]["tags"]["title"],
                    float(file["format"]["duration"]) * 1000,
                )
            )
        else:
            chapters.append(
                Chapter(
                    pathlib.Path(file["format"]["filename"]).stem,
                    float(file["format"]["duration"]) * 1000,
                )
            )
    with chapter_file.open("w+") as chapterIO:
        _ = chapterIO.write(";FFMETADATA1\n")
        duration = 0.0
        for chapter in chapters:
            _ = chapterIO.write(
                f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={duration}\nEND={duration + chapter.duration}\ntitle={chapter.title}\n"
            )
            duration += chapter.duration
    return chapter_file


def generate_metadata_for_folder(
    file_metadata: list[Any], metadata_file: pathlib.Path
) -> pathlib.Path:
    metadata = DiscoveredMetadata("", "", "", "", "", "")
    for file in file_metadata:
        if not metadata.title and "album" in file["format"]["tags"]:
            metadata.title = file["format"]["tags"]["album"]
        if not metadata.artist and "artist" in file["format"]["tags"]:
            metadata.artist = file["format"]["tags"]["artist"]
        if not metadata.performer:
            metadata.performer = get_performer(file)
        if not metadata.genre and "genre" in file["format"]["tags"]:
            metadata.genre = file["format"]["tags"]["genre"]
        if not metadata.date and "date" in file["format"]["tags"]:
            metadata.date = file["format"]["tags"]["date"]
        if not metadata.publisher and "publisher" in file["format"]["tags"]:
            metadata.publisher = file["format"]["tags"]["publisher"]
    with metadata_file.open("w+") as metadataIO:
        metadataIO.write(";FFMETADATA1\n")
        if metadata.title:
            metadataIO.write(f"title={metadata.title}\n")
        if metadata.artist:
            metadataIO.write(f"artist={metadata.artist}\n")
        if metadata.performer:
            metadataIO.write(f"performer={metadata.performer}\n")
        if metadata.genre:
            metadataIO.write(f"genre={metadata.genre}\n")
        if metadata.date:
            metadataIO.write(f"date={metadata.date}\n")
        if metadata.publisher:
            metadataIO.write(f"publisher={metadata.publisher}\n")
    return metadata_file


def merge_together(
    file_metadata: list[Any],
    metadata_file: pathlib.Path | None,
    auto_chapters: bool,
    output_file: pathlib.Path,
    bitrate: str,
    temp_dir: pathlib.Path,
) -> bool:
    if not metadata_file:
        metadata_file = generate_metadata_for_folder(
            file_metadata,
            temp_dir.joinpath(f"{file_metadata[0]["format"]["filename"].stem}.ffmeta"),
        )
    auto_bitrate = False
    if bitrate[-1] == "|":
        bitrate = bitrate[:-1]
        auto_bitrate = True
    all_same_suffix = True
    first_suffix = file_metadata[0]["format"]["filename"].suffix
    for file in file_metadata:
        if file["format"]["filename"].suffix != first_suffix:
            all_same_suffix = False

    chapter_file = (
        generate_chapters_for_folder(
            file_metadata,
            temp_dir.joinpath(
                f"{file_metadata[0]["format"]["filename"].stem}_chapters.ffmeta"
            ),
        )
        if auto_chapters
        else None
    )
    args = ["ffmpeg", "-v", "quiet", "-y"]
    if all_same_suffix:
        concat_filename = temp_dir.joinpath(
            f"{file_metadata[0]["format"]["filename"].stem}.files"
        )
        with concat_filename.open("w+") as concat_list:
            concat_list.writelines(
                f"file '{file["format"]["filename"]}'\n" for file in file_metadata
            )
        args.extend(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_filename),
                "-f",
                "ffmetadata",
                "-i",
                str(metadata_file),
            ]
        )
        if auto_chapters:
            args.extend(
                [
                    "-f",
                    "ffmetadata",
                    "-i",
                    str(chapter_file),
                    "-map_metadata",
                    "1",
                    "-map_chapters",
                    "2",
                ]
            )
        else:
            args.extend(["-map_metadata", "1", "-map_chapters", "1"])
        # If auto, preserve bitrate
        if auto_bitrate and first_suffix == ".opus":
            args.extend(["-c:a", "copy", str(output_file)])
        else:
            args.extend(
                [
                    "-c:a",
                    "libopus",
                    "-b:a",
                    bitrate,
                    "-vbr",
                    "on",
                    "-compression_level",
                    "10",
                    "-application",
                    "voip",
                    str(output_file),
                ]
            )
    else:
        for file in file_metadata:
            args.extend(["-i", file["format"]["filename"]])
        args.extend(["-f", "ffmetadata", "-i", str(metadata_file)])
        if auto_chapters:
            args.extend(["-f", "ffmetadata", "-i", str(chapter_file)])
        # If there are heterogeneous inputs, a filter is the only way to concatenate
        args.extend(
            [
                "-filter_complex",
                f'{"".join(f"[{i}:a:0]" for i, _ in enumerate(file_metadata))}concat={len(file_metadata)}:v=0:a=1[outa]',
                "-map",
                "[outa]",
                "-map_metadata",
                str(len(file_metadata)),
                "-map_chapters",
                (
                    str(len(file_metadata) + 1)
                    if auto_chapters
                    else str(len(file_metadata))
                ),
                "-c:a",
                "libopus",
                "-b:a",
                bitrate,
                "-vbr",
                "on",
                "-compression_level",
                "10",
                "-application",
                "voip",
                str(output_file),
            ]
        )
    # print(args)
    merger = subprocess.run(args)
    return merger.returncode == 0


# Auto-detection recursion
def get_folders_of_files(media_location: pathlib.Path) -> list[pathlib.Path]:
    if all(not loc.is_dir() for loc in media_location.iterdir()):
        if not list(loc.suffix[1:] in audio_files for loc in media_location.iterdir()):
            return []
        return [media_location]
    dirs = (loc for loc in media_location.iterdir() if loc.is_dir())
    ans: list[pathlib.Path] = []
    for dir in dirs:
        ans.extend(get_folders_of_files(dir))
    return ans


def resolve_automatic_conversion(
    media_location: pathlib.Path, bitrate: str | None, delete_originals: bool
) -> list[DispatchArgs]:
    ans = []
    for folder in get_folders_of_files(media_location):
        output_file = folder.joinpath(f"{folder.stem}.opus").expanduser().resolve()
        if output_file.exists():
            x = input("File {output_file} exists: Overwrite? (y/N): ")
            if x not in {"y", "Y"}:
                sys.exit(1)
        ans.append(
            DispatchArgs(
                [folder],
                None,
                None,
                None,
                True,
                output_file,
                bitrate,
                delete_originals,
            )
        )
    return ans


def flatten_manual_query(media_locations: list[pathlib.Path]) -> list[pathlib.Path]:
    delete_idxs = [1]
    while len(delete_idxs) > 0:
        delete_idxs = []
        size = len(media_locations)
        for i in range(size):
            if media_locations[i].is_dir():
                for suffix in audio_files:
                    media_locations.extend(media_locations[i].glob(f"*.{suffix}"))
                delete_idxs.append(i)
                media_locations.extend(
                    loc for loc in media_locations[i].iterdir() if loc.is_dir()
                )
        for idx in delete_idxs:
            del media_locations[idx]
    return media_locations


def discover_cover_image(
    file_metadata: list[Any], temp_dir_path: pathlib.Path
) -> pathlib.Path | None:
    for file in file_metadata:
        if any(stream["codec_type"] == "video" for stream in file["streams"]):
            codec = next(
                stream["codec_name"]
                for stream in file["streams"]
                if stream["codec_type"] == "video"
            )
            return extract_embedded_image(
                file["format"]["filename"], temp_dir_path, codec
            )
    for file in file_metadata:
        images: list[pathlib.Path] = []
        for suffix in image_files:
            images.extend(
                img for img in file["format"]["filename"].parent.glob(f"*.{suffix}")
            )
        if images:
            return images[0]
    return None


def prepare_file_metadata(media_locations: list[pathlib.Path]) -> list[Any]:
    file_metadata = [get_metadata(file) for file in media_locations]
    for file in file_metadata:
        file["format"]["filename"] = (
            pathlib.Path(file["format"]["filename"]).expanduser().resolve()
        )
    if all("track" in meta["format"]["tags"] for meta in file_metadata):
        file_metadata.sort(
            key=lambda x: (
                (1, int(x["format"]["tags"]["track"]))
                if not "disc_number" in x["format"]["tags"]
                else (
                    int(x["format"]["tags"]["disc_number"]),
                    int(x["format"]["tags"]["track"]),
                )
            )
        )
    else:
        file_metadata.sort(key=lambda x: x["format"]["filename"].stem)
    return file_metadata


def dispatch_conversion(args: DispatchArgs) -> tuple[str, bool]:
    media_locations = flatten_manual_query(args.media_locations)
    metadata_file = args.metadata_file
    cuesheet = args.cuesheet
    cover_image = args.cover_image
    auto_chapters = args.auto_chapters
    output_file = args.output_file
    bitrate = args.bitrate
    delete_originals = args.delete_originals
    file_metadata = prepare_file_metadata(media_locations)
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = pathlib.Path(temp_dir).expanduser().resolve()
        if not bitrate:
            raw_bitrate = max(int(file["format"]["bit_rate"]) for file in file_metadata)
            if raw_bitrate >= 262144:
                bitrate = "192k|"
            else:
                bitrate = "32k|"
        cover_image = (
            cover_image
            if cover_image
            else discover_cover_image(file_metadata, temp_dir_path)
        )
        success = False
        if len(media_locations) > 1:
            if cuesheet:
                print("Error! Cannot have a singular cuesheet with multiple files")
                return (
                    ",".join(
                        media_location.name for media_location in args.media_locations
                    ),
                    False,
                )
            success = merge_together(
                file_metadata,
                metadata_file,
                auto_chapters,
                output_file,
                bitrate,
                temp_dir_path,
            )
        else:
            performer = get_performer(file_metadata[0])
            input_file = media_locations[0]
            if cuesheet:
                input_file = add_cue(input_file, cuesheet, temp_dir_path)
                if not input_file:
                    print("Could not attach chapters")
                    return (
                        ",".join(
                            media_location.name
                            for media_location in args.media_locations
                        ),
                        False,
                    )
            elif not auto_chapters or file_metadata[0]["chapters"]:
                success = final_conversion(
                    input_file,
                    output_file,
                    metadata_file,
                    auto_chapters,
                    bitrate,
                    performer,
                )
            else:
                cue_sheet_text = None
                if file_metadata[0]["format"]["tags"]["CUESHEET"]:
                    cue_sheet_text = file_metadata[0]["format"]["CUESHEET"]
                if file_metadata[0]["format"]["tags"]["cuesheet"]:
                    cue_sheet_text = file_metadata[0]["format"]["cuesheet"]
                temp_cue_file = temp_dir_path.joinpath(f"{input_file.stem}.cue")
                if cue_sheet_text:
                    with temp_cue_file.open("w+") as temp_cue:
                        _ = temp_cue.write(cue_sheet_text)
                    input_file = add_cue(input_file, temp_cue_file, temp_dir_path)
                    if not input_file:
                        print("Could not add chapters to input.")
                        return (
                            ",".join(
                                media_location.name
                                for media_location in args.media_locations
                            ),
                            False,
                        )
                if not cue_sheet_text:
                    print("Warning: No Chapters Found")
                success = final_conversion(
                    input_file,
                    output_file,
                    metadata_file,
                    auto_chapters,
                    bitrate,
                    performer,
                )

    if cover_image:
        attach_image(output_file, cover_image)
    if success and delete_originals:
        for loc in media_locations:
            loc.unlink()
    if not success:
        return (
            ",".join(media_location.name for media_location in args.media_locations),
            False,
        )
    return output_file.name, True


def validate_inputs(inputs: list[argparse.Namespace]) -> list[DispatchArgs]:
    ans: list[DispatchArgs] = []
    for namespace in inputs:
        if namespace.bitrate:
            if not re.match(r"\d+[kKmM]?", namespace.bitrate):
                print("Error: Invalid Bitrate")
                sys.exit(1)
        if namespace.input:
            if namespace.output:
                output_file = pathlib.Path(namespace.output).expanduser().resolve()
                if output_file.exists():
                    x = input("File {output_file} exists: Overwrite? (y/N): ")
                    if x not in {"y", "Y"}:
                        sys.exit(1)
            else:
                first_input = pathlib.Path(namespace.input[0])
                output_file = (
                    first_input.parent.joinpath(f"{first_input.stem}.opus")
                    .expanduser()
                    .resolve()
                )
                if output_file.exists():
                    x = input("File {output_file} exists: Overwrite? (y/N): ")
                    if x not in {"y", "Y"}:
                        sys.exit(1)
            metadata = None
            auto_chapters = True
            if namespace.metadata:
                metadata = pathlib.Path(namespace.metadata).expanduser().resolve()
            elif namespace.metadatachapter:
                metadata = (
                    pathlib.Path(namespace.metadatachapter).expanduser().resolve()
                )
                auto_chapters = False
            cuesheet = None
            if namespace.cuesheet:
                cuesheet = pathlib.Path(namespace.cuesheet).expanduser().resolve()
            cover_image = None
            if namespace.cover:
                cover_image = pathlib.Path(namespace.cover).expanduser().resolve()
            ans.append(
                DispatchArgs(
                    [pathlib.Path(f).expanduser().resolve() for f in namespace.input],
                    metadata,
                    cuesheet,
                    cover_image,
                    auto_chapters,
                    output_file,
                    namespace.bitrate,
                    namespace.delete,
                )
            )

        else:
            if (
                namespace.metadata
                or namespace.metadatachapter
                or namespace.cuesheet
                or namespace.cover
                or namespace.output
            ):
                print("Error: Cannot set covers/metadata in auto mode")
                sys.exit(1)
            for inner in namespace.auto:
                ans.extend(
                    resolve_automatic_conversion(
                        pathlib.Path(inner).expanduser().resolve(),
                        namespace.bitrate,
                        namespace.delete,
                    )
                )
    return ans


def main():
    command_parser = argparse.ArgumentParser()
    ig = command_parser.add_mutually_exclusive_group(required=True)
    ig.add_argument(
        "-i",
        "--input",
        nargs="+",
        help="Locations of files for conversion. If this is a directory, all audio files recursively contained will be merged into one file.",
    )
    ig.add_argument(
        "-a",
        "--auto",
        nargs="+",
        help="Locations to auto-convert. Will recursively search for subfolders which contain no other directories and contain audio file(s). These files will be converted/merged.",
        metavar="LOCATION",
    )
    command_parser.add_argument(
        "-x",
        "--delete",
        action="store_true",
        help="Delete input files after conversion. DO NOT USE THIS IF YOU DON'T HAVE COMPLETE CONFIDENCE IN THIS TOOL.",
    )
    command_parser.add_argument(
        "-o",
        "--output",
        help="Set output file name. Defaults to the name of the first input file with a .opus extension",
    )
    mg = command_parser.add_mutually_exclusive_group()
    mg.add_argument(
        "-m",
        "--metadata",
        help="FFMETADATA file containing desired final metadata. Use -M if the metadata also contains chapter information",
    )
    mg.add_argument(
        "-M",
        "--metadatachapter",
        help="FFMETADATA file containing desired final metadata along with chapter data. Use -m to preserve automatic chapter detection.",
    )
    command_parser.add_argument(
        "-b",
        "--bitrate",
        help="Set bitrate for output file. Defaults to 32kbps for inputs under 192kbps, and 192kbps for inputs above that threshold.",
    )
    command_parser.add_argument(
        "-c",
        "--cuesheet",
        help="Set location for cuesheet file to read for chapter data. Only works if the input is a singular file.",
    )
    command_parser.add_argument(
        "-I",
        "--cover",
        help="Explicitly set final cover file. Will attempt to autodiscover cover if not set.",
    )
    parser = CustomPrintingArgParse(
        prog="yaacs",
        description="A Script to convert audiobooks to .opus",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    parser.add_argument(
        "-t",
        "--threads",
        help="Number of subprocesses to spawn to convert books. Not specifying or 0 will default to core count.",
        type=int,
        action="store",
        default=0,
    )
    command_parser_help_text = f"  {command_parser.format_help()[command_parser.format_help().find("-i INPUT [INPUT ...],"):]}"
    additional_usage = f"[{command_parser.format_usage()[command_parser.format_usage().find("("):].rstrip()}]+"
    parser.modded_help_output = (
        parser.format_help().replace(
            parser.format_usage(),
            f"{parser.format_usage().rstrip()} {additional_usage}\n",
        )
        + command_parser_help_text
        + "\n"
    )
    global_args, command_args = parser.parse_known_args()
    chunks: list[argparse.Namespace] = []
    start = 0
    for i, curr in enumerate(command_args):
        if i != 0 and curr in {"-i", "--input", "-a", "--auto"}:
            chunks.append(command_parser.parse_args(command_args[start:i]))
            start = i
    chunks.append(command_parser.parse_args(command_args[start:]))
    if not chunks:
        print("Error: No inputs specified")
        sys.exit(1)
    args = validate_inputs(chunks)
    processes = global_args.threads if global_args.threads != 0 else None
    with multiprocessing.Pool(processes=processes) as pool:
        total_amount = len(args)
        iter = pool.imap_unordered(dispatch_conversion, args)
        for i, (print_str, success) in enumerate(iter):
            if success:
                print(
                    f"Completed conversion and merger into {print_str}: ({i}/{total_amount})"
                )
            else:
                print(f"Failed to convert {print_str}: ({i}/{total_amount})")


if __name__ == "__main__":
    main()
