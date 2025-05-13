#!/bin/env python
from __future__ import annotations

import argparse
import logging
import multiprocessing
import pathlib
import re
import sys
from shutil import which
from typing import cast, override

from yaacs.consts import VERSION, audio_files
from yaacs.dispatch import dispatch_conversion
from yaacs.models import CommandParserArgs, DispatchArgs, GlobalParserArgs

single_process_logger = logging.getLogger("yaacs")


# The command parser is invoked multiple times. This makes that possible
class GlobalArgsArgparse(argparse.ArgumentParser):
    def __init__(
        self, *args, command_parser_help: str, command_parser_usage: str, **kwargs
    ):
        self.command_parser_help: str = "\n".join(command_parser_help.splitlines()[4:])
        self.command_parser_usage: str = (
            f"[{command_parser_usage[command_parser_usage.find(
                "("):].rstrip()}]+"
        )
        self.modded_help: str | None = None
        self.modded_usage: str | None = None
        super().__init__(*args, **kwargs)

    @override
    def format_help(self):
        # if not file:
        #     file = sys.stdout
        if not self.modded_help:
            self.modded_help = (
                super()
                .format_help()
                .replace(
                    super().format_usage(),
                    f"{super().format_usage().rstrip()} {
                        self.command_parser_usage}\n",
                )
                + self.command_parser_help
                + "\n"
            )
        return self.modded_help

    @override
    def format_usage(self):
        if not self.modded_usage:
            self.modded_usage = f"{super().format_usage().rstrip()} {
                    self.command_parser_usage}\n"
        return self.modded_usage


class CommandArgsArgparse(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:
        self.modded_help: str | None = None
        self.modded_usage: str | None = None
        super().__init__(*args, **kwargs)

    def set_modded_help_usage(self, modded_help: str, modded_usage: str):
        self.modded_help = modded_help
        self.modded_usage = modded_usage

    @override
    def format_help(self):
        # if not file:
        #     file = sys.stdout
        if not self.modded_help:
            return super().format_help()
        return self.modded_help

    @override
    def format_usage(self):
        if not self.modded_usage:
            return super().format_usage()
        return self.modded_usage


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
    ans: list[DispatchArgs] = []
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


def validate_inputs(inputs: list[CommandParserArgs]) -> list[DispatchArgs]:
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
                    output_file = first_input.joinpath(f"{first_input.stem}.opus")
                else:
                    output_file = first_input.parent.joinpath(
                        f"{first_input.stem}.opus"
                    )
                single_process_logger.warning(
                    f"{", ".join(namespace.input)} will be outputted to {
                        str(output_file)}"
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


def main() -> None:
    if not which("ffmpeg"):
        single_process_logger.error("FFMPEG not found on path. Exiting now.")
        sys.exit(1)
    command_parser = CommandArgsArgparse()
    ig = command_parser.add_mutually_exclusive_group(required=True)
    _ = ig.add_argument(
        "-i",
        "--input",
        nargs="+",
        help="Locations of files for conversion. If this is a directory, all audio files recursively contained will be merged into one file.",
    )
    _ = ig.add_argument(
        "-a",
        "--auto",
        nargs="+",
        help="Locations to auto-convert. Will recursively search for subfolders which contain no other directories and contain audio file(s). These files will be converted/merged.",
        metavar="LOCATION",
    )
    _ = command_parser.add_argument(
        "-x",
        "--delete",
        action="store_true",
        help="Delete input files after conversion. DO NOT USE THIS IF YOU DON'T HAVE COMPLETE CONFIDENCE IN THIS TOOL.",
    )
    _ = command_parser.add_argument(
        "-o",
        "--output",
        help="Set output file name. Defaults to the name of the first input file with a .opus extension",
    )
    mg = command_parser.add_mutually_exclusive_group()
    _ = mg.add_argument(
        "-m",
        "--metadata",
        help="FFMETADATA file containing desired final metadata. Use -M if the metadata also contains chapter information",
    )
    _ = mg.add_argument(
        "-M",
        "--metadatachapter",
        help="FFMETADATA file containing desired final metadata along with chapter data. Use -m to preserve automatic chapter detection.",
    )
    _ = command_parser.add_argument(
        "-b",
        "--bitrate",
        help="Set bitrate for output file. Defaults to 32kbps for inputs under 192kbps, and 192kbps for inputs above that threshold.",
    )
    _ = command_parser.add_argument(
        "-c",
        "--cuesheet",
        help="Set location for cuesheet file to read for chapter data. Only works if the input is a singular file.",
    )
    _ = command_parser.add_argument(
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
    _ = parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    logging_opts = parser.add_mutually_exclusive_group()
    _ = logging_opts.add_argument(
        "-q", "--quiet", help="Only log errors during conversion", action="store_true"
    )
    _ = logging_opts.add_argument(
        "-V",
        "--verbose",
        help="Log more information about the conversion process",
        action="store_true",
    )
    _ = parser.add_argument(
        "-t",
        "--threads",
        help="Number of subprocesses to spawn to convert books. Not specifying or 0 will default to core count.",
        type=int,
        action="store",
        default=0,
    )
    command_parser.set_modded_help_usage(parser.format_help(), parser.format_usage())
    global_args, command_args = parser.parse_known_args()
    global_args = cast(GlobalParserArgs, global_args)
    logging.getLogger().setLevel(logging.WARNING)
    if global_args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    if global_args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    chunks: list[CommandParserArgs] = []
    start = 0
    for i, curr in enumerate(command_args):
        if i != 0 and curr in {"-i", "--input", "-a", "--auto"}:
            chunks.append(
                cast(
                    CommandParserArgs, command_parser.parse_args(command_args[start:i])
                )
            )
            start = i
    chunks.append(
        cast(CommandParserArgs, command_parser.parse_args(command_args[start:]))
    )
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
                    f"Completed conversion and merger into {
                        print_str}: ({i+1}/{total_amount})"
                )
            else:
                single_process_logger.error(
                    f"Failed to convert {print_str}: ({i+1}/{total_amount})"
                )
