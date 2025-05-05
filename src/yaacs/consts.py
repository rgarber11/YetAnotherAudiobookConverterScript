import importlib.metadata

VERSION = importlib.metadata.version("yaacs")
audio_files: tuple[str, str, str, str, str, str, str, str] = (
    "mp3",
    "m4a",
    "m4b",
    "ogg",
    "flac",
    "wav",
    "aiff",
    "opus",
)
image_files: dict[str, str] = {
    "jpg": "image/jpg",
    "png": "image/png",
    "tiff": "image/tiff",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
}
