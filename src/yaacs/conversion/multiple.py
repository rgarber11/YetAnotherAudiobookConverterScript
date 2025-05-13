import logging
import pathlib
import subprocess

from yaacs.models import Chapter, DiscoveredMetadata, FileInfo


def generate_chapters_for_folder(
    file_metadata: list[FileInfo], chapter_file: pathlib.Path, logger: logging.Logger
) -> pathlib.Path:
    chapters: list[Chapter] = []
    logger.info("Detecting Chapters...")
    for file in file_metadata:
        if file.chapters:
            for chapter in file.chapters:
                prepend = file.title if file.title else file.filename.stem
                chapters.append(
                    Chapter(f"{prepend} - {chapter.title}", chapter.duration)
                )
        elif file.title:
            chapters.append(Chapter(file.title, file.duration * 1000))
        else:
            chapters.append(
                Chapter(
                    file.filename.stem,
                    file.duration * 1000,
                )
            )
    logger.info(f"Found {len(chapters)} Chapters: {chapters}")
    with chapter_file.open("w+") as chapterIO:
        _ = chapterIO.write(";FFMETADATA1\n")
        duration = 0.0
        for chapter in chapters:
            _ = chapterIO.write(
                f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={duration}\nEND={
                    duration + chapter.duration}\ntitle={chapter.title}\n"
            )
            duration += chapter.duration
    return chapter_file


def generate_metadata_for_folder(
    file_metadata: list[FileInfo], metadata_file: pathlib.Path, logger: logging.Logger
) -> pathlib.Path:
    logger.info("Detecting Metadata...")
    metadata = DiscoveredMetadata("", "", "", "", "", "")
    for file in file_metadata:
        if not metadata.title and "album" in file.album:
            metadata.title = file.album
        if not metadata.artist and file.artist:
            metadata.artist = file.artist
        if not metadata.performer:
            metadata.performer = file.performer
        if not metadata.genre and file.genre:
            metadata.genre = file.genre
        if not metadata.date and file.date:
            metadata.date = file.date
        if not metadata.publisher and file.publisher:
            metadata.publisher = file.publisher
    logger.info(f"Found metadata {metadata}")
    with metadata_file.open("w+") as metadataIO:
        _ = metadataIO.write(";FFMETADATA1\n")
        if metadata.title:
            _ = metadataIO.write(f"title={metadata.title}\n")
        if metadata.artist:
            _ = metadataIO.write(f"artist={metadata.artist}\n")
        if metadata.performer:
            _ = metadataIO.write(f"performer={metadata.performer}\n")
        if metadata.genre:
            _ = metadataIO.write(f"genre={metadata.genre}\n")
        if metadata.date:
            _ = metadataIO.write(f"date={metadata.date}\n")
        if metadata.publisher:
            _ = metadataIO.write(f"publisher={metadata.publisher}\n")
    return metadata_file


def merge_together(
    file_metadata: list[FileInfo],
    metadata_file: pathlib.Path | None,
    auto_chapters: bool,
    output_file: pathlib.Path,
    auto_bitrate: bool,
    bitrate: str,
    temp_dir: pathlib.Path,
    logger: logging.Logger,
) -> bool:
    if not metadata_file:
        metadata_file = generate_metadata_for_folder(
            file_metadata,
            temp_dir.joinpath(f"{file_metadata[0].filename.stem}.ffmeta"),
            logger,
        )
    all_same_suffix = True
    first_suffix = file_metadata[0].filename.suffix
    for file in file_metadata:
        if file.filename.suffix != first_suffix:
            all_same_suffix = False

    chapter_file = (
        generate_chapters_for_folder(
            file_metadata,
            temp_dir.joinpath(
                f"{file_metadata[0].filename.stem}_chapters.ffmeta",
            ),
            logger,
        )
        if auto_chapters
        else None
    )
    args = ["ffmpeg", "-v", "quiet", "-y"]
    if all_same_suffix:
        logger.info("All files have the same suffix. Assuming input concatenation.")
        concat_filename = temp_dir.joinpath(f"{file_metadata[0].filename.stem}.files")
        with concat_filename.open("w+") as concat_list:
            concat_list.writelines(
                f"file '{str(file.filename
                             ).replace("'", "'\\''")}'\n"
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
            args.extend(["-i", f"file:{file.filename}"])
        args.extend(["-f", "ffmetadata", "-i", f"file:{metadata_file}"])
        if auto_chapters:
            args.extend(["-f", "ffmetadata", "-i", f"file:{chapter_file}"])
        # If there are heterogeneous inputs, a filter is the only way to concatenate
        args.extend(
            [
                "-filter_complex",
                f'{"".join(f"[{i}:a:0]" for i, _ in enumerate(file_metadata))}concat={
                    len(file_metadata)}:v=0:a=1[outa]',
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
