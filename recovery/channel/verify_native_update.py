"""On an isolated Windows test runner, exercise the shipped WinSparkle updater."""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading

from PyInstaller.archive.readers import CArchiveReader


def download(installed_application, output, expected_sha):
    archive = CArchiveReader(installed_application)
    config = json.loads(archive.extract("release-config.json"))
    trust = json.loads(archive.extract("update-trust.json"))
    version = archive.extract("VERSION").decode("ascii").strip()
    with tempfile.TemporaryDirectory() as working:
        path = Path(working) / "WinSparkle.dll"
        path.write_bytes(archive.extract(r"vendor\winsparkle\WinSparkle.dll"))
        library = ctypes.CDLL(str(path))
        handle = library._handle
        event = threading.Event()
        results = []
        notify = ctypes.CFUNCTYPE(None)
        can_shutdown = ctypes.CFUNCTYPE(ctypes.c_int)
        run_installer = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_wchar_p)

        def call(name, types=(), values=(), result=None):
            function = getattr(library, "win_sparkle_" + name)
            function.argtypes = types
            function.restype = result
            return function(*values)

        def failed(message):
            results.append(message)
            event.set()

        @run_installer
        def receive(downloaded):
            try:
                with Path(downloaded).open("rb") as payload:
                    if hashlib.file_digest(payload, "sha256").hexdigest() != expected_sha:
                        raise ValueError("WinSparkle downloaded an unexpected installer.")
                shutil.copyfile(downloaded, output)
                results.append("verified")
                return 1
            except Exception as error:
                results.append(str(error))
                return -1
            finally:
                event.set()

        callbacks = {
            "set_error_callback": notify(lambda: failed("WinSparkle failed to download or verify the update.")),
            "set_did_not_find_update_callback": notify(lambda: failed("WinSparkle did not find a newer version.")),
            "set_update_cancelled_callback": notify(lambda: failed("WinSparkle cancelled the update.")),
        }
        ready = can_shutdown(lambda: 1)
        initialized = False
        try:
            call("set_registry_path", [ctypes.c_char_p], [b"Software\\TransportNSKChannelV2Verification\\WinSparkle"])
            call("set_app_details", [ctypes.c_wchar_p] * 3, ["TransportNSK", "TransportNSK verification", version])
            call("set_appcast_url", [ctypes.c_char_p], [config["feed_url"].encode("ascii")])
            if call("set_eddsa_public_key", [ctypes.c_char_p], [trust["public_key"].encode("ascii")], ctypes.c_int) != 1:
                raise RuntimeError("WinSparkle rejected the packaged public key.")
            call("set_automatic_check_for_updates", [ctypes.c_int], [0])
            for name, callback in callbacks.items():
                call(name, [notify], [callback])
            call("set_can_shutdown_callback", [can_shutdown], [ready])
            call("set_user_run_installer_callback", [run_installer], [receive])
            call("init")
            initialized = True
            call("check_update_with_ui_and_install")
            if not event.wait(120) or results[0] != "verified":
                raise RuntimeError(results[0] if results else "WinSparkle update timed out.")
        finally:
            if initialized:
                call("cleanup")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.FreeLibrary.argtypes = [ctypes.c_void_p]
            kernel.FreeLibrary(handle)
    print(f"WinSparkle {version} downloaded the installer and accepted its Ed25519 signature.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installed_application", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("expected_sha")
    args = parser.parse_args()
    download(args.installed_application, args.output, args.expected_sha)


if __name__ == "__main__":
    main()
