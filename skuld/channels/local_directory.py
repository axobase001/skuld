"""local_directory channel: an authorized directory. Each regular file below it is reported under its
relative POSIX path as {size, sha256}; symbolic links are not followed. Writes: WRITE_FILE (risk 1)
puts the prepared content at a relative path, DELETE_FILE (risk 3) removes one file; a path that
resolves outside the directory is refused before dispatch.
CHANGE_NOTE_13 (3): with scalar_keys true in the config, each file is also reported as two scalar keys,
file:<path>:sha256 and file:<path>:size, which observable contracts can use. Opt-in: a plain directory reports exactly
the structured values the S2 gate froze."""
from __future__ import annotations

import hashlib
import os
import stat
import errno
from datetime import datetime, timezone
from pathlib import Path

from skuld.channels.base import PolledChannel
from skuld.kernel.primitives import instant

MAX_FILE_BYTES = 50 * 1024 * 1024
PARTIAL_SUFFIX = ".skuld-partial"


class LocalDirectoryChannel(PolledChannel):
    kind = "local_directory"
    OPERATIONS = {"READ": 0, "WRITE_FILE": 1, "DELETE_FILE": 3}

    @classmethod
    def check_config(cls, runtime, config):
        path = Path(config["path"]).resolve()
        if not path.is_dir():
            raise ValueError("directory not found")
        identity=path.stat()
        out = {"path": str(path), "name": str(config.get("name") or path.name),
               "root_device":identity.st_dev,"root_inode":identity.st_ino}
        if config.get("scalar_keys") is True:
            out["scalar_keys"] = True
        if config.get('read_body') is True:
            limit=config.get('body_max_bytes',16384)
            formats=config.get('body_formats',['.txt','.md','.json'])
            if type(limit) is not int or not 1<=limit<=65536:raise ValueError('body_max_bytes must be 1..65536')
            if not isinstance(formats,list) or not formats or any(x not in ('.txt','.md','.json','.csv','.ics') for x in formats):raise ValueError('unsupported body format')
            out.update(read_body=True,body_max_bytes=limit,body_formats=sorted(set(formats)))
        return out

    def authorized_root(self):
        root=Path(self.config['path'])
        identity=root.lstat()  # Missing/unreadable roots are failures, never empty samples.
        if not stat.S_ISDIR(identity.st_mode):raise PermissionError('authorized root is not a directory (or was replaced by a symlink)')
        if 'root_inode' in self.config and (identity.st_dev,identity.st_ino)!=(self.config['root_device'],self.config['root_inode']):
            raise PermissionError('authorized root identity changed; explicit reauthorization required')
        return root

    def _root(self):
        return self.authorized_root()

    def _root_fd(self):
        root = self.authorized_root()
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        identity = os.fstat(fd)
        if 'root_inode' in self.config and (identity.st_dev, identity.st_ino) != (self.config['root_device'], self.config['root_inode']):
            os.close(fd)
            raise PermissionError('authorized root identity changed; explicit reauthorization required')
        return fd

    def _record_sample(self, out, rel, data, changed):
        digest = hashlib.sha256(data).hexdigest()
        out[rel] = ({'size': len(data), 'sha256': digest}, changed, {})
        if self.config.get('scalar_keys'):
            out[f'file:{rel}:sha256'] = (digest, changed, {})
            out[f'file:{rel}:size'] = (len(data), changed, {})
        if self.config.get('read_body'):
            provenance = {'capability': 'BODY_READ', 'relative_path': rel, 'sha256': digest, 'bytes': len(data)}
            suffix = Path(rel).suffix.lower()
            if suffix not in self.config['body_formats']:
                out[f'file:{rel}:body_status'] = ('UNSUPPORTED_FORMAT', changed, provenance)
            elif len(data) > self.config['body_max_bytes']:
                out[f'file:{rel}:body_status'] = ('TOO_LARGE', changed, provenance)
            else:
                try:
                    body = data.decode('utf-8')
                except UnicodeDecodeError:
                    out[f'file:{rel}:body_status'] = ('UNSUPPORTED_ENCODING', changed, provenance)
                else:
                    out[f'file:{rel}:body'] = (body, changed, provenance)
                    out[f'file:{rel}:body_status'] = ('READ', changed, provenance)

    def sample(self):
        root, out = self.authorized_root(), {}
        def fail(error):
            raise error
        if os.name == 'posix':
            root_fd = self._root_fd()
            try:
                for folder, dirs, files, dir_fd in os.fwalk('.', topdown=True, onerror=fail,
                                                             follow_symlinks=False, dir_fd=root_fd):
                    dirs[:] = sorted(d for d in dirs if not stat.S_ISLNK(os.stat(d, dir_fd=dir_fd, follow_symlinks=False).st_mode))
                    for name in sorted(files):
                        if name.endswith(PARTIAL_SUFFIX):
                            continue
                        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                            continue
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
                        with os.fdopen(file_fd, 'rb') as stream:
                            info = os.fstat(stream.fileno())
                            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                                continue
                            data = stream.read(MAX_FILE_BYTES + 1)
                        if len(data) > MAX_FILE_BYTES:
                            continue
                        rel = (Path(folder) / name).as_posix()
                        changed = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
                        self._record_sample(out, rel, data, changed)
            finally:
                os.close(root_fd)
        else:
            for folder, dirs, files in os.walk(root, topdown=True, onerror=fail, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
                for name in sorted(files):
                    p = Path(folder) / name
                    if name.endswith(PARTIAL_SUFFIX) or p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
                        continue
                    data = p.read_bytes()
                    changed = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
                    self._record_sample(out, p.relative_to(root).as_posix(), data, changed)
        return out

    def target(self, parameters):
        rel = parameters.get("path") if isinstance(parameters, dict) else None
        if (not isinstance(rel, str) or not rel or rel.startswith("/") or
                chr(92) in rel or chr(0) in rel):
            raise ValueError("a relative POSIX path is required")
        parts = rel.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("path outside the authorized directory")
        parent = self._root()
        for part in parts[:-1]:
            parent = parent / part
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError("path outside the authorized directory")
        target = parent / parts[-1]
        if target.is_symlink() or target.with_name(target.name + PARTIAL_SUFFIX).is_symlink():
            raise ValueError("symbolic link target is not authorized")
        return target

    def check_operation(self, operation, parameters):
        if operation not in ("WRITE_FILE", "DELETE_FILE"):
            raise ValueError(f"local_directory declares no write operation {operation}")
        if not isinstance(parameters, dict) or set(parameters) != {"path"}:
            raise ValueError("local_directory operations take exactly {path}")
        self.target(parameters)

    def perform(self, operation, parameters, content):
        self.check_operation(operation, parameters)
        target = self.target(parameters)
        name = target.name
        if os.name == "posix":
            fds = [self._root_fd()]
            try:
                for part in parameters["path"].split("/")[:-1]:
                    if operation == "WRITE_FILE":
                        try:
                            os.mkdir(part, dir_fd=fds[-1])
                        except FileExistsError:
                            pass
                    fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
                parent_fd = fds[-1]
                try:
                    existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    existing = None
                if existing and stat.S_ISLNK(existing.st_mode):
                    raise ValueError("symbolic link target is not authorized")
                if operation == "DELETE_FILE":
                    if not existing or not stat.S_ISREG(existing.st_mode):
                        return {"ok": False, "effect": {"path": parameters["path"], "error": "no such file"}}
                    os.unlink(name, dir_fd=parent_fd)
                    return {"ok": True, "effect": {"path": parameters["path"], "deleted": True}}
                if existing and stat.S_ISDIR(existing.st_mode):
                    return {"ok": False, "effect": {"path": parameters["path"], "error": "target is a directory"}}
                data = (content or "").encode("utf-8")
                partial = name + PARTIAL_SUFFIX
                file_fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=parent_fd)
                try:
                    with os.fdopen(file_fd, "wb") as stream:
                        stream.write(data)
                    stamp = instant(self.runtime.clock()).timestamp()
                    os.utime(partial, (stamp, stamp), dir_fd=parent_fd, follow_symlinks=False)
                    os.replace(partial, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                except BaseException:
                    os.unlink(partial, dir_fd=parent_fd)
                    raise
                return {"ok": True, "effect": {"path": parameters["path"], "size": len(data),
                                                "sha256": hashlib.sha256(data).hexdigest()}}
            finally:
                for fd in reversed(fds):
                    os.close(fd)
        if operation == "DELETE_FILE":
            if not target.is_file() or target.is_symlink():
                return {"ok": False, "effect": {"path": parameters["path"], "error": "no such file"}}
            target.unlink()
            return {"ok": True, "effect": {"path": parameters["path"], "deleted": True}}
        if target.is_dir():
            return {"ok": False, "effect": {"path": parameters["path"], "error": "target is a directory"}}
        data = (content or "").encode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        with partial.open("xb") as stream:
            stream.write(data)
        stamp = instant(self.runtime.clock()).timestamp()
        os.utime(partial, (stamp, stamp))
        os.replace(partial, target)
        return {"ok": True, "effect": {"path": parameters["path"], "size": len(data),
                                        "sha256": hashlib.sha256(data).hexdigest()}}


__all__ = ["LocalDirectoryChannel"]
