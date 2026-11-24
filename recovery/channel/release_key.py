"""Manage the Windows-protected signing key and sign checked release installers."""

import argparse
import base64
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


BACKUP_FORMAT = b"TransportNSK release key v2\n"


class DataBlob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, protect):
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = DataBlob(len(data), buffer), DataBlob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    description = wintypes.LPCWSTR if protect else ctypes.POINTER(wintypes.LPWSTR)
    function.argtypes = [ctypes.POINTER(DataBlob), description, ctypes.POINTER(DataBlob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(result.data, result.length)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if result.data:
            ctypes.memset(result.data, 0, result.length)
            kernel32.LocalFree(result.data)


def write_new(path, contents):
    with path.open("xb") as output:
        output.write(contents)


def public_key(key):
    return base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")


def check_public_key(key, config):
    if public_key(key) != json.loads(config.read_text(encoding="utf-8"))["public_key"]:
        raise ValueError("The signing key does not match the update channel's public key.")


def initialize(key_path, config, feed_url, backup):
    recovery_code = key_path.with_name("recovery-code.txt")
    for path in (key_path, backup, recovery_code, config):
        if path.exists() or not path.parent.is_dir():
            raise ValueError(f"Expected a new file in an existing directory: {path}")
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes_raw()
    backup_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted = AESGCM(backup_key).encrypt(nonce, raw, BACKUP_FORMAT)
    write_new(key_path, dpapi(raw, protect=True))
    write_new(backup, BACKUP_FORMAT + nonce + encrypted)
    write_new(recovery_code, base64.urlsafe_b64encode(backup_key) + b"\n")
    check = AESGCM(backup_key).decrypt(nonce, encrypted, BACKUP_FORMAT)
    if check != raw or dpapi(key_path.read_bytes(), protect=False) != raw:
        raise RuntimeError("The signing key backup verification failed.")
    config_data = {"feed_url": feed_url, "public_key": public_key(key)}
    write_new(config, (json.dumps(config_data, indent=2) + "\n").encode("ascii"))


def restore(key_path, config, backup):
    if key_path.exists():
        raise FileExistsError(key_path)
    data = backup.read_bytes()
    if not data.startswith(BACKUP_FORMAT):
        raise ValueError("Unexpected signing key backup format.")
    nonce, encrypted = data[len(BACKUP_FORMAT):len(BACKUP_FORMAT) + 12], data[len(BACKUP_FORMAT) + 12:]
    code = getpass.getpass("Recovery code: ").strip()
    raw = AESGCM(base64.urlsafe_b64decode(code)).decrypt(nonce, encrypted, BACKUP_FORMAT)
    check_public_key(Ed25519PrivateKey.from_private_bytes(raw), config)
    write_new(key_path, dpapi(raw, protect=True))


def sign(key_path, config, installer):
    key = Ed25519PrivateKey.from_private_bytes(dpapi(key_path.read_bytes(), protect=False))
    check_public_key(key, config)
    data = installer.read_bytes()
    signature = key.sign(data)
    key.public_key().verify(signature, data)
    print(json.dumps({"name": installer.name, "length": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "signature": base64.b64encode(signature).decode("ascii")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init")
    init_parser.add_argument("feed_url")
    init_parser.add_argument("backup", type=Path)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("backup", type=Path)
    sign_parser = commands.add_parser("sign")
    sign_parser.add_argument("installer", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The signing key is managed through Windows DPAPI.")
    if args.command == "init":
        initialize(args.key, args.config, args.feed_url, args.backup)
    elif args.command == "restore":
        restore(args.key, args.config, args.backup)
    else:
        sign(args.key, args.config, args.installer)


if __name__ == "__main__":
    main()
