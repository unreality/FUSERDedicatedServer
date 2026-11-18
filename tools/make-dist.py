"""Package the staged FDS server for distribution (docs/STEP6_FUSERD.md, 6.1).

--platform Linux (default)
  Input: build/package-linux/LinuxServer (tools/build-probe.ps1 -Mode Package -Platform Linux).
  Output in build/dist:
    fds-server-linux-<version>.tar.gz          the server tree plus MANIFEST.json, without debug symbols
    fds-server-linux-<version>-symbols.tar.gz  FuserServerServer.debug / .sym
    fds-server-linux-<version>.MANIFEST.json   a copy of the manifest, for records
  and build/docker, a self-contained Docker build context to copy to the Docker host (docker build -t fds-server:<v> .):
    Dockerfile, entrypoint.sh, README.md (from S9_DedicatedServer/docker), fds-server-linux.tar.gz (the server tarball
    under a fixed name, the Dockerfile's default), MANIFEST.json and VERSION. The folder is recreated on every run.
  Entries are rooted at the tree's top (Engine/, Fuser/, FuserServerServer.sh, MANIFEST.json), owned by 0:0, files
  0644 and directories 0755, except the server binary and launcher script (0755), so the image needs no chmod. Built
  with Python's tarfile so modes don't depend on the filesystem the tree sits on (/mnt/c shows every file executable).

--platform Win64
  Input: build/package/WindowsServer (tools/build-probe.ps1 -Mode Package -Platform Win64).
  Output in build/dist:
    fds-server-windows-<version>.zip                the server tree plus MANIFEST.json, without debug symbols
    fds-server-windows-<version>-symbols.zip        FuserServerServer.pdb
    fds-server-windows-<version>.MANIFEST.json      a copy of the manifest, for records
  Entries are rooted at the tree's top (Engine/, Fuser/, FuserServerServer.exe, MANIFEST.json). build/docker is not
  touched.

Usage: python make-dist.py [--platform Linux|Win64] [--version V] [--package DIR] [--out DIR] [--docker DIR]
"""
import argparse
import datetime
import hashlib
import io
import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

FDS = Path(__file__).resolve().parent.parent
NET_VERSION = 10402  # FUSER EGS v1.4.2 (FDSGameSession / docs/M2_CONNECTION.md)
SYMBOL_SUFFIXES = (".debug", ".sym", ".pdb")
PLATFORMS = {
    "Linux": {
        "package": FDS / "build" / "package-linux" / "LinuxServer",
        "binary": "Fuser/Binaries/Linux/FuserServerServer",
        "manifest_platform": "Linux-x86_64",
        "name": "linux",
        "ext": ".tar.gz",
    },
    "Win64": {
        "package": FDS / "build" / "package" / "WindowsServer",
        "binary": "Fuser/Binaries/Win64/FuserServerServer.exe",
        "manifest_platform": "Windows-x86_64",
        "name": "windows",
        "ext": ".zip",
    },
}
LINUX_EXECUTABLES = {PLATFORMS["Linux"]["binary"], "FuserServerServer.sh"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tar_info(name, size, mode, mtime):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, mode, mtime
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def write_tar(path, root, members, mtime, extra=None):
    dirs = set()
    with tarfile.open(path, "w:gz", compresslevel=6) as tar:
        for p in members:
            rel = p.relative_to(root).as_posix()
            for parent in list(Path(rel).parents)[::-1][1:]:
                d = parent.as_posix()
                if d not in dirs:
                    dirs.add(d)
                    info = tar_info(d, 0, 0o755, mtime)
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
            info = tar_info(rel, p.stat().st_size, 0o755 if rel in LINUX_EXECUTABLES else 0o644, int(p.stat().st_mtime))
            with open(p, "rb") as f:
                tar.addfile(info, f)
        if extra:
            name, data = extra
            tar.addfile(tar_info(name, len(data), 0o644, mtime), io.BytesIO(data))


def write_zip(path, root, members, mtime, extra=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for p in members:
            z.write(p, p.relative_to(root).as_posix())
        if extra:
            name, data = extra
            info = zipfile.ZipInfo(name, datetime.datetime.fromtimestamp(mtime).timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def write_docker_context(docker, server_archive, manifest_bytes, version):
    if docker.exists():
        shutil.rmtree(docker)
    docker.mkdir(parents=True)
    for name in ("Dockerfile", "entrypoint.sh", "README.md"):
        shutil.copyfile(FDS / "docker" / name, docker / name)  # byte copy: entrypoint.sh keeps LF endings
    shutil.copyfile(server_archive, docker / "fds-server-linux.tar.gz")
    (docker / "MANIFEST.json").write_bytes(manifest_bytes)
    (docker / "VERSION").write_bytes(f"{version}\n".encode("ascii"))
    print(f"{docker} (Docker build context: docker build -t fds-server:{version} .)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=sorted(PLATFORMS), default="Linux")
    ap.add_argument("--version", default=datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--package", default=None, help="staged server tree (default: the platform's build/package folder)")
    ap.add_argument("--out", default=str(FDS / "build" / "dist"))
    ap.add_argument("--docker", default=str(FDS / "build" / "docker"), help="Docker build context (Linux only)")
    a = ap.parse_args()
    plat = PLATFORMS[a.platform]
    root, out = Path(a.package) if a.package else plat["package"], Path(a.out)
    if not (root / plat["binary"]).is_file():
        sys.exit(f"no staged {a.platform} server at {root} (run build-probe.ps1 -Mode Package -Platform {a.platform})")
    out.mkdir(parents=True, exist_ok=True)
    # Fuser/Saved is runtime state (local test runs from the package folder write config and logs there); a fresh
    # install gets a fresh, writable one.
    saved = root / "Fuser" / "Saved"
    files = sorted(p for p in root.rglob("*") if p.is_file() and saved not in p.parents)
    server = [p for p in files if not p.name.endswith(SYMBOL_SUFFIXES)]
    symbols = [p for p in files if p.name.endswith(SYMBOL_SUFFIXES)]
    built = datetime.datetime.now(datetime.timezone.utc)
    manifest = {
        "name": "fds-server",
        "version": a.version,
        "built": built.isoformat(timespec="seconds"),
        "engine": "4.24.3",
        "netVersion": NET_VERSION,
        "platform": plat["manifest_platform"],
        "files": [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size, "sha256": sha256(p)} for p in server],
    }
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    mtime = int(built.timestamp())
    base = f"fds-server-{plat['name']}-{a.version}"
    write = write_tar if plat["ext"] == ".tar.gz" else write_zip

    server_archive = out / f"{base}{plat['ext']}"
    write(server_archive, root, server, mtime, ("MANIFEST.json", manifest_bytes))
    (out / f"{base}.MANIFEST.json").write_bytes(manifest_bytes)
    print(f"{server_archive} ({server_archive.stat().st_size / 1e6:.1f} MB, {len(server)} files)")
    if symbols:
        symbols_archive = out / f"{base}-symbols{plat['ext']}"
        write(symbols_archive, root, symbols, mtime)
        print(f"{symbols_archive} ({symbols_archive.stat().st_size / 1e6:.1f} MB)")

    if a.platform == "Linux":
        write_docker_context(Path(a.docker), server_archive, manifest_bytes, a.version)
    print(f"version {a.version}")


if __name__ == "__main__":
    main()
