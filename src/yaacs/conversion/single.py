import logging
import pathlib
import subprocess

from yaacs.cue.parse import VisitError, parse_cue_str, parse_cuefile
from yaacs.models import FileInfo


def final_conversion(
    init_file: pathlib.Path,
    output_file: pathlib.Path,
    metadata_file: pathlib.Path | None,
    chapter_file: pathlib.Path | None,
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
        if chapter_file:
            args.extend(["-f", "ffmetadata", "-i", f"file:{chapter_file}"])
            args.extend(["-map_metadata", "0", "-map_chapters", "1"])
        else:
            args.extend(["-map_metadata", "0"])
            args.extend(["-map_chapters", "0"])
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
    conversion = subprocess.run(args)
    if conversion.returncode != 0:
        logger.error(f"Failed to run: {args}")
    logger.info(f"Ran: {args}")
    return conversion.returncode == 0


def create_cue_chapter_file(
    cue: pathlib.Path | str,
    temp_directory: pathlib.Path,
    total_duration: float,
    logger: logging.Logger,
) -> pathlib.Path | None:
    logger.info("Creating Cue file...")
    chapter_file = temp_directory.joinpath("cue_chapter.ffmeta")
    try:
        cuesheet = parse_cue_str(cue) if isinstance(cue, str) else parse_cuefile(cue)
        if len(cuesheet.files) > 1:
            logger.error("Cuesheet for single input file contains more than one file.")
            return None
        with chapter_file.open("w") as chapters:
            _ = chapters.write(";FFMETADATA1\n")
            for i, track in enumerate(cuesheet.files[0].tracks[:-1]):
                _ = chapters.write(
                    f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={track.indices[1] * 1000}\nEND={
                    cuesheet.files[0].tracks[i + 1].indices[1] * 1000}\ntitle={track.get_title()}\n"
                )
            last_track = cuesheet.files[0].tracks[-1]
            if total_duration > last_track.indices[1]:
                _ = chapters.write(
                    f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={last_track.indices[1] * 1000}\nEND={total_duration * 1000}\ntitle={last_track.get_title()}\n"
                )
    except (VisitError, ValueError):
        logger.error("Cannot parse cuesheet.")
        return None
    return chapter_file


def prepare_single_file_conversion(
    file_metadata: FileInfo,
    cuesheet: pathlib.Path | None,
    auto_chapters: bool,
    temp_dir: pathlib.Path,
    logger: logging.Logger,
) -> tuple[pathlib.Path | None, bool]:
    input_file: pathlib.Path = file_metadata.filename
    logger.info(f"Set performer for {input_file.name} as {file_metadata.performer}")
    found_chapters = not auto_chapters
    chapter_file: pathlib.Path | None = None
    if cuesheet:
        chapter_file = create_cue_chapter_file(
            cuesheet, temp_dir, file_metadata.duration, logger
        )
        if not chapter_file:
            return (None, False)
        found_chapters = True
    elif auto_chapters and file_metadata.cuesheet:
        logger.info(f"Found embedded cuesheet in {input_file}")
        chapter_file = create_cue_chapter_file(
            file_metadata.cuesheet, temp_dir, file_metadata.duration, logger
        )
        if not chapter_file:
            return (None, False)
        found_chapters = True
    if file_metadata.chapters:
        logger.info(f"Found embedded chapter data in {input_file}")
        found_chapters = True
    if not found_chapters:
        logger.warning(f"Chapters not found for {input_file.name}")
    return (chapter_file, True)


def convert_single_file(
    metadata: FileInfo,
    cuesheet: pathlib.Path | None,
    temp_dir: pathlib.Path,
    output_file: pathlib.Path,
    metadata_file: pathlib.Path | None,
    auto_chapters: bool,
    bitrate: str,
    logger: logging.Logger,
) -> bool:
    chapter_file, success = prepare_single_file_conversion(
        metadata, cuesheet, auto_chapters, temp_dir, logger
    )
    if not success:
        return False
    return final_conversion(
        metadata.filename,
        output_file,
        metadata_file,
        chapter_file,
        auto_chapters,
        bitrate,
        metadata.performer,
        logger,
    )
