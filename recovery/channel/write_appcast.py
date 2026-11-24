"""Write a WinSparkle feed for a signed Transport NSK installer."""

import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import re
from xml.etree import ElementTree as xml


SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
DOWNLOADS = "https://github.com/SelfmotivationN1/transport-nsk-releases/releases/download"


def write_feed(output, version, signature_info, notes):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Use a three-part numeric version.")
    name = f"TransportNSK-{version}-Setup.exe"
    if signature_info["name"] != name or signature_info["length"] <= 0:
        raise ValueError("Signature metadata does not describe the requested installer.")
    signature = signature_info["signature"]
    if len(base64.b64decode(signature, validate=True)) != 64:
        raise ValueError("Expected a base64 Ed25519 signature.")

    xml.register_namespace("sparkle", SPARKLE)
    rss = xml.Element("rss", {"version": "2.0"})
    channel = xml.SubElement(rss, "channel")
    xml.SubElement(channel, "title").text = "Транспорт НСК — обновления"
    xml.SubElement(channel, "language").text = "ru"
    item = xml.SubElement(channel, "item")
    xml.SubElement(item, "title").text = f"Транспорт НСК {version}"
    xml.SubElement(item, "description").text = notes
    xml.SubElement(item, "pubDate").text = format_datetime(datetime.now(timezone.utc), usegmt=True)
    attributes = {"version": version, "shortVersionString": version, "os": "windows", "edSignature": signature}
    xml.SubElement(item, "enclosure", {
        "url": f"{DOWNLOADS}/v{version}-channel-v2/{name}",
        "length": str(signature_info["length"]),
        "type": "application/octet-stream",
        **{f"{{{SPARKLE}}}{key}": value for key, value in attributes.items()},
    })
    xml.indent(rss)
    xml.ElementTree(rss).write(output, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("version")
    parser.add_argument("signature_info", type=Path)
    parser.add_argument("notes")
    args = parser.parse_args()
    write_feed(args.output, args.version, json.loads(args.signature_info.read_text(encoding="utf-8")), args.notes)


if __name__ == "__main__":
    main()
