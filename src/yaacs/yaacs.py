#!/bin/env python
from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata
import json
import logging
import logging.config
import multiprocessing
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

VERSION = importlib.metadata.version("yaacs")
audio_files = ("mp3", "m4a", "m4b", "ogg", "flac", "wav", "aiff", "opus")
image_files = ("jpg", "png", "tiff", "jpeg")
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"simple": {"format": "%(message)s"}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {"root": {"level": "WARNING", "handlers": ["stdout"]}},
    }
)
single_process_logger = logging.getLogger("yaacs")


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


# The command parser is invoked multiple times. This makes that possible
class GlobalArgsArgparse(argparse.ArgumentParser):
    def __init__(
        self, *args, command_parser_help: str, command_parser_usage: str, **kwargs
    ):
        self.command_parser_help: str = "\n".join(command_parser_help.splitlines()[4:])
        self.command_parser_usage: str = (
            f"[{command_parser_usage[command_parser_usage.find("("):].rstrip()}]+"
        )
        self.modded_help: str | None = None
        self.modded_usage: str | None = None
        super().__init__(*args, **kwargs)

    def format_help(self):
        # if not file:
        #     file = sys.stdout
        if not self.modded_help:
            self.modded_help = (
                super()
                .format_help()
                .replace(
                    super().format_usage(),
                    f"{super().format_usage().rstrip()} {self.command_parser_usage}\n",
                )
                + self.command_parser_help
                + "\n"
            )
        return self.modded_help

    def format_usage(self):
        if not self.modded_usage:
            self.modded_usage = (
                f"{super().format_usage().rstrip()} {self.command_parser_usage}\n"
            )
        return self.modded_usage


class CommandArgsArgparse(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        self.modded_help: str | None = None
        self.modded_usage: str | None = None
        super().__init__(*args, **kwargs)

    def set_modded_help_usage(self, modded_help: str, modded_usage: str):
        self.modded_help = modded_help
        self.modded_usage = modded_usage

    def format_help(self):
        # if not file:
        #     file = sys.stdout
        if not self.modded_help:
            return super().format_help()
        return self.modded_help

    def format_usage(self):
        if not self.modded_usage:
            return super().format_usage()
        return self.modded_usage


def get_initial_int(x: str) -> int:  # atoi() in Python
    return int(x.replace(x.lstrip("0123456789"), ""))


def get_metadata(music_file: pathlib.Path, logger: logging.Logger) -> dict[str, Any]:
    logger.info(f"Getting metadata... for {music_file.name}")
    metadata_args = [
        "ffprobe",
        "-v",
        "quiet",
        "-of",
        "json",
        "-show_entries",
        "stream:format",
        "-show_chapters",
        f"file:{music_file}",
    ]
    logger.info(f"Running {metadata_args}")
    json_string = subprocess.run(
        metadata_args,
        capture_output=True,
    ).stdout.decode("utf-8")
    metadata = json.loads(json_string)
    if "tags" not in metadata["format"]:
        metadata["format"]["tags"] = {}
    if music_file.suffix == ".opus":  # FFMpeg maps opus tags wrong (11/13/24)
        for k, v in metadata["streams"][0]["tags"]:
            metadata["format"]["tags"][k] = v
    return metadata


def add_cue(
    music_file: pathlib.Path,
    cue_file: pathlib.Path,
    temp_directory: pathlib.Path,
    logger: logging.Logger,
) -> pathlib.Path | None:
    logger.info("Adding Cue file...")
    file_with_chapters = temp_directory.joinpath(f"{music_file.stem}.mka")
    temp_file = temp_directory.joinpath(f"{music_file.stem}.temp.mka")
    temp_args = [
        "mkvmerge",
        "-q",
        str(music_file),
        "--chapters",
        str(cue_file),
        "-o",
        str(temp_file),
    ]
    temporary = subprocess.run(temp_args)
    if temporary.returncode != 0:
        logger.error(f"Failed to run: {temp_args}")
        return None
    else:
        logger.info(f"Ran {temp_args}")
    # FFMpeg does not read chapters correctly when reencoding to opus from mka
    final_args = [
        "ffmpeg",
        "-v",
        "quiet",
        "-i",
        f"file:{temp_file}",
        "-i",
        f"file:{music_file}",
        "-map",
        "0",
        "-map_chapters",
        "0",
        "-map_metadata",
        "1",
        "-c",
        "copy",
        f"file:{file_with_chapters}",
    ]
    final = subprocess.run(final_args)
    temp_file.unlink()
    if final.returncode != 0:
        logger.error(f"Failed to run: {final_args}")
        return None
    else:
        logger.info(f"Ran {final_args}")
    return file_with_chapters


def get_performer(metadata: Any) -> str:  # Most formats don't have a performer tag
    if "performer" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["performer"]
    if "narratedby" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["narratedby"]
    if "composer" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["composer"]
    if "album_artist" in metadata["format"]["tags"]:
        return metadata["format"]["tags"]["album_artist"]
    return ""


def final_conversion(
    init_file: pathlib.Path,
    output_file: pathlib.Path,
    metadata_file: pathlib.Path | None,
    auto_chapters: bool,
    bitrate: str,
    performer: str,
    logger: logging.Logger,
) -> bool:
    logger.info(f"Converting single file {init_file.name}")
    args = ["ffmpeg", "-v", "quiet", "-y", "-i", f"file:{init_file}"]
    if metadata_file:
        args.extend(
            [
                "-f",
                "ffmetadata",
                "-i",
                f"file:{metadata_file}",
                "-map_metadata",
                "1",
            ]
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
        args.extend(["-c", "copy", f"file:{output_file}"])
    else:
        args.extend(
            [
                "-c:a",
                "libopus",
                "-b:a",
                bitrate.replace("|", ""),
                "-vbr",
                "on",
                "-compression_level",
                "10",
                "-application",
                "voip",
                f"file:{output_file}",
            ]
        )
    conversion = subprocess.run(args)
    if conversion.returncode != 0:
        logger.error(f"Failed to run: {args}")
    logger.info(f"Ran: {args}")
    return conversion.returncode == 0


# FFMPEG cannot map covers to opus (11/13/24)
def attach_image(
    output_file: pathlib.Path, cover_image: pathlib.Path, logger: logging.Logger
) -> bool:
    logger.info(f"Attaching image for {output_file.name}")
    attachment_args = [
        "opustags",
        str(output_file),
        "-i",
        "--set-cover",
        str(cover_image),
    ]
    attachment = subprocess.run(attachment_args)
    logger.info(attachment_args)
    return attachment.returncode == 0


def extract_embedded_image(
    media_file: pathlib.Path, temp_dir: pathlib.Path, codec: str, logger: logging.Logger
) -> pathlib.Path | None:
    logger.info(f"Extracting image from {media_file.name}")
    if codec[0] == "m":
        codec = codec[1:]
    file_with_image = temp_dir.joinpath(f"{media_file.stem}.{codec}")
    extraction_args: list[str] = [
        "ffmpeg",
        "-v",
        "quiet",
        "-y",
        "-i",
        f"file:{media_file}",
        "-map",
        "0:v:0",
        "-vcodec",
        "copy",
        f"file:{file_with_image}",
    ]
    extraction = subprocess.run(extraction_args)
    if extraction.returncode != 0:
        logger.error(f"Failed to run {extraction_args}")
        return None
    else:
        logger.info(f"Ran: {extraction_args}")
    return file_with_image


def generate_chapters_for_folder(
    file_metadata: list[Any], chapter_file: pathlib.Path, logger: logging.Logger
) -> pathlib.Path:
    chapters: list[Chapter] = []
    logger.info("Detecting Chapters...")
    for file in file_metadata:
        if file["chapters"]:
            for chapter in file["chapters"]:
                prepend = (
                    file["format"]["tags"]["title"]
                    if "title" in file["format"]["tags"]
                    else file["format"]["filename"].stem
                )
                chapters.append(
                    Chapter(
                        (
                            f"{prepend} - {chapter["tags"]["title"]}"
                            if "title" in chapter["tags"]
                            else f"{prepend} - Chapter {int(chapter["id"]) + 1}"
                        ),
                        (float(chapter["end_time"]) - float(chapter["start_time"]))
                        * 1000,
                    )
                )
        elif "title" in file["format"]["tags"]:
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
    logger.info(f"Found {len(chapters)} Chapters: {chapters}")
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
    file_metadata: list[Any], metadata_file: pathlib.Path, logger: logging.Logger
) -> pathlib.Path:
    logger.info("Detecting Metadata...")
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
    logger.info(f"Found metadata {metadata}")
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
    logger: logging.Logger,
) -> bool:
    if not metadata_file:
        metadata_file = generate_metadata_for_folder(
            file_metadata,
            temp_dir.joinpath(f"{file_metadata[0]["format"]["filename"].stem}.ffmeta"),
            logger,
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
                f"{file_metadata[0]["format"]["filename"].stem}_chapters.ffmeta",
            ),
            logger,
        )
        if auto_chapters
        else None
    )
    args = ["ffmpeg", "-v", "quiet", "-y"]
    if all_same_suffix:
        logger.info("All files have the same suffix. Assuming input concatenation.")
        concat_filename = temp_dir.joinpath(
            f"{file_metadata[0]["format"]["filename"].stem}.files"
        )
        with concat_filename.open("w+") as concat_list:
            concat_list.writelines(
                f"file '{str(file["format"]["filename"]).replace("'", "'\\''")}'\n"
                for file in file_metadata
            )
        args.extend(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                f"file:{concat_filename}",
                "-f",
                "ffmetadata",
                "-i",
                f"file:{metadata_file}",
            ]
        )
        if auto_chapters:
            args.extend(
                [
                    "-f",
                    "ffmetadata",
                    "-i",
                    f"file:{chapter_file}",
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
            logger.info(
                "Default bitrate used with opus files. Preserving quality by using copy codec."
            )
            args.extend(["-c:a", "copy", f"file:{output_file}"])
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
                    f"file:{output_file}",
                ]
            )
    else:
        logger.info("Heterogeneous inputs. Using concatenation filter.")
        for file in file_metadata:
            args.extend(["-i", f"file:{file["format"]["filename"]}"])
        args.extend(["-f", "ffmetadata", "-i", f"file:{metadata_file}"])
        if auto_chapters:
            args.extend(["-f", "ffmetadata", "-i", f"file:{chapter_file}"])
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
                f"file:{output_file}",
            ]
        )
    # print(args)
    merger = subprocess.run(args)
    if merger.returncode != 0:
        logger.error(f"Failed to run: {args}")
    else:
        logger.info(f"Ran {args}")
    return merger.returncode == 0


# Auto-detection recursion
def get_folders_of_files(media_location: pathlib.Path) -> list[pathlib.Path]:
    if all(loc.is_file() for loc in media_location.iterdir()):
        if not list(
            loc for loc in media_location.iterdir() if loc.suffix[1:] in audio_files
        ):
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
    single_process_logger.info(f"Detecting books within {media_location.name}")
    for folder in get_folders_of_files(media_location):
        output_file = folder.joinpath(f"{folder.stem}.opus").expanduser().resolve()
        if output_file.exists():
            x = input(f"File {output_file} exists: Overwrite? (y/N): ")
            if x not in {"y", "Y"}:
                sys.exit(1)
            output_file.unlink()
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
        delete_idxs.clear()
        size = len(media_locations)
        for i in range(size):
            if media_locations[i].is_dir():
                delete_idxs.append(i)
                for suffix in audio_files:
                    media_locations.extend(media_locations[i].glob(f"*.{suffix}"))
                media_locations.extend(
                    loc for loc in media_locations[i].iterdir() if loc.is_dir()
                )
        for idx in reversed(delete_idxs):
            del media_locations[idx]
    return media_locations


def discover_cover_image(
    file_metadata: list[Any], temp_dir_path: pathlib.Path, logger: logging.Logger
) -> pathlib.Path | None:
    logger.info("Discovering cover...")
    for file in file_metadata:
        if any(stream["codec_type"] == "video" for stream in file["streams"]):
            logger.info("Found embedded cover...")
            codec = next(
                stream["codec_name"]
                for stream in file["streams"]
                if stream["codec_type"] == "video"
            )
            return extract_embedded_image(
                file["format"]["filename"], temp_dir_path, codec, logger
            )
    logger.info("Searching for cover within folder")
    for file in file_metadata:
        images: list[pathlib.Path] = []
        for suffix in image_files:
            images.extend(
                img for img in file["format"]["filename"].parent.glob(f"*.{suffix}")
            )
        if images:
            logger.info(f"Found cover {images[0].name}")
            return images[0]
    return None


def prepare_file_metadata(
    media_locations: list[pathlib.Path], logger: logging.Logger
) -> list[Any]:
    file_metadata = [get_metadata(file, logger) for file in media_locations]
    for file in file_metadata:
        if file["format"]["filename"].startswith("file:"):
            file["format"]["filename"] = file["format"]["filename"][5:] # Keep filenames canonical
        file["format"]["filename"] = (
            pathlib.Path(file["format"]["filename"]).expanduser().resolve()
        )
    if all("track" in meta["format"]["tags"] for meta in file_metadata):
        logger.info(f"Sorting {[loc.name for loc in media_locations]} by track number")
        file_metadata.sort(
            key=lambda x: (
                (1, get_initial_int(x["format"]["tags"]["track"]))
                if "disc" not in x["format"]["tags"]
                else (
                    get_initial_int(x["format"]["tags"]["disc"]),
                    get_initial_int(x["format"]["tags"]["track"]),
                )
            )
        )
    else:
        logger.info(f"Sorting {[loc.name for loc in media_locations]} by file name")
        file_metadata.sort(key=lambda x: x["format"]["filename"].stem)
    return file_metadata


def prepare_single_file_conversion(
    file_metadata: Any,
    input_file: pathlib.Path,
    cuesheet: pathlib.Path | None,
    auto_chapters: bool,
    temp_dir: pathlib.Path,
    logger: logging.Logger,
) -> tuple[pathlib.Path, str, bool]:
    curr_input_file: pathlib.Path | None = input_file
    performer = get_performer(file_metadata)
    logger.info(f"Set performer for {input_file.name} as {performer}")
    temp_cue_file = temp_dir.joinpath(f"{input_file.stem}.cue")
    found_chapters = not auto_chapters
    if cuesheet:
        curr_input_file = add_cue(input_file, cuesheet, temp_dir, logger)
        found_chapters = True
    elif auto_chapters and "CUESHEET" in file_metadata["format"]["tags"]:
        logger.info(f"Found embedded cuesheet in {input_file}")
        with temp_cue_file.open("w+") as temp_cue:
            _ = temp_cue.write(
                f'FILE "{input_file.name}" {input_file.suffix[1:]}\n{file_metadata["format"]["tags"]["CUESHEET"]}\n'
            )
        curr_input_file = add_cue(input_file, temp_cue_file, temp_dir, logger)
        found_chapters = True
    elif auto_chapters and "cuesheet" in file_metadata["format"]["tags"]:
        logger.info(f"Found embedded cuesheet in {input_file}")
        with temp_cue_file.open("w+") as temp_cue:
            _ = temp_cue.write(
                f'FILE "{input_file.name}" {input_file.suffix[1:]}\n{file_metadata["format"]["tags"]["cuesheet"]}\n'
            )
        curr_input_file = add_cue(input_file, temp_cue_file, temp_dir, logger)
        found_chapters = True
    if not curr_input_file:
        return input_file, "", False
    if file_metadata["chapters"]:
        logger.info(f"Found embedded chapter data in {input_file}")
        found_chapters = True
    if not found_chapters:
        logger.warning(f"Chapters not found for {input_file.name}")
    return curr_input_file, performer, True


def dispatch_conversion(args: DispatchArgs) -> tuple[str, bool]:
    media_locations = flatten_manual_query(args.media_locations.copy())
    metadata_file = args.metadata_file
    cuesheet = args.cuesheet
    cover_image = args.cover_image
    auto_chapters = args.auto_chapters
    output_file = args.output_file
    bitrate = args.bitrate
    delete_originals = args.delete_originals
    logger = logging.getLogger("yaacs subprocess")
    logger.warning(f"Converting {",".join(str(loc) for loc in args.media_locations)}")
    try:
        file_metadata = prepare_file_metadata(media_locations, logger)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = pathlib.Path(temp_dir).expanduser().resolve()
            if not bitrate:
                logger.info("Setting auto-bitrate")
                raw_bitrate = max(
                    int(file["format"]["bit_rate"]) for file in file_metadata
                )
                if raw_bitrate >= 262144:
                    logger.info("Auto bitrate set to 192k")
                    bitrate = "192k|"
                else:
                    logger.info("Auto bitrate set to 32k")
                    bitrate = "32k|"
            success = False
            if len(media_locations) > 1:
                if cuesheet:
                    logger.error(
                        "Error! Cannot have a singular cuesheet with multiple files"
                    )
                    return (
                        ",".join(
                            media_location.name
                            for media_location in args.media_locations
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
                    logger,
                )
            else:
                input_file, performer, prep_success = prepare_single_file_conversion(
                    file_metadata[0],
                    media_locations[0],
                    cuesheet,
                    auto_chapters,
                    temp_dir_path,
                    logger,
                )
                if not prep_success:
                    return (
                        ", ".join(
                            media_location.name
                            for media_location in args.media_locations
                        ),
                        False,
                    )
                if input_file.suffix == ".opus" and bitrate[-1] == "|":
                    logger.warning(
                        f"{input_file.name} is already a .opus file. An explicit bitrate is required for downsampling."
                    )
                    return (
                        ", ".join(
                            media_location.name
                            for media_location in args.media_locations
                        ),
                        False,
                    )

                success = final_conversion(
                    input_file,
                    output_file,
                    metadata_file,
                    auto_chapters,
                    bitrate,
                    performer,
                    logger,
                )
            cover_image = (
                cover_image
                if cover_image
                else discover_cover_image(file_metadata, temp_dir_path, logger)
            )
            if success and cover_image:
                image_sucess = attach_image(output_file, cover_image, logger)
                if not image_sucess:
                    logger.error(f"Failed to attach cover image to {output_file}")
                else:
                    logger.info(f"Attached cover image to {output_file}")
            else:
                logger.warning(
                    f"Cover image not found for {", ".join(loc.name for loc in args.media_locations)}"
                )
        if success and delete_originals:
            logger.info("Deleting input files")
            for loc in media_locations:
                loc.unlink()
        if not success:
            if output_file.exists():
                logger.info("Deleting failed output")
                output_file.unlink()
            return (
                ", ".join(
                    media_location.name for media_location in args.media_locations
                ),
                False,
            )
        return output_file.name, True
    except Exception as e:
        logger.exception(
            f"Exception when converting {", ".join(media_location.name for media_location in args.media_locations)}: {repr(e)}"
        )
        return (
            ", ".join(media_location.name for media_location in args.media_locations),
            False,
        )


def validate_inputs(inputs: list[argparse.Namespace]) -> list[DispatchArgs]:
    ans: list[DispatchArgs] = []
    for namespace in inputs:
        if namespace.bitrate:
            if not re.match(r"\d+[kKmM]?", namespace.bitrate):
                single_process_logger.error("Error: Invalid Bitrate")
                sys.exit(1)
        if namespace.input:
            if namespace.output:
                output_file = pathlib.Path(namespace.output).expanduser().resolve()
            else:
                first_input = pathlib.Path(namespace.input[0]).expanduser().resolve()
                if first_input.is_dir():
                    output_file = (
                        first_input.joinpath(f"{first_input.stem}.opus")
                        .expanduser()
                        .resolve()
                    )
                else:
                    output_file = (
                        first_input.parent.joinpath(f"{first_input.stem}.opus")
                        .expanduser()
                        .resolve()
                    )
                single_process_logger.warning(
                    f"{", ".join(namespace.input)} will be outputted to {str(output_file)}"
                )
            if output_file.exists():
                x = input(f"File {output_file} exists: Overwrite? (y/N): ")
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
                single_process_logger.error(
                    "Error: Cannot set covers/metadata in auto mode"
                )
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
    command_parser = CommandArgsArgparse()
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
    parser = GlobalArgsArgparse(
        command_parser_help=command_parser.format_help(),
        command_parser_usage=command_parser.format_usage(),
        prog="yaacs",
        description="A Script to convert audiobooks to .opus",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    logging_opts = parser.add_mutually_exclusive_group()
    logging_opts.add_argument(
        "-q", "--quiet", help="Only log errors during conversion", action="store_true"
    )
    logging_opts.add_argument(
        "-V",
        "--verbose",
        help="Log more information about the conversion process",
        action="store_true",
    )
    parser.add_argument(
        "-t",
        "--threads",
        help="Number of subprocesses to spawn to convert books. Not specifying or 0 will default to core count.",
        type=int,
        action="store",
        default=0,
    )
    command_parser.set_modded_help_usage(parser.format_help(), parser.format_usage())
    global_args, command_args = parser.parse_known_args()
    logging.getLogger().setLevel(logging.WARNING)
    if global_args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    if global_args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    chunks: list[argparse.Namespace] = []
    start = 0
    for i, curr in enumerate(command_args):
        if i != 0 and curr in {"-i", "--input", "-a", "--auto"}:
            chunks.append(command_parser.parse_args(command_args[start:i]))
            start = i
    chunks.append(command_parser.parse_args(command_args[start:]))
    if not chunks:
        single_process_logger.error("Error: No inputs specified")
        sys.exit(1)
    args = validate_inputs(chunks)
    processes = global_args.threads if global_args.threads != 0 else None
    with multiprocessing.Pool(processes=processes) as pool:
        total_amount = len(args)
        iter = pool.imap_unordered(dispatch_conversion, args)
        for i, (print_str, success) in enumerate(iter):
            if success:
                single_process_logger.warning(
                    f"Completed conversion and merger into {print_str}: ({i+1}/{total_amount})"
                )
            else:
                single_process_logger.error(
                    f"Failed to convert {print_str}: ({i+1}/{total_amount})"
                )


if __name__ == "__main__":
    main()
