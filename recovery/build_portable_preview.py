"""Build Transport NSK from the verified 1.2.3 executable and local source overrides."""

import argparse
import base64
import hashlib
import importlib.util
import json
import marshal
import re
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from urllib.parse import urlsplit

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter

from transport_recovery import APP_SHA256


PYZ_NAME = "PYZ.pyz"


def patch_library(library, overlays):
    if library[:8] != b"PYZ\0" + importlib.util.MAGIC_NUMBER:
        raise ValueError("Unexpected embedded Python archive format.")
    toc_offset = struct.unpack_from("!i", library, 8)[0]
    toc = dict(marshal.loads(library[toc_offset:]))
    replacements = bytearray()
    for name, code in overlays.items():
        released_name = f"_{name}_release_123"
        if released_name in toc or toc[name][0] != 0:
            raise ValueError(f"Unexpected archive entries for {name}.")
        toc[released_name] = toc[name]
        replacement = zlib.compress(marshal.dumps(code), 6)
        toc[name] = (0, toc_offset + len(replacements), len(replacement))
        replacements.extend(replacement)
    new_offset = struct.pack("!i", toc_offset + len(replacements))
    return library[:8] + new_offset + library[12:toc_offset] + replacements + marshal.dumps(list(toc.items()))


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


def channel_resources(archive, channel):
    feed_url = channel["feed_url"]
    url = urlsplit(feed_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("The update feed must be an HTTPS URL without credentials.")
    public_key = channel["public_key"]
    if len(base64.b64decode(public_key, validate=True)) != 32:
        raise ValueError("The update signing key must be a base64 Ed25519 public key.")
    config = json.loads(archive.extract("release-config.json"))
    config["legacy_feed_url"] = config["feed_url"]
    config["feed_url"] = config["channel_feed_url"] = feed_url
    return {
        "release-config.json": (json.dumps(config, ensure_ascii=False) + "\n").encode("utf-8"),
        "update-trust.json": (json.dumps({"public_key": public_key}) + "\n").encode("utf-8"),
    }


def build(original, output, version="1.2.4", channel=None):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Use a three-part numeric version.")
    with original.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != APP_SHA256:
            raise ValueError("The input executable must be the verified release 1.2.3.")
    archive = CArchiveReader(original)
    override_dir = Path(__file__).with_name("overrides")
    sources = {"pdf_delivery": override_dir / "pdf_delivery.py"}
    resources = {"VERSION": (version + "\n").encode("ascii")}
    if channel is not None:
        sources["updates"] = Path(__file__).with_name("channel") / "updates.py"
        resources.update(channel_resources(archive, channel))
    overlays = {
        name: compile(source.read_text(encoding="utf-8"), f"{name}.py", "exec", optimize=0)
        for name, source in sources.items()
    }
    library = patch_library(archive.extract(PYZ_NAME), overlays)
    with tempfile.TemporaryDirectory(dir=output.parent) as working_dir:
        candidate = Path(working_dir) / output.name
        write_preview(original, candidate, archive, {PYZ_NAME: library, **resources})
        preview = CArchiveReader(candidate)
        for name, data in resources.items():
            if preview.extract(name) != data:
                raise RuntimeError(f"Bundled resource verification failed for {name}.")
        bundled = preview.open_embedded_archive(PYZ_NAME)
        released = archive.open_embedded_archive(PYZ_NAME)
        for name, code in overlays.items():
            if bundled.extract(f"_{name}_release_123") != released.extract(name) or bundled.extract(name) != code:
                raise RuntimeError(f"Bundled code verification failed for {name}.")
        with candidate.open("rb") as source, output.open("xb") as target:
            shutil.copyfileobj(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", default="1.2.4")
    parser.add_argument("--channel-config", type=Path, help="JSON with the HTTPS feed URL and public signing key.")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32:
        parser.error("Use CPython 3.12 x64 on Windows.")
    channel = json.loads(args.channel_config.read_text(encoding="utf-8")) if args.channel_config else None
    build(args.original.resolve(), args.output.resolve(), args.version, channel)


if __name__ == "__main__":
    main()
