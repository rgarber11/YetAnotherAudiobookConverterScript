# YAACS

---

Convert your audiobooks to .opus for smaller files at equivalent quality.

## Features

- Automatic bitrate selection
- (opt-in) Automatic audiobook discovery
- Automatic cover image discovery
- Automatic chapter discovery
- (opt-in) Automatic deletion of input files
- Multiprocessing to increase conversion throughput

## Dependencies

YAACS depends on the following system packages for conversion:

- [FFmpeg](https://ffmpeg.org/) for metadata detection and conversion

YAACS also depends on the following Python packages for conversion:

- [Mutagen](https://mutagen.readthedocs.io/en/latest/) for cover image installation

Building YAACS depends on the following Python packages:

- [Lark](https://github.com/lark-parser/lark) for generating CUE parser code.
- [Setuptools](https://github.com/pypa/setuptools) for creating the final Python wheel.
- [Build](https://github.com/pypa/build) as a standardized Python build frontend.

## Building

Prepackaged versions of YAACS are available in this repo. However, building YAACS is also possible.

1. First, setup a build environment. If you are confused, an example of how to do this is given below:

```bash
python -m venv venv
source venv/bin/activate # (bash/zsh) command to activate Virtual Environment
venv\Scripts\activate.bat # Windows cmd.exe command to activate Virtual Environment
```

2. Next, install YAACS' dev dependencies:

```bash
pip install -r requirements-dev.txt # Installs the lark parser, along with build software for Python

```

3. Now, you can run the standardized Python build command.

```bash
python -m build
```

4. And lastly you can install YAACS.

```bash
pip install dist/yaacs-2.0.0-py3-none-any.whl # Note that your version of YAACS may generate a differently named file.
```

## Installation

YAACS is available for installation via [PyPI](https://pypi.org/project/yaacs/) and on the [AUR](https://aur.archlinux.org/packages/yaacs). Feel free to package YAACS for other systems!

## Usage

```sh
$ yaacs -h
usage: yaacs [-h] [-v] [-t THREADS] [(-i INPUT [INPUT ...] | -a LOCATION [LOCATION ...]) [-x] [-o OUTPUT] [-m METADATA | -M METADATACHAPTER] [-b BITRATE] [-c CUESHEET] [-I COVER]]+

A Script to convert audiobooks to .opus

options:
  -h, --help            show this help message and exit
  -v, --version         show program's version number and exit
  -t THREADS, --threads THREADS
                        Number of subprocesses to spawn to convert books. Not specifying or 0 will default to core count.
  -i INPUT [INPUT ...], --input INPUT [INPUT ...]
                        Locations of files for conversion. If this is a directory, all audio files recursively contained will be merged into one file.
  -a LOCATION [LOCATION ...], --auto LOCATION [LOCATION ...]
                        Locations to auto-convert. Will recursively search for subfolders which contain no other directories and contain audio file(s). These files will be converted/merged.
  -x, --delete          Delete input files after conversion. DO NOT USE THIS IF YOU DON'T HAVE COMPLETE CONFIDENCE IN THIS TOOL.
  -o OUTPUT, --output OUTPUT
                        Set output file name. Defaults to the name of the first input file with a .opus extension
  -m METADATA, --metadata METADATA
                        FFMETADATA file containing desired final metadata. Use -M if the metadata also contains chapter information
  -M METADATACHAPTER, --metadatachapter METADATACHAPTER
                        FFMETADATA file containing desired final metadata along with chapter data. Use -m to preserve automatic chapter detection.
  -b BITRATE, --bitrate BITRATE
                        Set bitrate for output file. Defaults to 32kbps for inputs under 192kbps, and 192kbps for inputs above that threshold.
  -c CUESHEET, --cuesheet CUESHEET
                        Set location for cuesheet file to read for chapter data. Only works if the input is a singular file.
  -I COVER, --cover COVER
                        Explicitly set final cover file. Will attempt to autodiscover cover if not set.
$ yaacs -a ~/Audiobooks -x # Automatically detect all audiobooks (folders with no subfolders containing audio files) in ~/Audiobooks and convert them. Delete after conversion.
$ yaacs -i Warbreaker -m warbreaker.ffmeta -b 64k # Convert the audiobook within the Warbreaker folder at a bitrate of 64kbps. Use the warbreaker.ffmeta file for metadata, but still auto-detect chapters.
```
