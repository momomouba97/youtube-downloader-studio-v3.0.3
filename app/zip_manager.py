"""ZIP export of downloaded videos. Pure Python. Videos are already compressed, so entries are stored
(not deflated): fast, and progress is measured in bytes."""
import errno
import os
import shutil
import zipfile
from pathlib import Path

CHUNK = 1024 * 1024


class ZipError(Exception):
    pass


class ZipCancelled(Exception):
    pass


def unique_arcnames(paths):
    seen, out = {}, []
    for p in paths:
        base = Path(p).name
        stem, ext = os.path.splitext(base)
        name, n = base, 1
        while name.lower() in seen:
            n += 1
            name = f"{stem} ({n}){ext}"
        seen[name.lower()] = True
        out.append(name)
    return out


def unique_path(path):
    path = Path(path)
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists():
            return cand
        n += 1


def create_zip(files, dest, progress=None, should_cancel=None, free_space=None):
    """Create dest (.zip) from files. Returns {path, count, size, skipped}.
    Raises ZipError (clear message) or ZipCancelled. A partial archive is always removed."""
    dest = Path(dest)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip") if dest.suffix else Path(str(dest) + ".zip")
    present = [str(f) for f in files if os.path.isfile(f)]
    skipped = [str(f) for f in files if not os.path.isfile(f)]
    if not present:
        raise ZipError("None of the selected files exist on disk.")
    total = sum(os.path.getsize(f) for f in present)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        free = (free_space or (lambda d: shutil.disk_usage(d).free))(str(dest.parent))
    except OSError as exc:
        raise ZipError(f"Cannot write to the destination folder: {exc.strerror or exc}") from exc
    if free < total + 4 * CHUNK:
        raise ZipError(f"Not enough free space: the archive needs about {total // 1048576} MB "
                       f"but only {free // 1048576} MB is free.")
    dest = unique_path(dest)
    tmp = dest.with_name(dest.name + ".part")
    names = unique_arcnames(present)
    done = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path, arc in zip(present, names):
                zi = zipfile.ZipInfo.from_file(path, arc)
                zi.compress_type = zipfile.ZIP_STORED
                with open(path, "rb") as src, zf.open(zi, "w", force_zip64=True) as out:
                    while True:
                        if should_cancel and should_cancel():
                            raise ZipCancelled()
                        buf = src.read(CHUNK)
                        if not buf:
                            break
                        out.write(buf)
                        done += len(buf)
                        if progress:
                            progress(done, total, arc)
        os.replace(tmp, dest)
    except ZipCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise ZipError("The disk became full while creating the archive. Free some space and try again.") from exc
        raise ZipError(f"Could not create the archive: {exc.strerror or exc}") from exc
    except zipfile.BadZipFile as exc:
        tmp.unlink(missing_ok=True)
        raise ZipError(f"Compression failed: {exc}") from exc
    return {"path": str(dest), "count": len(present), "size": os.path.getsize(dest), "skipped": skipped}
