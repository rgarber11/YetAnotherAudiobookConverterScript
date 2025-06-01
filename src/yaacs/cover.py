import base64
import logging
import pathlib
import subprocess
from typing import cast

from mutagen._util import MutagenError
from mutagen.flac import Picture
from mutagen.id3 import PictureType
from mutagen.oggopus import OggOpus

from .consts import image_files
from .models import CoverStatus, FileInfo


# FFMPEG cannot map covers to opus (11/13/24)
def attach_image(
    output_file: pathlib.Path, cover_image: pathlib.Path, logger: logging.Logger
) -> bool:
    try:
        logger.info(f"Attaching image for {output_file.name}")
        with cover_image.open("rb") as img:
            image_data = img.read()
        picture = Picture()
        picture.data = image_data
        picture.type = cast(int, PictureType.COVER_FRONT)
        picture.height = 0  # We're allowed to set all these to zero
        picture.width = 0
        picture.colors = 0
        picture.depth = 0
        picture.desc = "Cover (front)"
        picture.mime = image_files[cover_image.suffix[1:]]
        picture_data = picture.write()
        encoded_data = base64.b64encode(picture_data)
        vcomment_value = encoded_data.decode("ascii")
        file = OggOpus(str(output_file))
        file["metadata_block_picture"] = [vcomment_value]
        file.save()
        return True
    except (IOError, MutagenError):
        return False


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


def discover_cover_image(
    file_metadata: list[FileInfo], temp_dir_path: pathlib.Path, logger: logging.Logger
) -> pathlib.Path | None:
    logger.info("Discovering cover...")
    for file in file_metadata:
        if file.cover_codec:
            logger.info("Found embedded cover...")
            return extract_embedded_image(
                file.filename, temp_dir_path, file.cover_codec, logger
            )
    logger.info("Searching for cover within folder")
    for file in file_metadata:
        images: list[pathlib.Path] = []
        for suffix in image_files:
            images.extend(img for img in file.filename.parent.glob(f"*.{suffix}"))
        if images:
            logger.info(f"Found cover {images[0].name}")
            return images[0]
    return None


def attempt_attach_cover(
    file_metadata: list[FileInfo],
    output_file: pathlib.Path,
    cover_image: pathlib.Path | None,
    temp_dir: pathlib.Path,
    logger: logging.Logger,
) -> CoverStatus:
    if not cover_image:
        cover_image = discover_cover_image(file_metadata, temp_dir, logger)
    if not cover_image:
        return CoverStatus.NONE_FOUND
    image_success = attach_image(output_file, cover_image, logger)
    if image_success:
        return CoverStatus.SUCCESS
    return CoverStatus.ATTACHMENT_FAILED
