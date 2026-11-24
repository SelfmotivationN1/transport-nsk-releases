"""Build the Transport NSK 1.2.4 portable preview from the verified 1.2.3 executable."""

import argparse
import hashlib
import importlib.util
import marshal
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter

from transport_recovery import APP_SHA256


VERSION = b"1.2.4\n"
PYZ_NAME = "PYZ.pyz"
RELEASE_MODULE = "_pdf_delivery_release_123"


def patch_library(library, source):
    if library[:8] != b"PYZ\0" + importlib.util.MAGIC_NUMBER:
        raise ValueError("Unexpected embedded Python archive format.")
    toc_offset = struct.unpack_from("!i", library, 8)[0]
    toc = dict(marshal.loads(library[toc_offset:]))
    if RELEASE_MODULE in toc or toc["pdf_delivery"][0] != 0:
        raise ValueError("Unexpected PDF module entries.")
    toc[RELEASE_MODULE] = toc["pdf_delivery"]
    code = compile(source.read_text(encoding="utf-8"), "pdf_delivery.py", "exec", optimize=0)
    replacement = zlib.compress(marshal.dumps(code), 6)
    toc["pdf_delivery"] = (0, toc_offset, len(replacement))
    new_offset = struct.pack("!i", toc_offset + len(replacement))
    return library[:8] + new_offset + library[12:toc_offset] + replacement + marshal.dumps(list(toc.items()))


def write_preview(original, target, archive, replacements):
    with original.open("rb") as source, target.open("wb") as output:
        source.seek(archive._end_offset - archive._COOKIE_LENGTH)
        cookie = source.read(archive._COOKIE_LENGTH)
        magic, _, _, _, python_version, python_library = struct.unpack(archive._COOKIE_FORMAT, cookie)
        if python_version != 312 or archive._end_offset != original.stat().st_size:
            raise ValueError("Unexpected release executable layout.")

        source.seek(0)
        output.write(source.read(archive._start_offset))
        archive_start = output.tell()
        toc = [(0, 0, 0, 0, "o", option) for option in archive.options]

        for name, (offset, stored_size, size, compression, typecode) in archive.toc.items():
            destination_offset = output.tell() - archive_start
            if name in replacements:
                entry = replacements[name]
                size = len(entry)
                data = zlib.compress(entry, 9) if compression else entry
            else:
                source.seek(archive_start + offset)
                data = source.read(stored_size)
            output.write(data)
            toc.append((destination_offset, len(data), size, compression, typecode, name))

        toc_offset = output.tell() - archive_start
        toc_data = CArchiveWriter._serialize_toc(toc)
        output.write(toc_data)
        archive_size = toc_offset + len(toc_data) + archive._COOKIE_LENGTH
        output.write(struct.pack(archive._COOKIE_FORMAT, magic, archive_size, toc_offset,
                                 len(toc_data), python_version, python_library))


def build(original, output):
    with original.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != APP_SHA256:
            raise ValueError("The input executable must be the verified release 1.2.3.")
    archive = CArchiveReader(original)
    override = Path(__file__).with_name("overrides") / "pdf_delivery.py"
    library = patch_library(archive.extract(PYZ_NAME), override)
    with tempfile.TemporaryDirectory(dir=output.parent) as working_dir:
        candidate = Path(working_dir) / output.name
        write_preview(original, candidate, archive, {PYZ_NAME: library, "VERSION": VERSION})
        preview = CArchiveReader(candidate)
        if preview.extract("VERSION") != VERSION:
            raise RuntimeError("Preview version verification failed.")
        bundled = preview.open_embedded_archive(PYZ_NAME)
        if bundled.extract(RELEASE_MODULE) != archive.open_embedded_archive(PYZ_NAME).extract("pdf_delivery"):
            raise RuntimeError("The bundled release PDF routines have changed.")
        if bundled.extract("pdf_delivery") != compile(override.read_text(encoding="utf-8"), "pdf_delivery.py", "exec", optimize=0):
            raise RuntimeError("The bundled PDF fix differs from its source.")
        with candidate.open("rb") as source, output.open("xb") as target:
            shutil.copyfileobj(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32:
        parser.error("Use CPython 3.12 x64 on Windows.")
    build(args.original.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
