"""Recover a local Python development environment from Transport NSK 1.2.3."""

import argparse
import dis
import hashlib
import importlib.util
import marshal
import os
import runpy
import sys
from pathlib import Path, PureWindowsPath


APP_SHA256 = "ccf2329c8a175a8daedfa16c3bcd8284d1e4717dff5620bc99ce48f9e733cdb3"
RESOURCE_NAMES = {"VERSION", "release-config.json", "update-trust.json", "codex-rejected-cas.pem"}
RESOURCE_DIRS = {"assets", "licenses", "translations", "vendor"}


def archive_path(name):
    path = PureWindowsPath(name)
    if path.anchor or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe path in archive: {name}")
    return Path(*path.parts)


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_bytecode(path, code):
    write_bytes(path, importlib.util.MAGIC_NUMBER + bytes(12) + marshal.dumps(code))


def extract(executable, destination):
    from PyInstaller.archive.readers import CArchiveReader

    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != APP_SHA256:
        raise ValueError("SHA-256 differs from the verified Transport NSK 1.2.3 executable.")
    archive = CArchiveReader(executable)
    library = archive.open_embedded_archive("PYZ.pyz")
    destination.mkdir(parents=True)
    bytecode = destination / "pyc"
    runtime = destination / "runtime"
    resources = destination / "resources"
    modules = {"app": marshal.loads(archive.extract("app"))}
    for name, entry in library.toc.items():
        package, _, _ = name.partition(".")
        if package in sys.stdlib_module_names:
            continue
        parts = name.split(".")
        if not all(part.isidentifier() for part in parts):
            raise ValueError(f"Invalid module name in archive: {name}")
        target = runtime.joinpath(*parts)
        if entry[0] == 3:
            target.mkdir(parents=True, exist_ok=True)
            continue
        code = library.extract(name)
        if code.co_filename == f"{name}.py" and name != "typing_extensions":
            modules[name] = code
        elif entry[0] == 1:
            write_bytecode(target / "__init__.pyc", code)
        else:
            write_bytecode(target.with_suffix(".pyc"), code)

    for name in archive.toc:
        path = archive_path(name)
        first = path.parts[0]
        if first in RESOURCE_DIRS or name in RESOURCE_NAMES:
            write_bytes(resources / path, archive.extract(name))
        elif (runtime / first).is_dir() or (len(path.parts) > 1 and first.startswith(("numpy", "ortools"))):
            write_bytes(runtime / path, archive.extract(name))

    listings = destination / "disassembly"
    listings.mkdir()
    for name, code in sorted(modules.items()):
        target = bytecode / f"{name}.pyc"
        write_bytecode(target, code)
        loader = importlib.machinery.SourcelessFileLoader(name, str(target))
        if loader.get_code(name) != code:
            raise RuntimeError(f"Extracted bytecode verification failed: {name}")
        with (listings / f"{name}.txt").open("w", encoding="utf-8") as stream:
            dis.dis(code, file=stream)
    print(f"Recovered {len(modules)} modules into {destination}")


def run(bundle, workspace, source, smoke_test):
    runtime = bundle / "runtime"
    pyc = bundle / "pyc"
    resources = bundle / "resources"
    if not (pyc / "app.pyc").is_file() or not (runtime / "PySide6").is_dir():
        raise ValueError("Extract the verified executable before launching.")

    os.environ["TRANSPORT_NSK_WORKSPACE"] = str(workspace)
    os.environ["QT_PLUGIN_PATH"] = str(runtime / "PySide6" / "plugins")
    sys._MEIPASS = str(resources)
    overrides = Path(__file__).with_name("overrides")
    sys.path[:0] = [str(path) for path in (source, overrides, pyc, runtime) if path is not None]
    sys.argv = [str(pyc / "app.pyc")]
    if smoke_test is not None:
        sys.argv.extend(("--smoke-test", str(smoke_test)))

    with os.add_dll_directory(str(runtime / "PySide6")):
        runpy.run_module("app", run_name="__main__", alter_sys=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recover = commands.add_parser("extract", help="Extract bytecode and bundled dependencies.")
    recover.add_argument("executable", type=Path)
    recover.add_argument("destination", type=Path)
    launch = commands.add_parser("run", help="Launch recovered bytecode with optional .py overrides.")
    launch.add_argument("bundle", type=Path)
    launch.add_argument("workspace", type=Path)
    launch.add_argument("--source", type=Path, help="Directory with replacement Python modules.")
    launch.add_argument("--smoke-test", type=Path, metavar="RESULT_JSON")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32:
        parser.error("Use CPython 3.12 x64 on Windows.")
    if args.command == "extract":
        extract(args.executable.resolve(), args.destination.resolve())
    else:
        run(
            args.bundle.resolve(),
            args.workspace.resolve(),
            args.source.resolve() if args.source is not None else None,
            args.smoke_test.resolve() if args.smoke_test is not None else None,
        )


if __name__ == "__main__":
    main()
