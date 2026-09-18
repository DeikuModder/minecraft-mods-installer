import os
import shutil

from collections import namedtuple

SyncResult = namedtuple("SyncResult", "installed updated")


def count_files(folder):
    if not os.path.isdir(folder):
        return 0
    return sum(1 for e in os.scandir(folder) if e.is_file())


def scan_sizes(folder):
    sizes = {}
    with os.scandir(folder) as it:
        for e in it:
            if e.is_file():
                sizes[e.name] = e.stat().st_size
    return sizes


def plan_sync(src, dst):
    src_sizes = scan_sizes(src)
    dst_sizes = scan_sizes(dst) if os.path.isdir(dst) else {}
    to_copy = [name for name, size in src_sizes.items() if name not in dst_sizes]
    to_overwrite = [
        name for name, size in src_sizes.items()
        if name in dst_sizes and dst_sizes[name] != size
    ]
    return to_copy, to_overwrite


def execute_plan(src, dst, to_copy, to_overwrite, progress_cb=None):
    if not os.path.isdir(dst):
        os.makedirs(dst)
    total = len(to_copy) + len(to_overwrite)
    done = 0
    result = SyncResult(installed=[], updated=[])
    for name in to_copy:
        shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
        result.installed.append(name)
        done += 1
        if progress_cb:
            progress_cb(done, total, name)
    for name in to_overwrite:
        shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
        result.updated.append(name)
        done += 1
        if progress_cb:
            progress_cb(done, total, name)
    return result