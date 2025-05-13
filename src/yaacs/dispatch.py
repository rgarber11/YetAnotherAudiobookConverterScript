import json
import logging
import pathlib
import subprocess
import tempfile
from typing import cast

from yaacs.consts import audio_files
from yaacs.conversion.multiple import merge_together
from yaacs.conversion.single import convert_single_file
from yaacs.cover import attempt_attach_cover
from yaacs.models import Chapter, CoverStatus, DispatchArgs, FFProbeResult, FileInfo


def empty_not_none(s: str | None) -> str:
    if not s:
        return ""
    return s


def get_initial_int(x: str | None) -> int | None:  # atoi() in Python
    if x is None:
        return None
    try:
        return int(x.replace(x.lstrip("0123456789"), ""))
    except (ValueError, AttributeError):
        return None


def get_performer(
    raw_metadata: FFProbeResult,
) -> str:  # Most formats don't have a performer tag
    if raw_metadata["format"]["tags"].get("performer"):
        return cast(str, raw_metadata["format"]["tags"].get("performer"))
    if raw_metadata["format"]["tags"].get("narratedby"):
        return cast(str, raw_metadata["format"]["tags"].get("narratedby"))
    if raw_metadata["format"]["tags"].get("composer"):
        return cast(str, raw_metadata["format"]["tags"].get("composer"))
    if raw_metadata["format"]["tags"].get("album_artist"):
        return cast(str, raw_metadata["format"]["tags"].get("album_artist"))
    return ""


def get_metadata(music_file: pathlib.Path, logger: logging.Logger) -> FileInfo:
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
    metadata = cast(FFProbeResult, metadata)
    ans = FileInfo(
        filename=music_file,
        performer=get_performer(metadata),
        cuesheet="",
        chapters=[],
        bit_rate=int(metadata["format"]["bit_rate"]),
        title=empty_not_none(metadata["format"]["tags"].get("title")),
        album=empty_not_none(metadata["format"]["tags"].get("album")),
        genre=empty_not_none(metadata["format"]["tags"].get("genre")),
        date=empty_not_none(metadata["format"]["tags"].get("date")),
        publisher=empty_not_none(metadata["format"]["tags"].get("publisher")),
        track=get_initial_int(metadata["format"]["tags"].get("track")),
        disc=get_initial_int(metadata["format"]["tags"].get("disc")),
        duration=float(metadata["format"]["duration"]),
        artist=empty_not_none(metadata["format"]["tags"].get("artist")),
        cover_codec="",
    )
    if any(stream["codec_type"] == "video" for stream in metadata["streams"]):
        ans.cover_codec = next(
            stream["codec_name"]
            for stream in metadata["streams"]
            if stream["codec_type"] == "video"
        )
    if metadata["format"]["tags"].get("CUESHEET"):
        ans.cuesheet = cast(str, metadata["format"]["tags"]["CUESHEET"])
    elif metadata["format"]["tags"].get("cuesheet"):
        ans.cuesheet = cast(str, metadata["format"]["tags"].get("cuesheet"))
    if ans.cuesheet and "FILE" not in ans.cuesheet:
        ans.cuesheet = f'FILE "{music_file.name}" MP3\n{ans.cuesheet}\n'
    if metadata["chapters"]:
        for chapter in metadata["chapters"]:
            ans.chapters.append(
                Chapter(
                    (
                        cast(str, chapter["tags"]["title"])
                        if chapter["tags"].get("title")
                        else f"Chapter {int(chapter["id"]) + 1}"
                    ),
                    (
                        (
                            float(chapter["end_time"])
                            - float(chapter["start_time"]) * 1000
                        )
                    ),
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


def prepare_file_metadata(
    media_locations: list[pathlib.Path], logger: logging.Logger
) -> list[FileInfo]:
    file_metadata = [get_metadata(file, logger) for file in media_locations]
    if all(meta.track for meta in file_metadata):
        logger.info(f"Sorting {[loc.name for loc in media_locations]} by track number")
        file_metadata.sort(
            key=lambda x: ((1, x.track) if not x.disc else (x.disc, x.track))
        )
    else:
        logger.info(f"Sorting {[loc.name for loc in media_locations]} by file name")
        file_metadata.sort(key=lambda x: x.filename.stem)
    return file_metadata


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
            auto_bitrate = False
            if not bitrate:
                logger.info("Setting auto-bitrate")
                raw_bitrate = max(int(file.bit_rate) for file in file_metadata)
                auto_bitrate = True
                if raw_bitrate >= 262144:
                    logger.info("Auto bitrate set to 192k")
                    bitrate = "192k"
                else:
                    logger.info("Auto bitrate set to 32k")
                    bitrate = "32k"
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
                    auto_bitrate,
                    bitrate,
                    temp_dir_path,
                    logger,
                )
            else:
                if file_metadata[0].filename.suffix == ".opus" and bitrate[-1] == "|":
                    logger.warning(
                        f"{file_metadata[0].filename.name} is already a .opus file. An explicit bitrate is required for downsampling."
                    )
                    return (
                        ", ".join(
                            media_location.name
                            for media_location in args.media_locations
                        ),
                        False,
                    )
                bitrate = bitrate.replace("|", "")
                success = convert_single_file(
                    file_metadata[0],
                    cuesheet,
                    temp_dir_path,
                    output_file,
                    metadata_file,
                    auto_chapters,
                    bitrate,
                    logger,
                )
            if success:
                image_status = attempt_attach_cover(
                    file_metadata, output_file, cover_image, temp_dir_path, logger
                )
                if image_status == CoverStatus.ATTACHMENT_FAILED:
                    logger.error(f"Failed to attach cover image to {output_file}")
                elif image_status == CoverStatus.SUCCESS:
                    logger.info(f"Attached cover image to {output_file}")
                else:
                    logger.warning(
                        f"Cover image not found for {
                            ", ".join(loc.name for loc in args.media_locations)}"
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
            f"Exception when converting {", ".join(
                media_location.name for media_location in args.media_locations)}: {repr(e)}"
        )
        return (
            ", ".join(media_location.name for media_location in args.media_locations),
            False,
        )
