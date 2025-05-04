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
image_files: tuple[str, str, str, str] = ("jpg", "png", "tiff", "jpeg")
