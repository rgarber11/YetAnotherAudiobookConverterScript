import logging
import pathlib
import subprocess

from yaacs.models import FileInfo


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


def prepare_single_file_conversion(
    file_metadata: FileInfo,
    cuesheet: pathlib.Path | None,
    auto_chapters: bool,
    temp_dir: pathlib.Path,
    logger: logging.Logger,
) -> bool:
    input_file: pathlib.Path = file_metadata.filename
    logger.info(f"Set performer for {input_file.name} as {file_metadata.performer}")
    temp_cue_file = temp_dir.joinpath(f"{input_file.stem}.cue")
    found_chapters = not auto_chapters
    if cuesheet:
        cued_file = add_cue(input_file, cuesheet, temp_dir, logger)
        if not cued_file:
            return False
        input_file = cued_file
        found_chapters = True
    elif auto_chapters and file_metadata.cuesheet:
        logger.info(f"Found embedded cuesheet in {input_file}")
        with temp_cue_file.open("w+") as temp_cue:
            _ = temp_cue.write(
                f'FILE "{input_file.name}" {input_file.suffix[1:]}\n{file_metadata.cuesheet}\n'
            )
        cued_file = add_cue(file_metadata.filename, temp_cue_file, temp_dir, logger)
        if not cued_file:
            return False
        input_file = cued_file
        found_chapters = True
    if file_metadata.chapters:
        logger.info(f"Found embedded chapter data in {input_file}")
        found_chapters = True
    if not found_chapters:
        logger.warning(f"Chapters not found for {input_file.name}")
    return True


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
    success = prepare_single_file_conversion(
        metadata, cuesheet, auto_chapters, temp_dir, logger
    )
    if not success:
        return False
    return final_conversion(
        metadata.filename,
        output_file,
        metadata_file,
        auto_chapters,
        bitrate,
        metadata.performer,
        logger,
    )
