"""Verify and materialize exact-byte repository candidates in isolated Git state."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from distribution.candidate_manifest import (
    CandidateManifestError,
    manifest_references,
    parse_manifest,
    validate_git_overlay,
    validate_repo_path,
)

ReferenceAuthenticator = Callable[[Mapping[str, str]], bool]


class CandidateSnapshotError(ValueError):
    """Candidate bytes, repository state, or materialization failed validation."""



def _posix_os_flag(name: str, default: int | None = None) -> int:
    value = getattr(os, name, None)
    if isinstance(value, int):
        return value
    if default is not None:
        return default
    raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: required POSIX open flag is unavailable")


def _posix_effective_uid() -> int:
    get_effective_uid = getattr(os, "geteuid", None)
    if not callable(get_effective_uid):
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: effective UID lookup is unavailable")
    return int(get_effective_uid())


def _posix_fchmod(file_descriptor: int, mode: int) -> None:
    chmod_descriptor = getattr(os, "fchmod", None)
    if not callable(chmod_descriptor):
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: descriptor mode setting is unavailable")
    chmod_descriptor(file_descriptor, mode)


def _load_windows_library(name: str, *, use_last_error: bool = False) -> Any:
    loader = getattr(ctypes, "WinDLL", None)
    if not callable(loader):
        raise AttributeError("ctypes.WinDLL is unavailable")
    return loader(name, use_last_error=use_last_error)


def _windows_last_error() -> int:
    get_last_error = getattr(ctypes, "get_last_error", None)
    if not callable(get_last_error):
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Win32 last-error lookup is unavailable")
    return int(get_last_error())

class _WindowsPublicationIndeterminate(CandidateSnapshotError):
    """A native rename may still be pending; retained references keep its buffers and handles alive."""

    def __init__(self, message: str, resources: tuple[Any, ...]) -> None:
        super().__init__(message)
        self.resources = resources


_WINDOWS_INDETERMINATE_PUBLICATIONS: list[_WindowsPublicationIndeterminate] = []


@dataclass(frozen=True)
class CandidateSnapshot:
    manifest: Mapping[str, Any]
    manifest_sha256: str
    source_tree: str
    materialized_root: Path

    @property
    def base_commit(self) -> str:
        return str(self.manifest["base"]["head"])

    @property
    def tree(self) -> str:
        return self.source_tree


def _git(root: Path, args: list[str], *, env: Mapping[str, str] | None = None,
         input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(["git", *args], cwd=root, env=None if env is None else dict(env),
                                input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, stdin=None if input_bytes is not None else subprocess.DEVNULL)
    except OSError as exc:
        raise CandidateSnapshotError(f"cannot execute Git: {args[0]}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace")[-1200:]
        raise CandidateSnapshotError(f"Git command failed ({args[0]}): {detail}")
    return result.stdout


def _git_environment(root: Path, temporary: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    object_dir = _git(root, ["rev-parse", "--git-path", "objects"]).decode().strip()
    objects = Path(object_dir)
    if not objects.is_absolute():
        objects = root / objects
    if not objects.is_dir():
        raise CandidateSnapshotError("repository object database is unavailable")
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_INDEX_FILE": str(temporary / "index"),
                "GIT_OBJECT_DIRECTORY": str(temporary / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects.resolve())})
    return env


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_file(root: Path, relative: str) -> tuple[Path, os.stat_result]:
    path = root
    for component in relative.split("/"):
        path = path / component
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise CandidateSnapshotError(f"symlink or reparse point is not admitted: {relative}")
    if not stat.S_ISREG(info.st_mode):
        raise CandidateSnapshotError(f"candidate path is not a regular file: {relative}")
    return path, info


def _is_operational(child: os.DirEntry[str], relative: str, roots: set[str]) -> bool:
    if relative not in roots:
        return False
    info = child.stat(follow_symlinks=False)
    if not child.is_dir(follow_symlinks=False) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise CandidateSnapshotError(f"operational root must be a real directory: {relative}")
    return True


def _record_workspace_entry(
    child: os.DirEntry[str], relative: str, protected: set[str], operational: set[str], found: set[str],
    exclude_git: bool,
) -> None:
    if _is_operational(child, relative, operational):
        return
    try:
        validate_repo_path(relative)
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(f"workspace contains an unsafe path: {relative}") from exc
    info = child.stat(follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise CandidateSnapshotError(f"workspace contains an unclassified symlink or reparse point: {relative}")
    if child.is_dir(follow_symlinks=False):
        _walk_workspace(Path(child.path), relative, protected, operational, found, exclude_git)
    elif child.is_file(follow_symlinks=False):
        if relative not in protected:
            found.add(relative)
    else:
        raise CandidateSnapshotError(f"workspace contains an unsupported filesystem object: {relative}")


def _walk_workspace(
    directory: Path, prefix: str, protected: set[str], operational: set[str], found: set[str],
    exclude_git: bool,
) -> None:
    try:
        children = sorted(os.scandir(directory), key=lambda item: item.name)
    except OSError as exc:
        raise CandidateSnapshotError(f"cannot enumerate workspace path: {prefix or '.'}") from exc
    for child in children:
        if exclude_git and not prefix and child.name == ".git":
            continue
        relative = f"{prefix}/{child.name}" if prefix else child.name
        _record_workspace_entry(child, relative, protected, operational, found, exclude_git)


def _scan_workspace(root: Path, manifest: Mapping[str, Any], *, workspace: bool) -> set[str]:
    protected = {row["path"] for row in manifest["workspace_context"]["protected_files"]} if workspace else set()
    operational = set(manifest["workspace_context"]["operational_roots"]) if workspace else set()
    found: set[str] = set()
    _walk_workspace(root, "", protected, operational, found, workspace)
    return found


def _authenticate(manifest: Mapping[str, Any], root: Path,
                  callback: ReferenceAuthenticator | None, *, workspace: bool) -> None:
    for reference in manifest_references(manifest, workspace=workspace):
        if callback is not None:
            valid = callback(reference)
        else:
            try:
                path, _ = _safe_file(root, reference["path"])
                valid = hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
            except OSError:
                valid = False
        if not valid:
            raise CandidateSnapshotError(f"evidence reference failed authentication: {reference['path']}")


def _verify_files(root: Path, manifest: Mapping[str, Any]) -> None:
    files = {item["path"]: item for item in manifest["files"]}
    for relative, expected in files.items():
        path, info = _safe_file(root, relative)
        content = path.read_bytes()
        if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise CandidateSnapshotError(f"candidate bytes differ from manifest: {relative}")
        mode = expected["mode"] if os.name == "nt" else "100755" if info.st_mode & stat.S_IXUSR else "100644"
        if mode != expected["mode"]:
            raise CandidateSnapshotError(f"candidate mode differs from manifest: {relative}")


def _construct_tree(
    git_root: Path, source_root: Path, manifest: Mapping[str, Any], env: Mapping[str, str],
    destination: Path | None = None,
) -> str:
    _git(git_root, ["read-tree", "--empty"], env=env)
    blobs: list[tuple[Mapping[str, Any], str]] = []
    entries: list[bytes] = []
    for row in manifest["files"]:
        source, _ = _safe_file(source_root, row["path"])
        content = source.read_bytes()
        if len(content) != row["bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise CandidateSnapshotError(f"candidate bytes changed during tree construction: {row['path']}")
        blob = _git(git_root, ["hash-object", "-w", "--stdin", "--no-filters"], env=env,
                    input_bytes=content).decode("ascii").strip()
        entries.append(f"{row['mode']} {blob}\t{row['path']}\0".encode("utf-8"))
        blobs.append((row, blob))
    if entries:
        _git(git_root, ["update-index", "-z", "--index-info"], env=env, input_bytes=b"".join(entries))
    tree = _git(git_root, ["write-tree"], env=env).decode("ascii").strip()
    if destination is not None:
        for row, blob in blobs:
            target = destination.joinpath(*row["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(git_root, ["cat-file", "blob", blob], env=env))
            if os.name != "nt":
                target.chmod(0o755 if row["mode"] == "100755" else 0o644)
    return tree


@dataclass
class _PinnedDestinationParent:
    path: Path
    name: str
    identity: tuple[int, ...]
    fd: int | None = None
    handle: int | None = None
    components: list[tuple[Path, int, tuple[int, ...]]] = field(default_factory=list)


def _absolute_destination(destination: Path) -> Path:
    try:
        result = Path(os.path.abspath(os.fspath(destination)))
    except (OSError, TypeError, ValueError) as exc:
        raise CandidateSnapshotError("materialization destination is invalid") from exc
    if not result.name or result.name in {".", ".."}:
        raise CandidateSnapshotError("materialization destination must name a new directory")
    return result


def _path_is_at_or_below(parent: Path, child: Path) -> bool:
    parent_text = os.path.normcase(os.path.abspath(os.fspath(parent)))
    child_text = os.path.normcase(os.path.abspath(os.fspath(child)))
    try:
        common = os.path.normcase(os.path.commonpath((parent_text, child_text)))
    except ValueError:
        return False
    return common == parent_text


def _windows_api() -> tuple[Any, Any]:
    if os.name != "nt":
        raise CandidateSnapshotError("Windows filesystem operation requested on another platform")
    try:
        from ctypes import wintypes

        kernel = _load_windows_library("kernel32", use_last_error=True)
        advapi = _load_windows_library("advapi32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Windows APIs are unavailable") from exc

    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi.GetLengthSid.restype = wintypes.DWORD
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    return kernel, advapi


def _windows_close_handle(kernel: Any, handle: int | ctypes.c_void_p) -> None:
    from ctypes import wintypes

    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    value = handle.value if isinstance(handle, ctypes.c_void_p) else handle
    if value is None or not kernel.CloseHandle(wintypes.HANDLE(value)):
        raise CandidateSnapshotError("cannot close a Windows filesystem handle")


def _windows_close_handles(kernel: Any, handles: list[int]) -> None:
    first_error: Exception | None = None
    for handle in handles:
        try:
            _windows_close_handle(kernel, handle)
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise CandidateSnapshotError("one or more Windows filesystem handles could not be closed") from first_error


def _windows_local_free(kernel: Any, pointer: ctypes.c_void_p) -> None:
    if pointer.value and kernel.LocalFree(pointer):
        raise CandidateSnapshotError("cannot release a Windows security descriptor")


def _windows_token_identity_sid(advapi: Any, kernel: Any, information_class: int) -> bytes:
    from ctypes import wintypes

    if information_class not in (1, 4):
        raise CandidateSnapshotError("unsupported Windows token identity class")

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    minimum = ctypes.sizeof(SidAndAttributes) if information_class == 1 else ctypes.sizeof(ctypes.c_void_p)
    identity = "user" if information_class == 1 else "owner"
    error = f"cannot inspect the Windows process {identity}"
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise CandidateSnapshotError(error)
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, information_class, None, 0, ctypes.byref(needed))
        if needed.value < minimum:
            raise CandidateSnapshotError(error)
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(
            token, information_class, ctypes.cast(buffer, ctypes.c_void_p), needed.value, ctypes.byref(needed),
        ):
            raise CandidateSnapshotError(error)
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not sid:
            raise CandidateSnapshotError(error)
        length = advapi.GetLengthSid(ctypes.c_void_p(sid))
        if not length:
            raise CandidateSnapshotError(error)
        return ctypes.string_at(sid, length)
    finally:
        _windows_close_handle(kernel, token)


def _windows_current_owner_sid(advapi: Any, kernel: Any) -> bytes:
    return _windows_token_identity_sid(advapi, kernel, 4)  # TokenOwner: default object owner.


def _windows_current_user_sid(advapi: Any, kernel: Any) -> bytes:
    return _windows_token_identity_sid(advapi, kernel, 1)  # TokenUser: exact process principal.


def _windows_file_identity_attributes(
    handle: int, kernel: Any,
) -> tuple[tuple[int, ...], int]:
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", FileTime),
            ("ftLastAccessTime", FileTime),
            ("ftLastWriteTime", FileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    info = ByHandleFileInformation()
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    if not kernel.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info)):
        raise CandidateSnapshotError("cannot verify Windows filesystem identity")
    file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    identity = int(info.dwVolumeSerialNumber), file_index
    return identity, int(info.dwFileAttributes)


def _windows_identity(handle: int, kernel: Any) -> tuple[int, ...]:
    identity, attributes = _windows_file_identity_attributes(handle, kernel)
    if not attributes & 0x10 or attributes & 0x400:
        raise CandidateSnapshotError("destination parent or staging entry is not a real directory")
    return identity

def _windows_trusted_sids(advapi: Any, kernel: Any) -> set[bytes]:
    # TokenOwner may be an owner-eligible group, distinct from the process user.
    # Trust that exact principal, never arbitrary enabled or owner-eligible groups.
    trusted_sids = {_windows_current_owner_sid(advapi, kernel), _windows_current_user_sid(advapi, kernel)}
    for text_sid in (
        "S-1-5-18", "S-1-5-32-544", "S-1-3-4",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
    ):
        sid = ctypes.c_void_p()
        if not advapi.ConvertStringSidToSidW(text_sid, ctypes.byref(sid)):
            raise CandidateSnapshotError("cannot verify Windows directory ACL")
        try:
            length = advapi.GetLengthSid(sid)
            if not length:
                raise CandidateSnapshotError("cannot verify Windows directory ACL")
            trusted_sids.add(ctypes.string_at(sid, length))
        finally:
            _windows_local_free(kernel, sid)
    return trusted_sids


def _windows_directory_security_info(
    handle: int, kernel: Any, advapi: Any,
) -> tuple[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]:
    from ctypes import wintypes

    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    security_descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    status = advapi.GetSecurityInfo(
        wintypes.HANDLE(handle), 1, 0x00000001 | 0x00000004, ctypes.byref(owner), None,
        ctypes.byref(dacl), None, ctypes.byref(security_descriptor),
    )
    if status:
        _windows_local_free(kernel, security_descriptor)
        raise CandidateSnapshotError("cannot inspect Windows directory ACL")
    return security_descriptor, owner, dacl


def _windows_validate_directory_owner(
    owner: ctypes.c_void_p, dacl: ctypes.c_void_p, trusted_sids: set[bytes], advapi: Any,
) -> None:
    if not owner.value or not dacl.value:
        raise CandidateSnapshotError("Windows directory has no trusted owner or DACL")
    length = advapi.GetLengthSid(owner)
    if not length or ctypes.string_at(owner, length) not in trusted_sids:
        raise CandidateSnapshotError("Windows directory owner is not trusted for isolated materialization")


def _windows_directory_acl_entries(
    security_descriptor: ctypes.c_void_p, require_protected: bool, advapi: Any,
) -> tuple[ctypes.c_void_p, int]:
    from ctypes import wintypes

    class AclHeader(ctypes.Structure):
        _fields_ = [
            ("AclRevision", wintypes.BYTE), ("Sbz1", wintypes.BYTE), ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD), ("Sbz2", wintypes.WORD),
        ]

    advapi.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    if not advapi.GetSecurityDescriptorDacl(
        security_descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted),
    ) or not present.value or not dacl.value:
        raise CandidateSnapshotError("Windows directory has no explicit DACL")

    control = wintypes.WORD()
    revision = wintypes.DWORD()
    advapi.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    if not advapi.GetSecurityDescriptorControl(security_descriptor, ctypes.byref(control), ctypes.byref(revision)):
        raise CandidateSnapshotError("cannot inspect Windows directory ACL controls")
    if require_protected and not control.value & 0x1000:
        raise CandidateSnapshotError("private staging ACL is not protected from inheritance")
    return dacl, int(ctypes.cast(dacl, ctypes.POINTER(AclHeader)).contents.AceCount)


def _windows_acl_mask_is_unsafe(mask: int, *, allow_child_creation: bool = False) -> bool:
    protected = 0x00000040 | 0x00010000 | 0x00040000 | 0x00080000 | 0x10000000 | 0x40000000
    if not allow_child_creation:
        protected |= 0x00000002 | 0x00000004
    return bool(mask & protected)

def _windows_validate_acl_ace(
    ace_pointer: ctypes.c_void_p, trusted_sids: set[bytes], advapi: Any, *,
    allow_child_creation: bool = False,
) -> None:
    from ctypes import wintypes

    class AceHeader(ctypes.Structure):
        _fields_ = [("AceType", wintypes.BYTE), ("AceFlags", wintypes.BYTE), ("AceSize", wintypes.WORD)]

    ace = ctypes.cast(ace_pointer, ctypes.POINTER(AceHeader)).contents
    if ace.AceType == 1 or ace.AceFlags & 0x08:
        return
    if ace.AceType != 0 or ace.AceSize < 16:
        raise CandidateSnapshotError("Windows directory ACL uses an unsupported ACE")
    ace_address = ace_pointer.value
    if ace_address is None:
        raise CandidateSnapshotError("Windows directory ACL contains a null ACE pointer")
    mask = ctypes.c_uint32.from_address(ace_address + 4).value
    sid_pointer = ace_address + 8
    sid_length = advapi.GetLengthSid(ctypes.c_void_p(sid_pointer))
    if not sid_length or sid_length > ace.AceSize - 8:
        raise CandidateSnapshotError("Windows directory ACL contains an invalid SID")
    sid_bytes = ctypes.string_at(sid_pointer, sid_length)
    if _windows_acl_mask_is_unsafe(mask, allow_child_creation=allow_child_creation) and sid_bytes not in trusted_sids:
        raise CandidateSnapshotError("Windows directory ACL permits untrusted namespace changes")


def _windows_validate_acl_entries(
    dacl: ctypes.c_void_p, count: int, trusted_sids: set[bytes], advapi: Any, *,
    allow_child_creation: bool = False,
) -> None:
    from ctypes import wintypes

    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.restype = wintypes.BOOL
    for index in range(count):
        ace_pointer = ctypes.c_void_p()
        if not advapi.GetAce(dacl, index, ctypes.byref(ace_pointer)):
            raise CandidateSnapshotError("cannot inspect Windows directory ACL entries")
        _windows_validate_acl_ace(
            ace_pointer, trusted_sids, advapi, allow_child_creation=allow_child_creation,
        )


def _windows_check_directory_acl(
    handle: int, *, require_protected: bool = False, allow_child_creation: bool = False,
) -> None:
    kernel, advapi = _windows_api()
    trusted_sids = _windows_trusted_sids(advapi, kernel)
    security_descriptor, owner, dacl = _windows_directory_security_info(handle, kernel, advapi)
    try:
        _windows_validate_directory_owner(owner, dacl, trusted_sids, advapi)
        acl, count = _windows_directory_acl_entries(security_descriptor, require_protected, advapi)
        _windows_validate_acl_entries(
            acl, count, trusted_sids, advapi, allow_child_creation=allow_child_creation,
        )
    finally:
        _windows_local_free(kernel, security_descriptor)

def _windows_open_path(
    path: Path, access: int, share_mode: int, *, kernel: Any | None = None,
) -> int:
    from ctypes import wintypes

    if kernel is None:
        kernel, _ = _windows_api()
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle_value = kernel.CreateFileW(
        str(path), access, share_mode, None, 3, 0x02000000 | 0x00200000, None,
    )
    handle = ctypes.cast(handle_value, ctypes.c_void_p).value
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise CandidateSnapshotError("cannot pin a Windows filesystem entry")
    return handle


def _windows_open_directory(
    path: Path, access: int, *, share_mode: int = 0x00000001 | 0x00000002 | 0x00000004,
    allow_child_creation: bool = False,
) -> tuple[int, tuple[int, ...]]:
    kernel, _ = _windows_api()
    handle = _windows_open_path(path, access, share_mode, kernel=kernel)
    try:
        identity = _windows_identity(handle, kernel)
        _windows_check_directory_acl(handle, allow_child_creation=allow_child_creation)
        return handle, identity
    except Exception:
        _windows_close_handle(kernel, handle)
        raise

def _windows_destination_paths(parent: Path, kernel: Any) -> list[Path]:
    from ctypes import wintypes

    if not parent.is_absolute() or parent.anchor.startswith("\\\\"):
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Windows destination must be a local drive path")
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if kernel.GetDriveTypeW(parent.anchor) != 3:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Windows destination must use a fixed local volume")
    paths = [Path(parent.anchor)]
    current = paths[0]
    for part in parent.parts[1:]:
        if part not in {"", "\\\\", "/"}:
            current = current / part
            paths.append(current)
    return paths


def _open_windows_destination_parent(destination: Path) -> _PinnedDestinationParent:
    kernel, _ = _windows_api()
    paths = _windows_destination_paths(destination.parent, kernel)
    components: list[tuple[Path, int, tuple[int, ...]]] = []
    try:
        for path in paths:
            access = 0x00000080 | 0x00020000
            if path == destination.parent:
                access |= 0x00000002 | 0x00000004 | 0x00000020 | 0x00000040
            handle, identity = _windows_open_directory(path, access, allow_child_creation=True)
            components.append((path, handle, identity))
        path, handle, identity = components[-1]
        return _PinnedDestinationParent(path, destination.name, identity, handle=handle, components=components)
    except Exception:
        _windows_close_handles(kernel, [handle for _, handle, _ in components])
        raise


def _posix_parent_open_flags(parent: Path) -> int:
    required = {os.open, os.mkdir, os.stat, os.unlink, os.rmdir}
    if not required.issubset(os.supports_dir_fd) or os.scandir not in os.supports_fd:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: safe directory-handle operations are unavailable")
    if not parent.is_absolute() or parent.anchor != "/":
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: destination parent must be an absolute POSIX path")
    return (
        _posix_os_flag("O_RDONLY") | _posix_os_flag("O_DIRECTORY")
        | _posix_os_flag("O_NOFOLLOW") | _posix_os_flag("O_CLOEXEC", 0)
    )


def _posix_open_child_directory(parent_fd: int, component: str, flags: int) -> int:
    child_fd = os.open(component, flags, dir_fd=parent_fd)
    try:
        info = os.fstat(child_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, _posix_effective_uid()}:
            raise CandidateSnapshotError("destination parent ownership is not trusted")
        writable = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if writable and not (info.st_mode & stat.S_ISVTX and info.st_uid in {0, _posix_effective_uid()}):
            raise CandidateSnapshotError("destination parent permits untrusted namespace changes")
        return child_fd
    except Exception:
        os.close(child_fd)
        raise


def _open_posix_destination_parent(destination: Path) -> _PinnedDestinationParent:
    parent = destination.parent
    flags = _posix_parent_open_flags(parent)
    fd = os.open("/", flags)
    try:
        for component in parent.parts[1:]:
            if not component:
                continue
            child_fd = _posix_open_child_directory(fd, component, flags)
            os.close(fd)
            fd = child_fd
        info = os.fstat(fd)
        identity = (int(info.st_dev), int(info.st_ino))
        return _PinnedDestinationParent(parent, destination.name, identity, fd=fd)
    except Exception:
        os.close(fd)
        raise

def _open_destination_parent(destination: Path) -> _PinnedDestinationParent:
    if os.name == "nt":
        return _open_windows_destination_parent(destination)
    return _open_posix_destination_parent(destination)

def _close_destination_parent(parent: _PinnedDestinationParent) -> None:
    if parent.fd is not None:
        os.close(parent.fd)
        parent.fd = None
    if parent.components:
        kernel, _ = _windows_api()
        handles = [handle for _, handle, _ in parent.components]
        parent.components.clear()
        _windows_close_handles(kernel, handles)


def _ensure_destination_absent(parent: _PinnedDestinationParent, destination: Path) -> None:
    try:
        if parent.fd is not None:
            os.stat(parent.name, dir_fd=parent.fd, follow_symlinks=False)
        else:
            destination.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CandidateSnapshotError("cannot safely inspect materialization destination") from exc
    raise CandidateSnapshotError("materialization destination must not already exist")


def _ensure_parent_still_pinned(parent: _PinnedDestinationParent) -> None:
    if parent.fd is not None:
        current = _open_destination_parent(parent.path / parent.name)
        try:
            if current.identity != parent.identity:
                raise CandidateSnapshotError("destination parent identity changed during materialization")
        finally:
            _close_destination_parent(current)
        return
    kernel, _ = _windows_api()
    for path, handle, expected in parent.components:
        current_handle, current_identity = _windows_open_directory(
            path, 0x00000080 | 0x00020000, allow_child_creation=True,
        )
        try:
            if current_identity != expected or current_identity != _windows_identity(handle, kernel):
                raise CandidateSnapshotError("destination parent identity changed during materialization")
        finally:
            _windows_close_handle(kernel, current_handle)


def _windows_create_stage_path(parent: _PinnedDestinationParent, attributes: Any, kernel: Any) -> tuple[str, Path]:
    from ctypes import wintypes

    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(type(attributes))]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    for _ in range(32):
        name = f".cand03-{secrets.token_hex(16)}"
        stage_path = parent.path / name
        if kernel.CreateDirectoryW(str(stage_path), ctypes.byref(attributes)):
            return name, stage_path
        error = _windows_last_error()
        if error not in {80, 183}:
            raise CandidateSnapshotError("cannot create private Windows staging directory")
    raise CandidateSnapshotError("cannot allocate a unique private staging directory")


def _remove_windows_unpublished_stage(kernel: Any, stage_path: Path, handle: int | None) -> None:
    if handle is None:
        raise CandidateSnapshotError(
            f"CLEANUP_UNCONFIRMED for staging entry {stage_path.name}: no pinned handle is available"
        )
    cleanup_error: Exception | None = None
    try:
        _windows_mark_directory_for_deletion(kernel, handle)
    except Exception as exc:
        cleanup_error = exc
    try:
        _windows_close_handle(kernel, handle)
    except Exception as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if cleanup_error is not None:
        raise CandidateSnapshotError(
            f"CLEANUP_UNCONFIRMED for staging entry {stage_path.name}: {cleanup_error}"
        ) from cleanup_error


def _create_windows_private_stage(parent: _PinnedDestinationParent) -> tuple[str, int, tuple[int, ...], Path]:
    from ctypes import wintypes

    kernel, advapi = _windows_api()

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    descriptor = ctypes.c_void_p()
    sddl = "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None,
    ):
        raise CandidateSnapshotError("cannot create a restricted Windows staging ACL")
    stage_name, stage_path = "", parent.path
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        stage_name, stage_path = _windows_create_stage_path(parent, attributes, kernel)
    finally:
        try:
            _windows_local_free(kernel, descriptor)
        except Exception as free_exc:
            if stage_name:
                try:
                    _remove_windows_unpublished_stage(kernel, stage_path, None)
                except Exception as cleanup_exc:
                    raise CandidateSnapshotError(
                        f"CLEANUP_UNCONFIRMED for staging entry {stage_path.name}: {cleanup_exc}"
                    ) from free_exc
            raise

    handle: int | None = None
    try:
        # CreateFileW omits FILE_FLAG_OVERLAPPED, so this is a synchronous file handle.
        # SYNCHRONIZE permits waiting on it if NtSetInformationFile reports STATUS_PENDING.
        # Omitting FILE_SHARE_DELETE prevents a competing rename or deletion of this stage.
        handle, identity = _windows_open_directory(
            stage_path, 0x00100000 | 0x00010000 | 0x00000080 | 0x00020000,
            share_mode=0x00000001 | 0x00000002,
        )
        _windows_check_directory_acl(handle, require_protected=True)
        return stage_name, handle, identity, stage_path
    except Exception as exc:
        if handle is not None:
            try:
                _windows_close_handle(kernel, handle)
            except Exception as close_exc:
                raise CandidateSnapshotError(
                    f"CLEANUP_UNCONFIRMED for staging entry {stage_name}: "
                    "the newly created directory identity could not be proven"
                ) from close_exc
        raise CandidateSnapshotError(
            f"CLEANUP_UNCONFIRMED for staging entry {stage_name}: "
            "the newly created directory identity could not be proven; left untouched"
        ) from exc


def _linux_remove_unpublished_stage(
    parent: _PinnedDestinationParent, name: str, stage_fd: int | None, cause: Exception,
) -> None:
    assert parent.fd is not None
    cleanup_error: Exception | None = None
    if stage_fd is not None:
        try:
            os.close(stage_fd)
        except Exception as exc:
            cleanup_error = exc
    try:
        os.rmdir(name, dir_fd=parent.fd)
    except Exception as exc:
        if cleanup_error is None:
            cleanup_error = exc
    if cleanup_error is not None:
        raise CandidateSnapshotError(
            f"CLEANUP_UNCONFIRMED for staging entry {name}: {cleanup_error}"
        ) from cause


def _linux_create_stage_attempt(
    parent: _PinnedDestinationParent, name: str, flags: int,
) -> tuple[int, tuple[int, ...], Path] | None:
    assert parent.fd is not None
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent.fd)
    except FileExistsError:
        return None
    stage_fd: int | None = None
    try:
        stage_fd = os.open(name, flags, dir_fd=parent.fd)
        info = os.fstat(stage_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != _posix_effective_uid():
            raise CandidateSnapshotError("staging directory ownership could not be proven")
        _posix_fchmod(stage_fd, 0o700)
        info = os.fstat(stage_fd)
        if info.st_mode & 0o077:
            raise CandidateSnapshotError("staging directory permissions are not private")
        identity = (int(info.st_dev), int(info.st_ino))
        return stage_fd, identity, Path("/proc/self/fd") / str(stage_fd)
    except Exception as exc:
        _linux_remove_unpublished_stage(parent, name, stage_fd, exc)
        raise

def _create_linux_private_stage(parent: _PinnedDestinationParent) -> tuple[str, int, tuple[int, ...], Path]:
    if not os.path.isdir("/proc/self/fd"):
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Linux descriptor paths are unavailable")
    assert parent.fd is not None
    flags = _posix_os_flag("O_RDONLY") | _posix_os_flag("O_DIRECTORY") | _posix_os_flag("O_NOFOLLOW") | _posix_os_flag("O_CLOEXEC", 0)
    for _ in range(32):
        name = f".cand03-{secrets.token_hex(16)}"
        created = _linux_create_stage_attempt(parent, name, flags)
        if created is not None:
            stage_fd, identity, stage_root = created
            return name, stage_fd, identity, stage_root
    raise CandidateSnapshotError("cannot allocate a unique private staging directory")

def _create_private_stage(parent: _PinnedDestinationParent) -> tuple[str, int, tuple[int, ...], Path]:
    if os.name == "nt":
        return _create_windows_private_stage(parent)
    return _create_linux_private_stage(parent)

def _ensure_stage_still_owned(
    parent: _PinnedDestinationParent, stage_name: str, stage_handle: int, expected: tuple[int, ...],
) -> None:
    if os.name == "nt":
        kernel, _ = _windows_api()
        stage_path = parent.path / stage_name
        path_handle, identity = _windows_open_directory(stage_path, 0x00000080 | 0x00020000)
        try:
            if identity != expected or identity != _windows_identity(stage_handle, kernel):
                raise CandidateSnapshotError("private staging identity changed before publication")
            _windows_check_directory_acl(path_handle, require_protected=True)
        finally:
            _windows_close_handle(kernel, path_handle)
        return

    assert parent.fd is not None
    info = os.stat(stage_name, dir_fd=parent.fd, follow_symlinks=False)
    opened = os.fstat(stage_handle)
    identity = (int(info.st_dev), int(info.st_ino))
    if (identity != expected or identity != (int(opened.st_dev), int(opened.st_ino))
            or opened.st_uid != _posix_effective_uid() or opened.st_mode & 0o077):
        raise CandidateSnapshotError("private staging identity or permissions changed before publication")

def _verify_private_tree(root: Path, manifest: Mapping[str, Any]) -> None:
    actual_paths = _scan_workspace(root, manifest, workspace=False)
    expected_paths = {item["path"] for item in manifest["files"]}
    if actual_paths != expected_paths:
        raise CandidateSnapshotError("private staging path set differs from complete manifest")
    _verify_files(root, manifest)


def _windows_file_rename_information(root_handle: int, destination_name: str) -> tuple[Any, int]:
    from ctypes import wintypes

    if os.name != "nt":
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Windows rename requested on another platform")
    if (not destination_name or destination_name in {".", ".."}
            or "/" in destination_name or "\\" in destination_name or "\0" in destination_name):
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: destination name is not a single component")

    class FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", ctypes.c_ubyte), ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1),
        ]

    encoded_name = destination_name.encode("utf-16-le")
    header_size = FileRenameInformation.FileName.offset
    info = FileRenameInformation()
    info.ReplaceIfExists = 0
    info.RootDirectory = wintypes.HANDLE(root_handle)
    info.FileNameLength = len(encoded_name)
    buffer_size = max(header_size + len(encoded_name), ctypes.sizeof(FileRenameInformation) + len(encoded_name))
    buffer = ctypes.create_string_buffer(buffer_size)
    ctypes.memmove(buffer, ctypes.byref(info), header_size)
    ctypes.memmove(ctypes.addressof(buffer) + header_size, encoded_name, len(encoded_name))
    return buffer, buffer_size


def _windows_final_io_status(
    initial_status: int, io_status: Any, wait_for_completion: Callable[[], int],
    resources: tuple[Any, ...] = (), *, synchronous: bool = False,
) -> int:
    initial_code = initial_status & 0xFFFFFFFF
    wait_status: int | None = None
    if initial_code == 0x00000103:  # STATUS_PENDING
        try:
            wait_status = wait_for_completion()
        except BaseException as exc:
            raise _WindowsPublicationIndeterminate(
                "Windows publication wait was interrupted; native operation outcome is indeterminate",
                resources,
            ) from exc
        if wait_status != 0:
            raise _WindowsPublicationIndeterminate(
                f"Windows publication wait returned 0x{wait_status & 0xFFFFFFFF:08X}; "
                "native operation outcome is indeterminate",
                resources,
            )
    final_status = int(io_status.StatusUnion.Status)
    final_code = final_status & 0xFFFFFFFF
    if final_code == 0x00000103:
        if initial_code != 0x00000103 and synchronous:
            # A synchronous CreateFileW handle completes inline; the return status is authoritative.
            return initial_status
        wait_text = "not-run" if wait_status is None else f"0x{wait_status & 0xFFFFFFFF:08X}"
        raise _WindowsPublicationIndeterminate(
            "Windows publication completion did not replace STATUS_PENDING in IO_STATUS_BLOCK "
            f"(return=0x{initial_code:08X}, wait={wait_text}, io=0x{final_code:08X})",
            resources,
        )
    if initial_code != 0x00000103 and final_code != initial_code:
        raise _WindowsPublicationIndeterminate(
            "Windows publication return status disagrees with IO_STATUS_BLOCK",
            resources,
        )
    return final_status


def _windows_ntdll_wait_for_single_object(ntdll: Any, handle: int) -> int:
    from ctypes import wintypes

    wait = ntdll.NtWaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.BOOLEAN, ctypes.POINTER(ctypes.c_longlong)]
    wait.restype = ctypes.c_long
    timeout = ctypes.c_longlong(-30 * 10_000_000)  # 30 seconds in relative 100-ns units
    return int(wait(wintypes.HANDLE(handle), False, ctypes.byref(timeout)))


def _windows_ntdll_set_information_file(stage_handle: int, buffer: Any, buffer_size: int) -> int:
    from ctypes import wintypes

    if os.name != "nt":
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Windows rename requested on another platform")

    class IoStatusBlockUnion(ctypes.Union):
        _fields_ = [("Status", ctypes.c_long), ("Pointer", ctypes.c_void_p)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusUnion", IoStatusBlockUnion), ("Information", ctypes.c_size_t)]

    try:
        ntdll = _load_windows_library("ntdll")
        set_information = ntdll.NtSetInformationFile
    except (AttributeError, OSError) as exc:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: NtSetInformationFile is unavailable") from exc

    set_information.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
    ]
    set_information.restype = ctypes.c_long
    io_status = IoStatusBlock()
    # NTSTATUS 0x103 marks incomplete I/O; sync stage handles normally complete before return.
    io_status.StatusUnion.Status = ctypes.c_long(0x00000103).value
    status = int(set_information(
        wintypes.HANDLE(stage_handle), ctypes.byref(io_status), ctypes.cast(buffer, ctypes.c_void_p),
        buffer_size, 10,  # FileRenameInformation
    ))
    resources = (buffer, io_status, ntdll, stage_handle)
    return _windows_final_io_status(
        status, io_status, lambda: _windows_ntdll_wait_for_single_object(ntdll, stage_handle),
        resources, synchronous=True,
    )


def _publish_windows_directory_noreplace(
    parent: _PinnedDestinationParent, stage_handle: int, destination_name: str,
) -> None:
    if parent.handle is None:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: destination handle is unavailable")
    # The pinned RootDirectory and no-replace flag avoid the path race in absolute-path fallbacks.
    buffer, buffer_size = _windows_file_rename_information(parent.handle, destination_name)
    try:
        status = _windows_ntdll_set_information_file(stage_handle, buffer, buffer_size)
    except _WindowsPublicationIndeterminate as exc:
        exc.resources = (*exc.resources, parent, stage_handle)
        raise
    status_code = status & 0xFFFFFFFF
    if status >= 0:
        return
    if status_code == 0xC0000035:  # STATUS_OBJECT_NAME_COLLISION
        raise CandidateSnapshotError("destination appeared before no-replace publication")
    if status_code in {0xC0000002, 0xC0000003, 0xC000000D, 0xC00000BB}:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: native Windows relative rename is unavailable")
    raise CandidateSnapshotError(f"Windows no-replace publication failed (NTSTATUS 0x{status_code:08X})")


def _windows_mark_directory_for_deletion(kernel: Any, handle: int) -> None:
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_ubyte)]

    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    disposition = FileDispositionInfo(1)
    if not kernel.SetFileInformationByHandle(
        wintypes.HANDLE(handle), 4, ctypes.byref(disposition), ctypes.sizeof(disposition),
    ):
        raise CandidateSnapshotError("cannot mark pinned Windows staging directory for deletion")

def _publish_linux_directory_noreplace(
    parent: _PinnedDestinationParent, stage_name: str, destination_name: str,
) -> None:
    assert parent.fd is not None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: Linux renameat2 is unavailable") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(parent.fd, os.fsencode(stage_name), parent.fd, os.fsencode(destination_name), 1)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise CandidateSnapshotError("destination appeared before no-replace publication")
    unsupported = {
        errno.ENOSYS, errno.EINVAL, getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        getattr(errno, "ENOTSUP", errno.ENOSYS),
    }
    if error in unsupported:
        raise CandidateSnapshotError("UNSUPPORTED_ATOMIC_PUBLISH: filesystem lacks renameat2 no-replace support")
    raise CandidateSnapshotError(f"Linux no-replace publication failed (errno {error})")


def _publish_private_directory_noreplace(
    parent: _PinnedDestinationParent, stage_name: str, stage_handle: int, destination_name: str,
) -> None:
    if os.name == "nt":
        _publish_windows_directory_noreplace(parent, stage_handle, destination_name)
        return
    _publish_linux_directory_noreplace(parent, stage_name, destination_name)

def _cleanup_linux_stage(parent: _PinnedDestinationParent, stage_name: str, expected: tuple[int, ...]) -> None:
    assert parent.fd is not None
    flags = _posix_os_flag("O_RDONLY") | _posix_os_flag("O_DIRECTORY") | _posix_os_flag("O_NOFOLLOW") | _posix_os_flag("O_CLOEXEC", 0)
    info = os.stat(stage_name, dir_fd=parent.fd, follow_symlinks=False)
    if (int(info.st_dev), int(info.st_ino)) != expected or not stat.S_ISDIR(info.st_mode):
        raise CandidateSnapshotError("owned staging identity changed before cleanup")
    stage_fd = os.open(stage_name, flags, dir_fd=parent.fd)
    try:
        opened = os.fstat(stage_fd)
        if (int(opened.st_dev), int(opened.st_ino)) != expected or opened.st_uid != _posix_effective_uid():
            raise CandidateSnapshotError("owned staging identity changed before cleanup")

        def remove_contents(directory_fd: int) -> None:
            for entry in list(os.scandir(directory_fd)):
                child_info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(child_info.st_mode) and not stat.S_ISLNK(child_info.st_mode):
                    child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
                    try:
                        opened_child = os.fstat(child_fd)
                        if (int(opened_child.st_dev), int(opened_child.st_ino)) != (
                            int(child_info.st_dev), int(child_info.st_ino),
                        ):
                            raise CandidateSnapshotError("staging entry identity changed during cleanup")
                        remove_contents(child_fd)
                    finally:
                        os.close(child_fd)
                    os.rmdir(entry.name, dir_fd=directory_fd)
                else:
                    os.unlink(entry.name, dir_fd=directory_fd)

        remove_contents(stage_fd)
    finally:
        os.close(stage_fd)
    os.rmdir(stage_name, dir_fd=parent.fd)


def _windows_cleanup_identity_is_consistent(
    identity: tuple[int, ...], device: int, file_id: int,
) -> bool:
    # Python may report zero st_dev/st_ino on Windows; compare each available component.
    return (not device or device == identity[0]) and (not file_id or file_id == identity[1])


def _windows_open_cleanup_entry(path: Path, info: os.stat_result) -> tuple[Any, int, bool]:
    if _is_reparse(info):
        raise CandidateSnapshotError("reparse point found in private staging during cleanup")
    is_directory = stat.S_ISDIR(info.st_mode)
    if not is_directory and not stat.S_ISREG(info.st_mode):
        raise CandidateSnapshotError("unsupported object found in private staging during cleanup")
    kernel, _ = _windows_api()
    handle = _windows_open_path(
        path, 0x00010000 | 0x00000080 | 0x00020000,
        share_mode=0x00000001 | 0x00000002, kernel=kernel,
    )
    try:
        identity, attributes = _windows_file_identity_attributes(handle, kernel)
        if (not _windows_cleanup_identity_is_consistent(
                identity, int(info.st_dev), int(info.st_ino),
        ) or bool(attributes & 0x10) != is_directory
                or attributes & 0x400):
            raise CandidateSnapshotError("staging entry identity or type changed during cleanup")
        return kernel, handle, is_directory
    except Exception:
        _windows_close_handle(kernel, handle)
        raise


def _windows_remove_directory_contents(directory: Path) -> None:
    with os.scandir(directory) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            child_path = Path(entry.path)
            kernel, child_handle, is_directory = _windows_open_cleanup_entry(child_path, info)
            try:
                if is_directory:
                    _windows_remove_directory_contents(child_path)
                _windows_mark_directory_for_deletion(kernel, child_handle)
            finally:
                _windows_close_handle(kernel, child_handle)


def _cleanup_windows_stage(
    parent: _PinnedDestinationParent, stage_name: str, expected: tuple[int, ...], stage_handle: int,
) -> None:
    _ensure_parent_still_pinned(parent)
    kernel, _ = _windows_api()
    stage_path = parent.path / stage_name
    if _windows_identity(stage_handle, kernel) != expected:
        raise CandidateSnapshotError("owned staging identity changed before cleanup")
    _ensure_stage_still_owned(parent, stage_name, stage_handle, expected)
    _windows_check_directory_acl(stage_handle, require_protected=True)
    _windows_remove_directory_contents(stage_path)
    _ensure_stage_still_owned(parent, stage_name, stage_handle, expected)
    _ensure_parent_still_pinned(parent)
    _windows_mark_directory_for_deletion(kernel, stage_handle)

def _close_stage_handle(stage_handle: int | None) -> None:
    if stage_handle is None:
        return
    if os.name == "nt":
        kernel, _ = _windows_api()
        _windows_close_handle(kernel, stage_handle)
    else:
        os.close(stage_handle)


def _verify_published_tree(
    parent: _PinnedDestinationParent, destination: Path, stage_handle: int,
    stage_identity: tuple[int, ...], manifest: Mapping[str, Any],
) -> None:
    _ensure_parent_still_pinned(parent)
    if os.name == "nt":
        kernel, _ = _windows_api()
        final_handle, identity = _windows_open_directory(destination, 0x00000080 | 0x00020000)
        try:
            if identity != stage_identity or identity != _windows_identity(stage_handle, kernel):
                raise CandidateSnapshotError("published directory identity differs from verified staging")
            _windows_check_directory_acl(final_handle, require_protected=True)
        finally:
            _windows_close_handle(kernel, final_handle)
    else:
        assert parent.fd is not None
        info = os.stat(parent.name, dir_fd=parent.fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (int(info.st_dev), int(info.st_ino)) != stage_identity:
            raise CandidateSnapshotError("published directory identity differs from verified staging")
    _verify_private_tree(destination, manifest)


def _build_private_stage_tree(root: Path, manifest: Mapping[str, Any], stage_root: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="candidate-exact-byte-git-") as raw_temp:
        temporary = Path(raw_temp)
        (temporary / "objects").mkdir()
        env = _git_environment(root, temporary)
        tree = _construct_tree(root, root, manifest, env, stage_root)
        if tree != manifest["source_tree"]:
            raise CandidateSnapshotError("reconstructed exact-byte tree differs from manifest source_tree")
        _verify_private_tree(stage_root, manifest)
    return tree


def _rename_verified_stage(
    parent: _PinnedDestinationParent, stage_name: str, stage_handle: int,
    stage_identity: tuple[int, ...], destination_name: str,
) -> None:
    _ensure_parent_still_pinned(parent)
    _ensure_stage_still_owned(parent, stage_name, stage_handle, stage_identity)
    _publish_private_directory_noreplace(parent, stage_name, stage_handle, destination_name)


def _verify_publication_postcondition(
    parent: _PinnedDestinationParent, destination: Path, stage_handle: int,
    stage_identity: tuple[int, ...], manifest: Mapping[str, Any],
) -> None:
    try:
        _verify_published_tree(parent, destination, stage_handle, stage_identity, manifest)
    except Exception as exc:
        raise CandidateSnapshotError(
            "publication completed but its postcondition could not be verified; state is ambiguous"
        ) from exc

def _cleanup_unpublished_stage(
    parent: _PinnedDestinationParent, stage_name: str, stage_identity: tuple[int, ...],
    stage_handle: int | None,
) -> None:
    if parent.fd is not None:
        _cleanup_linux_stage(parent, stage_name, stage_identity)
    elif stage_handle is None:
        raise CandidateSnapshotError("CLEANUP_UNCONFIRMED: Windows staging handle is unavailable")
    else:
        _cleanup_windows_stage(parent, stage_name, stage_identity, stage_handle)


def _materialize_with_parent(
    root: Path, manifest: Mapping[str, Any], destination: Path, parent: _PinnedDestinationParent,
) -> str:
    stage_name: str | None = None
    stage_handle: int | None = None
    stage_identity: tuple[int, ...] | None = None
    stage_root: Path | None = None
    stage_created = False
    published = False
    preserve_windows_resources = False
    try:
        _ensure_destination_absent(parent, destination)
        stage_name, stage_handle, stage_identity, stage_root = _create_private_stage(parent)
        stage_created = True
        tree = _build_private_stage_tree(root, manifest, stage_root)
        _rename_verified_stage(parent, stage_name, stage_handle, stage_identity, destination.name)
        published = True
        _verify_publication_postcondition(parent, destination, stage_handle, stage_identity, manifest)
        return tree
    except Exception as exc:
        if isinstance(exc, _WindowsPublicationIndeterminate):
            _WINDOWS_INDETERMINATE_PUBLICATIONS.append(exc)
            preserve_windows_resources = True
        if (not preserve_windows_resources and stage_created and not published
                and stage_name is not None and stage_identity is not None):
            try:
                _cleanup_unpublished_stage(parent, stage_name, stage_identity, stage_handle)
            except Exception as cleanup_exc:
                raise CandidateSnapshotError(
                    f"CLEANUP_UNCONFIRMED for staging entry {stage_name}: {cleanup_exc}"
                ) from exc
        if isinstance(exc, CandidateSnapshotError):
            raise
        raise CandidateSnapshotError("candidate materialization failed before publication") from exc
    finally:
        if not preserve_windows_resources:
            try:
                _close_stage_handle(stage_handle)
            finally:
                _close_destination_parent(parent)


def _build_and_materialize(root: Path, manifest: Mapping[str, Any], destination: Path) -> str:
    destination = _absolute_destination(destination)
    root = root.resolve()
    if _path_is_at_or_below(root, destination) or _path_is_at_or_below(destination, root):
        raise CandidateSnapshotError("materialization destination must be outside the repository")
    try:
        parent = _open_destination_parent(destination)
    except OSError as exc:
        raise CandidateSnapshotError("destination parent cannot be safely pinned") from exc
    return _materialize_with_parent(root, manifest, destination, parent)

def _verify_pinned_repository(root: Path, manifest: Mapping[str, Any]) -> None:
    object_format = _git(root, ["rev-parse", "--show-object-format"]).decode().strip()
    head = _git(root, ["rev-parse", "HEAD"]).decode().strip()
    tree = _git(root, ["rev-parse", "HEAD^{tree}"]).decode().strip()
    if object_format != manifest["object_format"] or head != manifest["base"]["head"] or tree != manifest["base"]["tree"]:
        raise CandidateSnapshotError("repository object format or HEAD/tree differs from pinned base")
    if _git(root, ["diff", "--cached", "--quiet"]).strip():
        raise CandidateSnapshotError("staging must be empty")


def verify_workspace(root: Path, manifest_bytes: bytes, expected_sha256: str,
                     destination: Path, *, authenticate_reference: ReferenceAuthenticator | None = None) -> CandidateSnapshot:
    """Verify a selected live workspace, then materialize its exact source bytes."""
    root = root.resolve()
    if not root.is_dir():
        raise CandidateSnapshotError("repository root does not exist")
    try:
        manifest = parse_manifest(manifest_bytes, expected_sha256)
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(str(exc)) from exc
    _verify_pinned_repository(root, manifest)
    _authenticate(manifest, root, authenticate_reference, workspace=True)
    try:
        raw_tree = _git(root, ["ls-tree", "-r", "-z", "--full-tree", manifest["base"]["tree"]])
        validate_git_overlay(manifest, raw_tree, lambda oid: _git(root, ["cat-file", "blob", oid]))
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(str(exc)) from exc
    actual_paths = _scan_workspace(root, manifest, workspace=True)
    expected_paths = {item["path"] for item in manifest["files"]}
    if actual_paths != expected_paths:
        raise CandidateSnapshotError("workspace source path set differs from complete manifest")
    _verify_files(root, manifest)
    for row in manifest["workspace_context"]["protected_files"]:
        try:
            path, _ = _safe_file(root, row["path"])
            data = path.read_bytes()
        except OSError as exc:
            raise CandidateSnapshotError(f"protected workspace file is unavailable: {row['path']}") from exc
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise CandidateSnapshotError(f"protected workspace file changed: {row['path']}")
    destination = _absolute_destination(destination)
    tree = _build_and_materialize(root, manifest, destination)
    return CandidateSnapshot(manifest, expected_sha256, tree, destination)


def verify_materialized(root: Path, manifest_bytes: bytes, expected_sha256: str, *,
                        authenticate_reference: ReferenceAuthenticator | None = None) -> CandidateSnapshot:
    """Verify a complete isolated source tree without requiring workspace authority."""
    root = root.resolve()
    try:
        manifest = parse_manifest(manifest_bytes, expected_sha256)
    except CandidateManifestError as exc:
        raise CandidateSnapshotError(str(exc)) from exc
    _authenticate(manifest, root, authenticate_reference, workspace=False)
    actual_paths = _scan_workspace(root, manifest, workspace=False)
    expected_paths = {item["path"] for item in manifest["files"]}
    if actual_paths != expected_paths:
        raise CandidateSnapshotError("materialized source path set differs from complete manifest")
    _verify_files(root, manifest)
    with tempfile.TemporaryDirectory(prefix="candidate-materialized-check-") as scratch:
        scratch_root = Path(scratch)
        clean_env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        clean_env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
                          "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1"})
        _git_init = subprocess.run(["git", "init", "-q", f"--object-format={manifest['object_format']}", scratch], env=clean_env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        if _git_init.returncode:
            raise CandidateSnapshotError("cannot initialize isolated materialized-source verifier")
        actual_format = _git(scratch_root, ["rev-parse", "--show-object-format"], env=clean_env).decode().strip()
        if actual_format != manifest["object_format"]:
            raise CandidateSnapshotError("isolated verifier object format differs from manifest")
        with tempfile.TemporaryDirectory(prefix="candidate-materialized-objects-") as objects_temp:
            object_root = Path(objects_temp)
            (object_root / "objects").mkdir()
            env = dict(clean_env)
            env.update({
                        "GIT_OBJECT_DIRECTORY": str(object_root / "objects"), "GIT_INDEX_FILE": str(object_root / "index")})
            tree = _construct_tree(scratch_root, root, manifest, env)
    if tree != manifest["source_tree"]:
        raise CandidateSnapshotError("materialized source tree differs from manifest source_tree")
    return CandidateSnapshot(manifest, expected_sha256, tree, root)


__all__ = ["CandidateSnapshot", "CandidateSnapshotError", "ReferenceAuthenticator", "verify_materialized", "verify_workspace"]
