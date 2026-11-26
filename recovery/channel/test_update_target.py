"""Verify that the embedded controller sends the executable directory for portable updates."""

import json
import os
from pathlib import Path
import sys
from threading import Event
from types import ModuleType, SimpleNamespace

from PyInstaller.archive.readers import CArchiveReader


def installer_arguments(bundle, executable, workspace, installer):
    sys.path[:0] = [str(bundle / "pyc"), str(bundle / "runtime")]
    sys._MEIPASS = str(bundle / "resources")
    sys.frozen = True
    sys.executable = str(executable)
    qt = bundle / "runtime" / "PySide6"
    args = []
    with os.add_dll_directory(str(qt)):
        archive = CArchiveReader(executable).open_embedded_archive("PYZ.pyz")
        for name in ("_updates_release_123", "updates"):
            module = ModuleType(name)
            sys.modules[name] = module
            exec(archive.extract(name), module.__dict__)
        released = sys.modules["_updates_release_123"]
        released.subprocess = SimpleNamespace(CREATE_NO_WINDOW=0, Popen=lambda values, **kw: args.extend(values))
        controller = SimpleNamespace(allow_shutdown=True, stopping=Event(), root=workspace)
        if not module.UpdateController.launch_verified_installer(controller, installer):
            raise RuntimeError("The portable controller did not start the verified installer.")

    assert args[0] == str(installer.resolve())
    assert f"/DIR={executable.parent}" in args and f"/WORKSPACE={workspace}" in args
    assert "/FROMUPDATE=1" in args
    return args[1:]


if __name__ == "__main__":
    print(json.dumps(installer_arguments(*(Path(s).resolve() for s in sys.argv[1:]))))
