from typing import override

import setuptools.command.build
from setuptools import Command, setup


class build_parser(Command):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.build_lib = None
        self.editable_mode = False

    @override
    def run(self):
        from lark import Lark
        from lark.tools.standalone import gen_standalone

        with (
            open("src/yaacs/cue/cue.lark", "r") as grammar_file,
            open(f"{self.build_lib}/yaacs/cue/cue.py", "w") as out_file,
        ):
            lalrParser = Lark(
                grammar_file, parser="lalr", start=["start", "file", "track"]
            )
            gen_standalone(lalrParser, out=out_file)

    @override
    def initialize_options(self) -> None:
        self.build_lib = None
        self.editable_mode = False

    @override
    def finalize_options(self):
        self.set_undefined_options("build_py", ("build_lib", "build_lib"))

    def get_outputs(self) -> list[str]:
        return [f"{self.build_lib}/yaacs/cue/cue.py"]

    def get_output_mapping(self) -> dict[str, str]:
        return {f"{self.build_lib}/yaacs/cue/cue.py": "src/yaacs/cue/cue.lark"}

    def get_source_files(self):
        return ["src/yaacs/cue/cue.lark"]


setuptools.command.build.build.sub_commands.append(("build_lark", None))
setup(cmdclass={"build_lark": build_parser})
