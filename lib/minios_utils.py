#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniOS utilities for kernel management
Handles MiniOS directory detection, permission checks, and file operations
"""

import os
import shutil
import subprocess
import glob
import tempfile
import gettext
import stat
import uuid
import hashlib
import json
from typing import Optional, List, Tuple

try:
    from .bootloader_utils import update_bootloader_configs as _update_bootloader_configs_impl
    from .kernel_acquisition import validate_embedded_support_tree
except ImportError:
    from bootloader_utils import update_bootloader_configs as _update_bootloader_configs_impl
    from kernel_acquisition import validate_embedded_support_tree

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext

def find_minios_directory() -> Optional[str]:
    """Find MiniOS directory on the system"""
    common_paths = [
        "/run/initramfs/memory/data/minios",
        "/lib/live/mount/medium/minios"
    ]

    # Check each path
    for path_pattern in common_paths:
        if '*' in path_pattern:
            # Handle wildcard paths
            for path in glob.glob(path_pattern):
                if _is_valid_minios_directory(path):
                    return path
        else:
            # Direct path check
            if _is_valid_minios_directory(path_pattern):
                return path_pattern

    # Try to find mounted filesystems with minios folder
    try:
        result = subprocess.run(['findmnt', '--noheadings', '--output', 'TARGET', '--raw',
                                 '-t', 'vfat,ext4,ext2,btrfs,ntfs,ntfs3,exfat'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if result.returncode == 0:
            for mount_point in result.stdout.splitlines():
                mount_point = mount_point.strip()
                if mount_point:
                    minios_path = os.path.join(mount_point, 'minios')
                    if _is_valid_minios_directory(minios_path):
                        return minios_path
    except Exception:
        pass

    return None

def _is_valid_minios_directory(path: str) -> bool:
    """Check if directory looks like a valid MiniOS directory"""
    if not os.path.exists(path):
        return False

    # Check for typical MiniOS structure
    expected_items = ['boot', '01-kernel*', '02-firmware*']
    found_items = 0

    try:
        items = os.listdir(path)
        for item in items:
            if item == 'boot':
                found_items += 1
            elif item.startswith('01-kernel'):
                found_items += 1
            elif item.startswith('02-firmware'):
                found_items += 1
    except PermissionError:
        return False

    return found_items >= 1  # At least one expected item

def get_kernel_repository_path(minios_path: str) -> str:
    """Get the path to the kernel repository."""
    return os.path.join(minios_path, "kernels")


def _trusted_directory(path: str) -> bool:
    """Return whether a directory and each existing parent are non-symlinks."""
    path = os.path.abspath(path)
    current = os.path.sep
    for component in path.strip(os.path.sep).split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            return False
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            return False
    return True


def _require_trusted_minios_root(minios_path: str) -> None:
    if not _trusted_directory(minios_path):
        raise ValueError('MiniOS root or one of its parents is symlinked or untrusted')
    boot = os.path.join(minios_path, 'boot')
    if not _trusted_directory(boot):
        raise ValueError('MiniOS boot root is symlinked or untrusted')


def _regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _files_are_identical(first: str, second: str) -> bool:
    """Compare two regular files byte-for-byte without following symlinks."""
    if not _regular_file(first) or not _regular_file(second):
        return False
    try:
        if os.lstat(first).st_size != os.lstat(second).st_size:
            return False
        with open(first, 'rb') as first_file, open(second, 'rb') as second_file:
            while True:
                first_chunk = first_file.read(1024 * 1024)
                second_chunk = second_file.read(1024 * 1024)
                if first_chunk != second_chunk:
                    return False
                if not first_chunk:
                    return True
    except OSError:
        return False


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def validate_kernel_bundle_artifacts(kernel_version: str, squashfs_file: str,
                                     vmlinuz_file: str,
                                     initramfs_file: str,
                                     allow_legacy: bool = False) -> dict:
    """Preflight a bundle manifest and every final payload hash."""
    for path in (squashfs_file, vmlinuz_file, initramfs_file):
        if not _regular_file(path) or os.path.getsize(path) == 0:
            raise RuntimeError(
                'Kernel artifact is not a nonempty regular file: {}'.format(path))
    if not shutil.which('unsquashfs'):
        raise RuntimeError('unsquashfs is required for kernel bundle preflight')
    workspace = tempfile.mkdtemp(prefix='minios-kernel-preflight-')
    try:
        result = subprocess.run(
            ['unsquashfs', '-no-progress', '-d', workspace, squashfs_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                'Cannot inspect kernel module: {}'.format(result.stderr.strip()))
        support = os.path.join(
            workspace, 'usr', 'share', 'minios', 'kernel-dpkg')
        manifest = validate_embedded_support_tree(
            support, kernel_version, allow_legacy=allow_legacy)
        owned_paths = set()
        owned_records = {}
        for package in manifest['packages']:
            relative = package.get('payload_manifest')
            if not relative:
                continue
            with open(os.path.join(support, relative), 'r',
                      encoding='utf-8') as payload_file:
                payload = json.load(payload_file)
            if payload.get('dpkg_instance') != package['dpkg_instance']:
                raise RuntimeError('Kernel payload manifest instance mismatch')
            if set(payload) != {'format', 'dpkg_instance', 'files'} or payload.get('format') != 1:
                raise RuntimeError('Kernel payload manifest is not canonical format 1')
            for record in payload.get('files', []):
                path = record.get('path', '')
                entry_type = record.get('type')
                expected_fields = ({'path', 'type', 'sha256'}
                                   if entry_type == 'file' else {'path', 'type'})
                if entry_type == 'symlink':
                    expected_fields.add('target')
                if (set(record) != expected_fields or
                        entry_type not in ('file', 'directory', 'symlink') or
                        not path.startswith('/') or '..' in path.split('/')):
                    raise RuntimeError('Kernel payload record is invalid')
                if path in owned_paths:
                    raise RuntimeError('Kernel payload ownership is duplicated')
                owned_paths.add(path)
                owned_records[path] = entry_type
                if path == '/boot/vmlinuz-{}'.format(kernel_version):
                    materialized = vmlinuz_file
                else:
                    materialized = os.path.join(workspace, path.lstrip('/'))
                entry_type = record.get('type')
                valid = False
                if entry_type == 'file':
                    valid = (_regular_file(materialized) and
                             _file_sha256(materialized) == record.get('sha256'))
                elif entry_type == 'symlink':
                    target = record.get('target', '')
                    resolved = os.path.normpath(
                        os.path.join(os.path.dirname(path), target))
                    allowed_target = (
                        resolved.startswith('/boot/') or
                        resolved.startswith('/lib/modules/{}/'.format(kernel_version)) or
                        resolved.startswith('/usr/lib/modules/{}/'.format(kernel_version))
                    )
                    valid = (target and '\x00' not in target and
                             not os.path.isabs(target) and allowed_target and
                             os.path.islink(materialized) and
                             os.readlink(materialized) == target)
                elif entry_type == 'directory':
                    valid = (os.path.isdir(materialized) and
                             not os.path.islink(materialized))
                if not valid:
                    raise RuntimeError(
                        'Kernel payload hash mismatch: {}'.format(path))
        legacy = set(manifest) == {'format', 'kernel_version', 'packages'}
        if legacy:
            module_roots = (
                os.path.join(workspace, 'lib', 'modules', kernel_version),
                os.path.join(workspace, 'usr', 'lib', 'modules', kernel_version),
            )
            has_module = False
            for module_root in module_roots:
                if not os.path.isdir(module_root) or os.path.islink(module_root):
                    continue
                for parent, _directories, filenames in os.walk(module_root):
                    if any(filename.endswith(('.ko', '.ko.xz', '.ko.zst'))
                           and _regular_file(os.path.join(parent, filename))
                           for filename in filenames):
                        has_module = True
                        break
                if has_module:
                    break
            if not has_module:
                raise RuntimeError('Legacy kernel bundle has no loadable modules')
        else:
            required = {
                '/boot/vmlinuz-' + kernel_version,
                '/boot/config-' + kernel_version,
                '/boot/System.map-' + kernel_version,
            }
            if any(owned_records.get(path) != 'file' for path in required):
                raise RuntimeError('Kernel bundle lacks mandatory boot payload')
            module_prefixes = (
                '/lib/modules/{}/'.format(kernel_version),
                '/usr/lib/modules/{}/'.format(kernel_version),
            )
            if not any(entry_type == 'file' and path.endswith('.ko') and
                       path.startswith(module_prefixes)
                       for path, entry_type in owned_records.items()):
                raise RuntimeError('Kernel bundle has no loadable module payload')
        return manifest
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _kernel_artifact_names(kernel_version: str) -> List[str]:
    return [
        '01-kernel-{}.sb'.format(kernel_version),
        'vmlinuz-{}'.format(kernel_version),
        'initrfs-{}.img'.format(kernel_version),
    ]


def _active_kernel_artifacts(minios_path: str, kernel_version: str) -> List[str]:
    names = _kernel_artifact_names(kernel_version)
    return [
        os.path.join(minios_path, names[0]),
        os.path.join(minios_path, 'boot', names[1]),
        os.path.join(minios_path, 'boot', names[2]),
    ]


def publish_kernel_artifacts(output_dir: str, artifact_files: List[str]) -> List[str]:
    """Publish completed regular artifacts without following destination links."""
    output_dir = os.path.abspath(output_dir)
    parent = os.path.dirname(output_dir)
    if not _trusted_directory(parent):
        raise RuntimeError('Kernel output parent is symlinked or untrusted')

    if os.path.lexists(output_dir):
        if not _trusted_directory(output_dir):
            raise RuntimeError('Kernel output directory is symlinked or untrusted')
    else:
        os.mkdir(output_dir, 0o755)

    flags = os.O_RDONLY
    flags |= getattr(os, 'O_DIRECTORY', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    directory_fd = os.open(output_dir, flags)
    published = []
    temporary_names = []
    try:
        for source in artifact_files:
            if not _regular_file(source):
                raise RuntimeError(
                    'Kernel artifact is not a regular non-symlink file: {}'.format(
                        source))
            destination_name = os.path.basename(source)
            if destination_name in ('', '.', '..'):
                raise RuntimeError('Invalid kernel artifact name')

            temporary_name = '.{}.{}'.format(
                destination_name, uuid.uuid4().hex)
            temporary_names.append(temporary_name)
            destination_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                getattr(os, 'O_NOFOLLOW', 0),
                0o600,
                dir_fd=directory_fd,
            )
            try:
                source_flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                source_fd = os.open(source, source_flags)
                try:
                    source_stat = os.fstat(source_fd)
                    if not stat.S_ISREG(source_stat.st_mode):
                        raise RuntimeError('Kernel artifact changed during publication')
                    while True:
                        chunk = os.read(source_fd, 1024 * 1024)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination_fd, view)
                            view = view[written:]
                    os.fchmod(destination_fd, stat.S_IMODE(source_stat.st_mode))
                    os.fsync(destination_fd)
                finally:
                    os.close(source_fd)
            finally:
                os.close(destination_fd)

            # link() is an atomic no-replace publication: an existing regular
            # file, directory, or symlink makes it fail rather than be followed.
            os.link(
                temporary_name, destination_name,
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary_name, dir_fd=directory_fd)
            temporary_names.remove(temporary_name)
            published.append(destination_name)

        os.fsync(directory_fd)
        return [os.path.join(output_dir, name) for name in published]
    except Exception:
        for name in temporary_names:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass
        for name in reversed(published):
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(directory_fd)


def _atomic_write(path: str, content) -> None:
    """Replace a regular file atomically while preserving its mode."""
    parent = os.path.dirname(path)
    if not _trusted_directory(parent):
        raise RuntimeError('Untrusted destination directory')
    old_mode = 0o644
    if os.path.lexists(path):
        if not _regular_file(path):
            raise RuntimeError('Refusing to write a non-regular file')
        old_mode = stat.S_IMODE(os.lstat(path).st_mode)
    fd, temporary = tempfile.mkstemp(prefix='.minios-kernel-', dir=parent)
    try:
        os.fchmod(fd, old_mode)
        binary = isinstance(content, bytes)
        if binary:
            fh = os.fdopen(fd, 'wb')
        else:
            fh = os.fdopen(fd, 'w', encoding='utf-8')
        with fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

def _kernel_repository_path(minios_path: str, kernel_version: str, resolve: bool = True) -> str:
    """Return a repository path only for a single, confined directory name."""
    if not kernel_version or os.path.basename(kernel_version) != kernel_version or kernel_version in ('.', '..'):
        raise ValueError("Invalid kernel version")
    _require_trusted_minios_root(minios_path)
    repository = get_kernel_repository_path(minios_path)
    if os.path.lexists(repository) and not _trusted_directory(repository):
        raise ValueError('Kernel repository is symlinked or untrusted')
    candidate = os.path.join(repository, kernel_version)
    if resolve:
        candidate = os.path.realpath(candidate)
    if os.path.commonpath((repository, candidate)) != repository:
        raise ValueError("Kernel path escapes repository")
    original = os.path.join(repository, kernel_version)
    if os.path.lexists(original) and os.path.islink(original):
        raise ValueError("Kernel repository entry is a symlink")
    return candidate

def get_kernel_path(minios_path: str, kernel_version: str) -> str:
    """Get the path to a specific kernel version in the repository."""
    return _kernel_repository_path(minios_path, kernel_version)

def package_kernel_to_repository(minios_path: str, kernel_version: str,
                                 squashfs_file: str, vmlinuz_file: str, initramfs_file: str) -> bool:
    """Packages a kernel and places it in the inactive kernel repository."""
    try:
        kernel_repo_path = get_kernel_repository_path(minios_path)
        kernel_version_path = _kernel_repository_path(minios_path, kernel_version, resolve=False)
        _require_trusted_minios_root(minios_path)
        if not os.path.exists(kernel_repo_path):
            os.mkdir(kernel_repo_path)
        if not _trusted_directory(kernel_repo_path):
            raise RuntimeError('Kernel repository is symlinked or untrusted')
        if os.path.lexists(kernel_version_path):
            raise RuntimeError(f"Kernel {kernel_version} already exists in repository")

        for source in (squashfs_file, vmlinuz_file, initramfs_file):
            if not _regular_file(source):
                raise RuntimeError(
                    'Kernel artifact is not a regular non-symlink file: {}'.format(
                        source))
        source_names = [
            os.path.basename(squashfs_file),
            os.path.basename(vmlinuz_file),
            os.path.basename(initramfs_file),
        ]
        if source_names != _kernel_artifact_names(kernel_version):
            raise RuntimeError('Kernel artifact names do not match the bundle version')
        validate_kernel_bundle_artifacts(
            kernel_version, squashfs_file, vmlinuz_file, initramfs_file)

        staging_path = tempfile.mkdtemp(prefix='.kernel-copy-', dir=kernel_repo_path)
        try:
            for source in (squashfs_file, vmlinuz_file, initramfs_file):
                destination = os.path.join(staging_path, os.path.basename(source))
                shutil.copy2(source, destination)
                if not _files_are_identical(source, destination):
                    raise RuntimeError('Kernel artifact copy verification failed')
            os.rename(staging_path, kernel_version_path)
        except Exception:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise

        return True
    except Exception as e:
        print(f"Failed to package kernel to repository: {e}")
        return False

def get_active_kernel(minios_path: str) -> Optional[str]:
    """Gets the version of the currently active kernel from boot marker."""
    # First try to get active kernel from boot marker
    marker_file = os.path.join(minios_path, "boot", "active-kernel")

    if _regular_file(marker_file):
        try:
            with open(marker_file, 'r') as f:
                kernel_version = f.read().strip()
                if kernel_version:
                    return kernel_version
        except Exception as e:
            print(f"Warning: Error reading active kernel marker {marker_file}: {e}")

    # Fallback: check for vmlinuz files in boot directory
    boot_path = os.path.join(minios_path, "boot")
    if not os.path.exists(boot_path):
        return None

    vmlinuz_files = glob.glob(os.path.join(boot_path, "vmlinuz-*"))
    if not vmlinuz_files:
        return None

    try:
        first_file = os.path.basename(vmlinuz_files[0])
        return first_file.replace("vmlinuz-", "")
    except IndexError:
        return None

def get_active_kernel_files(minios_path: str, kernel_version: str = None) -> List[str]:
    """Gets list of active kernel files (vmlinuz, initramfs, squashfs)."""
    files = []

    if kernel_version:
        # Get files for specific kernel version
        boot_path = os.path.join(minios_path, "boot")
        if os.path.exists(boot_path):
            vmlinuz_file = os.path.join(boot_path, f"vmlinuz-{kernel_version}")
            if os.path.exists(vmlinuz_file):
                files.append(vmlinuz_file)

            initramfs_file = os.path.join(boot_path, f"initrfs-{kernel_version}.img")
            if os.path.exists(initramfs_file):
                files.append(initramfs_file)

        squashfs_file = os.path.join(minios_path, f"01-kernel-{kernel_version}.sb")
        if os.path.exists(squashfs_file):
            files.append(squashfs_file)
    else:
        # Get all active files (original behavior)
        boot_path = os.path.join(minios_path, "boot")
        if os.path.exists(boot_path):
            vmlinuz_files = glob.glob(os.path.join(boot_path, "vmlinuz-*"))
            files.extend(vmlinuz_files)

            # Check for initramfs files
            initramfs_files = glob.glob(os.path.join(boot_path, "initrfs-*.img"))
            files.extend(initramfs_files)

        # Check for squashfs files in minios root
        squashfs_files = glob.glob(os.path.join(minios_path, "01-kernel-*.sb"))
        files.extend(squashfs_files)

    return files

def _get_filesystem_type(path: str) -> str:
    """Get filesystem type for a given path."""
    try:
        result = subprocess.run(['stat', '-f', '-c', '%T', path],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback method using /proc/mounts
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mount_point, fs_type = parts[1], parts[2]
                        if path.startswith(mount_point):
                            return fs_type
        except Exception:
            pass
    return "unknown"

def _update_bootloader_configs(minios_path: str, kernel_version: str) -> bool:
    """Update GRUB and Syslinux configuration files with new kernel version."""
    fs_type = _get_filesystem_type(minios_path)
    print(f"Updating bootloader configs on filesystem type: {fs_type}")
    return _update_bootloader_configs_impl(minios_path, kernel_version)

def deactivate_current_kernel(minios_path: str) -> bool:
    """Retain a verified repository copy before removing inactive boot files."""
    active_kernel_version = get_active_kernel(minios_path)
    if not active_kernel_version:
        return True # Nothing to do

    # Always ensure the repository directory exists
    try:
        _require_trusted_minios_root(minios_path)
        repository = get_kernel_repository_path(minios_path)
        if not os.path.exists(repository):
            os.mkdir(repository)
        if not _trusted_directory(repository):
            raise RuntimeError('Kernel repository is symlinked or untrusted')
        kernel_version_path = _kernel_repository_path(
            minios_path, active_kernel_version, resolve=False)
        if os.path.lexists(kernel_version_path):
            if not _trusted_directory(kernel_version_path):
                raise RuntimeError('Kernel repository entry is symlinked or untrusted')
    except Exception as e:
        print(f"Failed to prepare kernel repository: {e}")
        return False

    is_running = is_kernel_currently_running(active_kernel_version)

    try:
        active_files = _active_kernel_artifacts(
            minios_path, active_kernel_version)
        if not all(_regular_file(path) for path in active_files):
            raise RuntimeError(
                'Active kernel bundle is incomplete or contains non-regular files')

        expected_names = _kernel_artifact_names(active_kernel_version)
        if os.path.lexists(kernel_version_path):
            if set(os.listdir(kernel_version_path)) != set(expected_names):
                raise RuntimeError(
                    'Retained repository copy is incomplete or has unexpected files')
            for active_file in active_files:
                repository_file = os.path.join(
                    kernel_version_path, os.path.basename(active_file))
                if not _files_are_identical(active_file, repository_file):
                    raise RuntimeError(
                        'Retained repository copy differs from active kernel bundle')
        else:
            staging_path = tempfile.mkdtemp(
                prefix='.kernel-retain-', dir=repository)
            try:
                for active_file in active_files:
                    destination = os.path.join(
                        staging_path, os.path.basename(active_file))
                    shutil.copy2(active_file, destination)
                    if not _files_are_identical(active_file, destination):
                        raise RuntimeError(
                            'Retained repository copy verification failed')
                os.rename(staging_path, kernel_version_path)
            except Exception:
                shutil.rmtree(staging_path, ignore_errors=True)
                raise

        if is_running:
            print(f"Active kernel {active_kernel_version} is running - files copied to repository and left in place")
        else:
            removed_files = []
            try:
                for active_file in active_files:
                    os.unlink(active_file)
                    removed_files.append(active_file)
            except Exception:
                for active_file in removed_files:
                    repository_file = os.path.join(
                        kernel_version_path, os.path.basename(active_file))
                    if not os.path.lexists(active_file):
                        shutil.copy2(repository_file, active_file)
                raise
            print(f"Active kernel {active_kernel_version} deactivated - verified repository copy retained")

        return True
    except Exception as e:
        print(f"Failed to deactivate current kernel {active_kernel_version}: {e}")
        return False


def _snapshot_activation_state(minios_path: str):
    """Capture boot configs and the active marker before activation mutates them."""
    boot_files = {}
    for path in glob.glob(
            os.path.join(minios_path, 'boot', '**', '*.cfg'), recursive=True):
        if not _regular_file(path) or not _trusted_directory(os.path.dirname(path)):
            raise RuntimeError('Boot configuration is symlinked or untrusted')
        with open(path, 'rb') as config_file:
            boot_files[path] = config_file.read()

    marker_file = os.path.join(minios_path, 'boot', 'active-kernel')
    marker_state = None
    if os.path.lexists(marker_file):
        if not _regular_file(marker_file):
            raise RuntimeError('Active kernel marker is not a regular file')
        marker_mode = stat.S_IMODE(os.lstat(marker_file).st_mode)
        with open(marker_file, 'rb') as marker:
            marker_state = (marker.read(), marker_mode)
    return boot_files, marker_file, marker_state


def _restore_activation_state(boot_files, marker_file, marker_state) -> None:
    for path, content in boot_files.items():
        _atomic_write(path, content)
    if marker_state is None:
        if os.path.lexists(marker_file):
            if not _regular_file(marker_file):
                raise RuntimeError('Cannot remove untrusted active kernel marker')
            os.unlink(marker_file)
    else:
        content, mode = marker_state
        _atomic_write(marker_file, content)
        os.chmod(marker_file, mode)


def _restore_active_files(minios_path: str, kernel_version: str) -> None:
    """Restore a previous active bundle from its retained repository copy."""
    repository = get_kernel_path(minios_path, kernel_version)
    for destination in _active_kernel_artifacts(minios_path, kernel_version):
        source = os.path.join(repository, os.path.basename(destination))
        if not _regular_file(source):
            raise RuntimeError('Previous repository bundle is incomplete')
        if os.path.lexists(destination):
            if not _regular_file(destination):
                raise RuntimeError('Previous active destination is untrusted')
            if _files_are_identical(source, destination):
                continue
            raise RuntimeError('Previous active destination changed during rollback')
        shutil.copy2(source, destination)

def activate_kernel(minios_path: str, kernel_version: str) -> bool:
    """Activates a kernel from the repository."""
    try:
        _kernel_repository_path(minios_path, kernel_version, resolve=False)
    except ValueError as e:
        print(f"Failed to activate kernel {kernel_version}: {e}")
        return False
    # Handle running kernel activation
    if is_kernel_currently_running(kernel_version):
        current_active = get_active_kernel(minios_path)
        if current_active == kernel_version:
            print(f"Kernel {kernel_version} is already active and running.")
            return True
        else:
            # Deactivate current and use running kernel files
            try:
                running_artifacts = _active_kernel_artifacts(
                    minios_path, kernel_version)
                if not all(_regular_file(path) for path in running_artifacts):
                    raise RuntimeError('Running kernel bundle is incomplete')
                validate_kernel_bundle_artifacts(
                    kernel_version, running_artifacts[0],
                    running_artifacts[1], running_artifacts[2],
                    allow_legacy=True)
                previous_boot_files, marker_file, marker_state = \
                    _snapshot_activation_state(minios_path)
                if not deactivate_current_kernel(minios_path):
                    return False
                try:
                    if not _update_bootloader_configs(minios_path, kernel_version):
                        raise RuntimeError('Bootloader configuration update failed')
                    # The marker is part of the same rollback boundary as the
                    # active files and bootloader configuration.
                    _atomic_write(marker_file, kernel_version)
                except Exception:
                    if current_active:
                        _restore_active_files(minios_path, current_active)
                    _restore_activation_state(
                        previous_boot_files, marker_file, marker_state)
                    raise

                print(f"Activated running kernel {kernel_version} (files already in place).")
                return True
            except Exception as e:
                print(f"Failed to activate running kernel {kernel_version}: {e}")
                return False

    try:
        kernel_version_path = get_kernel_path(minios_path, kernel_version)
    except ValueError as e:
        print(f"Failed to activate kernel {kernel_version}: {e}")
        return False
    if not os.path.exists(kernel_version_path):
        print(f"Kernel version {kernel_version} not found in repository.")
        return False

    try:
        # Prepare kernel file paths
        squashfs_file = os.path.join(kernel_version_path, f"01-kernel-{kernel_version}.sb")
        vmlinuz_file = os.path.join(kernel_version_path, f"vmlinuz-{kernel_version}")
        initramfs_file = os.path.join(kernel_version_path, f"initrfs-{kernel_version}.img")

        # Verify all required files exist before copying
        if not _regular_file(squashfs_file):
            raise FileNotFoundError(f"SquashFS file not found: {squashfs_file}")
        if not _regular_file(vmlinuz_file):
            raise FileNotFoundError(f"Kernel file not found: {vmlinuz_file}")
        if not _regular_file(initramfs_file):
            raise FileNotFoundError(f"Initramfs file not found: {initramfs_file}")
        validate_kernel_bundle_artifacts(
            kernel_version, squashfs_file, vmlinuz_file, initramfs_file,
            allow_legacy=True)

        # Stage every input before changing the currently active kernel.
        previous_kernel = get_active_kernel(minios_path)
        previous_boot_files, marker_file, marker_state = \
            _snapshot_activation_state(minios_path)
        stage_dir = tempfile.mkdtemp(prefix='.kernel-activate-', dir=minios_path)
        try:
            staged_files = []
            for source, directory in ((squashfs_file, minios_path), (vmlinuz_file, os.path.join(minios_path, 'boot')), (initramfs_file, os.path.join(minios_path, 'boot'))):
                staged = os.path.join(stage_dir, os.path.basename(source))
                shutil.copy2(source, staged)
                staged_files.append((staged, os.path.join(directory, os.path.basename(source))))

            if not deactivate_current_kernel(minios_path):
                return False

            installed_files = []
            try:
                for staged, destination in staged_files:
                    if os.path.lexists(destination):
                        raise RuntimeError(f'Refusing to overwrite existing file: {destination}')
                    shutil.move(staged, destination)
                    installed_files.append(destination)
                if not _update_bootloader_configs(minios_path, kernel_version):
                    raise RuntimeError("Bootloader configuration update failed")
                # Do not commit the new active files or bootloader without the
                # matching marker. Marker failure enters the same rollback.
                _atomic_write(marker_file, kernel_version)
            except Exception:
                for destination in installed_files:
                    if os.path.exists(destination):
                        os.unlink(destination)
                # Restore files moved by deactivation before reporting failure.
                if previous_kernel and not is_kernel_currently_running(previous_kernel):
                    _restore_active_files(minios_path, previous_kernel)
                _restore_activation_state(
                    previous_boot_files, marker_file, marker_state)
                raise
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

        print(f"Successfully copied kernel files for {kernel_version}")
        return True
    except (Exception, IndexError) as e:
        print(f"Failed to activate kernel {kernel_version}: {e}")
        return False

def list_all_kernels(minios_path: str) -> List[str]:
    """Lists all unique kernel versions available (packaged, active, or running)."""
    kernels = set()

    # Add packaged kernels
    kernel_repo_path = get_kernel_repository_path(minios_path)
    if os.path.exists(kernel_repo_path):
        if _trusted_directory(kernel_repo_path):
            kernels.update([d for d in os.listdir(kernel_repo_path)
                            if not os.path.islink(os.path.join(kernel_repo_path, d))
                            and os.path.isdir(os.path.join(kernel_repo_path, d))])

    # Add active kernel
    active_kernel = get_active_kernel(minios_path)
    if active_kernel:
        kernels.add(active_kernel)

    # Add running kernel
    running_kernel = get_currently_running_kernel()
    if running_kernel:
        kernels.add(running_kernel)

    return sorted(list(kernels))

def delete_packaged_kernel(minios_path: str, kernel_version: str) -> bool:
    """Deletes a packaged kernel from the repository."""
    try:
        # Delete a repository symlink itself rather than resolving its target.
        if not kernel_version or os.path.basename(kernel_version) != kernel_version or kernel_version in ('.', '..'):
            raise ValueError('Invalid kernel version')
        _require_trusted_minios_root(minios_path)
        repository = get_kernel_repository_path(minios_path)
        if not _trusted_directory(repository):
            raise ValueError('Kernel repository is symlinked or untrusted')
        kernel_version_path = os.path.join(repository, kernel_version)
    except ValueError as e:
        print(f"Failed to delete packaged kernel {kernel_version}: {e}")
        return False
    if kernel_version == get_active_kernel(minios_path):
        print(f"Refusing to delete active kernel {kernel_version}")
        return False
    if is_kernel_currently_running(kernel_version):
        print(f"Refusing to delete running kernel {kernel_version}")
        return False
    if not os.path.lexists(kernel_version_path):
        return True # Already gone

    try:
        if os.path.islink(kernel_version_path):
            raise RuntimeError('Refusing to delete a symlinked repository entry')
        elif os.path.isdir(kernel_version_path):
            shutil.rmtree(kernel_version_path)
        else:
            raise RuntimeError("Kernel repository entry is not a directory")
        return True
    except Exception as e:
        print(f"Failed to delete packaged kernel {kernel_version}: {e}")
        return False

def get_kernel_info(minios_path: str, kernel_id: str) -> dict:
    """Get detailed information about a kernel."""
    active_kernel_id = get_active_kernel(minios_path)
    is_active = kernel_id == active_kernel_id
    is_running = is_kernel_currently_running(kernel_id)
    is_packaged = os.path.exists(get_kernel_path(minios_path, kernel_id))

    # Create better display name with version parsing
    display_name = kernel_id
    if '-' in kernel_id:
        parts = kernel_id.split('-')
        if len(parts) >= 2:
            version = parts[0]
            arch_flavor = '-'.join(parts[1:])
            display_name = f"{version} ({arch_flavor})"

    # Determine status with priorities
    status_parts = []
    status_color = "#666666"  # Default gray
    icon_name = "package-x-generic"  # Unified icon for all kernels

    if is_running:
        status_parts.append("Running")
        status_color = "#e74c3c"  # Red for running
        icon_name = "package-x-generic"  # Unified icon

    if is_active:
        if is_running:
            status_parts = ["Active & Running"]
        else:
            status_parts.append("Active")
            status_color = "#27ae60"  # Green for active
            icon_name = "package-x-generic"  # Unified icon

    if is_packaged and not is_active:
        status_parts.append("Available")
        status_color = "#3498db"  # Blue for packaged
        icon_name = "package-x-generic"  # Unified icon

    if not status_parts:
        return None

    # Determine kernel type and description
    kernel_type = "Standard"
    kernel_desc = ""

    kernel_lower = kernel_id.lower()
    if 'rt' in kernel_lower:
        kernel_type = "Real-time"
        kernel_desc = "Low-latency kernel for real-time applications"
    elif 'cloud' in kernel_lower:
        kernel_type = "Cloud"
        kernel_desc = "Optimized for virtualized environments"
    elif 'mos' in kernel_lower or 'minios' in kernel_lower:
        kernel_type = "MiniOS"
        kernel_desc = "Custom kernel for MiniOS distribution"
    elif 'generic' in kernel_lower:
        kernel_type = "Generic"
        kernel_desc = "General purpose kernel"
    elif 'lowlatency' in kernel_lower:
        kernel_type = "Low-latency"
        kernel_desc = "Reduced latency for audio/video applications"
    else:
        kernel_desc = "Linux kernel"

    # Get file sizes for additional info
    size_info = ""
    if is_active or is_packaged:
        try:
            kernel_path = get_kernel_path(minios_path, kernel_id) if is_packaged else minios_path
            if is_active and not is_packaged:
                # Active kernel files are in different locations
                sb_files = glob.glob(os.path.join(minios_path, "01-kernel-*.sb"))
                if sb_files:
                    sb_size = os.path.getsize(sb_files[0])
                    size_info = f" • {_format_size(sb_size)}"
            elif is_packaged:
                sb_files = glob.glob(os.path.join(kernel_path, "01-kernel-*.sb"))
                if sb_files:
                    sb_size = os.path.getsize(sb_files[0])
                    size_info = f" • {_format_size(sb_size)}"
        except Exception:
            pass

    info = {
        'id': kernel_id,
        'display_name': display_name,
        'version': kernel_id,
        'status': " ".join(status_parts),
        'status_color': status_color,
        'icon_name': icon_name,
        'kernel_type': kernel_type,
        'description': f"{kernel_type} kernel{size_info}",
        'full_description': kernel_desc,
        'is_active': is_active,
        'is_running': is_running,
        'is_packaged': is_packaged
    }

    return info

def _format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def get_kernel_file_info(file_path: str) -> dict:
    """Get file information (size, date) for a kernel file"""
    file_info = {'size': 0, 'size_text': 'Unknown', 'date': 'Unknown'}

    try:
        if os.path.exists(file_path):
            stat = os.stat(file_path)
            file_info['size'] = stat.st_size

            # Format size in human readable format
            size = stat.st_size
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024.0:
                    file_info['size_text'] = f"{size:.1f} {unit}"
                    break
                size /= 1024.0

            # Format date
            import time
            file_info['date'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
    except Exception:
        pass

    return file_info

def get_currently_running_kernel() -> str:
    """Get the kernel version currently running on the system with comprehensive analysis"""
    import re

    # Method 1: Check mounted .sb modules to see which kernel module is active
    try:
        result = subprocess.run(['mount'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
        mount_output = result.stdout

        # Look for mounted kernel .sb files
        for line in mount_output.split('\n'):
            if '01-kernel-' in line and '.sb' in line and 'squashfs' in line:
                match = re.search(r'01-kernel-([^/\s]+\.sb)', line)
                if match:
                    kernel_sb = match.group(1)
                    kernel_version = kernel_sb.replace('.sb', '')
                    return kernel_version
    except subprocess.CalledProcessError:
        pass

    # Method 2: Fallback to uname -r
    try:
        result = subprocess.run(['uname', '-r'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
        kernel_version = result.stdout.strip()
        return kernel_version
    except subprocess.CalledProcessError:
        pass

    return ""

def is_kernel_currently_running(kernel_version: str, minios_path: str = None) -> bool:
    """Check if a specific kernel version is currently running"""
    return kernel_version == get_currently_running_kernel()

def get_system_type() -> str:
    """Get type of system (live, installed, etc.)"""
    if os.path.exists('/run/initramfs/memory'):
        return "Live system (running from media)"
    elif os.path.exists('/lib/live/mount'):
        return "Live system (running from media)"
    else:
        return "Installed system"

def get_union_filesystem_type() -> str:
    """Get the type of union filesystem used by MiniOS (aufs or overlayfs)"""
    try:
        # Check mount output for root filesystem
        result = subprocess.run(['mount'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        for line in result.stdout.split('\n'):
            if ' on / type ' in line:
                if 'aufs' in line:
                    return 'aufs'
                elif 'overlay' in line:
                    return 'overlayfs'

        # Fallback: check /proc/mounts
        with open('/proc/mounts', 'r') as f:
            for line in f:
                if line.startswith('aufs / aufs') or line.startswith('none / aufs'):
                    return 'aufs'
                elif line.startswith('overlay / overlay') or line.startswith('none / overlay'):
                    return 'overlayfs'

        # Default fallback
        return 'overlayfs'

    except (OSError, IOError, subprocess.CalledProcessError):
        # Default to overlayfs if detection fails
        return 'overlayfs'

def get_temp_dir_with_space_check(required_mb: int = 1024, prefix: str = "minios-kernel-", operation_type: str = "kernel_packaging", custom_temp_dir: str = None) -> str:
    """Get temporary directory with sufficient space.

    Checks available space in /tmp and falls back to alternative location if needed.

    Args:
        required_mb: Required space in megabytes (default: 1024MB for kernel packaging)
        prefix: Optional prefix for temporary directory name
        operation_type: Type of operation for logging purposes (e.g., "kernel_packaging")
        custom_temp_dir: Custom temporary directory path (if None, use automatic selection)

    Returns:
        str: Path to temporary directory with sufficient space

    Raises:
        RuntimeError: If insufficient space is available in all locations
    """
    REQUIRED_SPACE = int(required_mb * 1024 * 1024)  # Convert MB to bytes

    def make_private_workspace(parent):
        parent = os.path.abspath(parent)
        if not _trusted_directory(parent):
            raise RuntimeError(
                _("Temporary directory is symlinked or untrusted: {}").format(
                    parent))
        parent_stat = os.lstat(parent)
        parent_mode = stat.S_IMODE(parent_stat.st_mode)
        current_uid = os.geteuid()
        owner_is_caller = parent_stat.st_uid == current_uid
        root_sticky_parent = (
            parent_stat.st_uid == 0 and bool(parent_mode & stat.S_ISVTX))
        if not owner_is_caller and not root_sticky_parent:
            raise RuntimeError(
                _("Temporary directory is not owned by root: {}").format(parent))
        if parent_mode & 0o022 and not root_sticky_parent:
            raise RuntimeError(
                _("Temporary directory is writable by other users: {}").format(
                    parent))

        workspace = tempfile.mkdtemp(dir=parent, prefix=prefix)
        os.chmod(workspace, 0o700)
        workspace_stat = os.lstat(workspace)
        if (not stat.S_ISDIR(workspace_stat.st_mode)
                or workspace_stat.st_uid != current_uid
                or stat.S_IMODE(workspace_stat.st_mode) != 0o700):
            shutil.rmtree(workspace, ignore_errors=True)
            raise RuntimeError(
                _("Could not create a private root-owned packaging workspace"))
        return workspace

    # Check custom temporary directory first if provided
    if custom_temp_dir:
        if not os.path.exists(custom_temp_dir):
            raise RuntimeError(_("Custom temporary directory does not exist: {}").format(custom_temp_dir))

        if not os.access(custom_temp_dir, os.W_OK):
            raise RuntimeError(_("Custom temporary directory is not writable: {}").format(custom_temp_dir))

        try:
            statvfs_custom = os.statvfs(custom_temp_dir)
            available_space_custom = statvfs_custom.f_bavail * statvfs_custom.f_frsize

            if available_space_custom >= REQUIRED_SPACE:
                print("I: {}".format(_('Using custom temporary directory for {operation} ({available:.1f}MB available, {needed:.1f}MB needed)')).format(
                    operation=operation_type, available=available_space_custom / (1024*1024), needed=REQUIRED_SPACE / (1024*1024)), flush=True)
                return make_private_workspace(custom_temp_dir)
            else:
                raise RuntimeError(_("Insufficient space in custom temporary directory '{}' for {}: {:.1f}MB available, {:.1f}MB needed").format(
                    custom_temp_dir, operation_type, available_space_custom / (1024*1024), REQUIRED_SPACE / (1024*1024)))
        except (OSError, IOError) as e:
            raise RuntimeError(_("Cannot check space in custom temporary directory '{}': {}").format(custom_temp_dir, str(e)))

    # Primary choice: /tmp
    default_tmp = "/tmp"

    try:
        # Check available space in /tmp
        statvfs = os.statvfs(default_tmp)
        available_space = statvfs.f_bavail * statvfs.f_frsize

        if available_space >= REQUIRED_SPACE:
            # Sufficient space in /tmp
            print("I: {}".format(_('Using /tmp for {operation} ({available:.1f}MB available, {needed:.1f}MB needed)')).format(
                operation=operation_type, available=available_space / (1024*1024), needed=REQUIRED_SPACE / (1024*1024)), flush=True)
            return make_private_workspace(default_tmp)
        else:
            print("I: {}".format(_('Insufficient space in /tmp for {operation} ({available:.1f}MB available, {needed:.1f}MB needed)')).format(
                operation=operation_type, available=available_space / (1024*1024), needed=REQUIRED_SPACE / (1024*1024)), flush=True)

            # Alternative directory depends on filesystem type and initramfs type
            fs_type = get_union_filesystem_type()
            if os.path.exists('/run/initramfs/memory/changes'):
                if fs_type == 'aufs':
                    alt_tmp = "/run/initramfs/memory/changes/tmp"
                else:  # overlayfs
                    alt_tmp = "/run/initramfs/memory/changes/changes/tmp"
            elif os.path.exists('/lib/live/mount/changes'):
                if fs_type == 'aufs':
                    alt_tmp = "/lib/live/mount/changes/tmp"
                else:  # overlayfs
                    alt_tmp = "/lib/live/mount/changes/changes/tmp"
            else:
                print("W: {}".format(_('No live system changes directory found, using /tmp anyway')), flush=True)
                return make_private_workspace(default_tmp)

            print("I: {}".format(_('Detected {} filesystem, using alternative: {}')).format(
                fs_type, alt_tmp), flush=True)

            # Create alternative directory if it doesn't exist
            if not os.path.exists(alt_tmp):
                os.makedirs(alt_tmp, exist_ok=True)
                print("I: {}".format(_('Created alternative temporary directory: {}')).format(alt_tmp), flush=True)

            # Check space in alternative location
            statvfs_alt = os.statvfs(alt_tmp)
            available_space_alt = statvfs_alt.f_bavail * statvfs_alt.f_frsize

            if available_space_alt >= REQUIRED_SPACE:
                print("I: {}".format(_('Using alternative temporary directory: {} ({:.1f}MB available)')).format(
                    alt_tmp, available_space_alt / (1024*1024)), flush=True)
                return make_private_workspace(alt_tmp)
            else:
                # Not enough space anywhere
                raise RuntimeError(_(
                    "Insufficient disk space for operation. Need {:.1f}MB, but only {:.1f}MB available in /tmp and {:.1f}MB in {}"
                ).format(
                    required_mb,
                    available_space / (1024*1024),
                    available_space_alt / (1024*1024),
                    alt_tmp
                ))

    except (OSError, IOError) as e:
        # Fallback to default behavior if space checking fails
        print("W: {}".format(_('Could not check disk space: {}. Using default temporary directory.')).format(str(e)), flush=True)
        return make_private_workspace(default_tmp)
