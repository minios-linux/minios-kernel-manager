#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kernel utilities for MiniOS Kernel Manager
Handles kernel detection, download, and module management
"""

import os
import glob
import subprocess
import tempfile
import shutil
import re
import gettext
import json
import stat
import sys
from typing import Dict, List, Optional, Tuple

try:
    from .kernel_acquisition import (
        SUPPORT_DIR, analyze_and_extract_packages,
    )
except ImportError:
    from kernel_acquisition import (
        SUPPORT_DIR, analyze_and_extract_packages,
    )

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext


LAST_KERNEL_VERSIONS: Dict[str, Optional[str]] = {
    'display_version': None,
    'actual_version': None,
}

KERNEL_DPKG_METADATA_DIR = SUPPORT_DIR


def get_last_kernel_versions() -> Dict[str, Optional[str]]:
    """Return versions detected during the latest package processing."""
    return dict(LAST_KERNEL_VERSIONS)


def get_available_kernels() -> List[str]:
    """Get list of installed kernels"""
    kernels = []

    # Use simple kernel detection approach
    try:
        if os.path.exists("/lib/modules"):
            kernels = [k for k in os.listdir("/lib/modules")
                      if os.path.isdir(os.path.join("/lib/modules", k))]
    except (OSError, PermissionError):
        pass

    return sorted(kernels)


def get_manual_packages() -> List[str]:
    """Return empty list - manual packages are now selected via file picker"""
    # Manual packages are now selected through file picker dialog
    # This function is kept for compatibility but returns empty list
    return []


def get_repository_kernels() -> List[dict]:
    """Return real versioned kernel packages from the system APT cache."""
    packages = []
    try:
        result = subprocess.run(
            ['apt-cache', 'search', '^linux-image-[0-9]'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, check=True)
        seen = set()
        for line in result.stdout.strip().split('\n'):
            if not line or ' - ' not in line:
                continue
            package_name, description = line.split(' - ', 1)
            if (not re.match(r'^linux-image-[0-9][A-Za-z0-9.+:~_-]*$', package_name)
                    or 'dbg' in package_name or package_name in seen):
                continue
            seen.add(package_name)
            try:
                show = subprocess.run(
                    ['apt-cache', 'show', package_name],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    universal_newlines=True, check=True)
                info = _parse_package_info(
                    show.stdout, package_name, description)
                if info:
                    packages.append(info)
            except (subprocess.CalledProcessError, ValueError, IndexError):
                continue
    except subprocess.CalledProcessError:
        pass
    packages.sort(key=lambda item: item['version'], reverse=True)
    return packages

def _parse_package_info(apt_show_output: str, package_name: str, description: str) -> Optional[dict]:
    """Parse apt-cache show output to extract package information"""
    info = {
        'package': package_name,
        'version': '',
        'size': 0,
        'size_text': '',
        'description': description,
        'architecture': '',
        'installed_size': 0,
        'depends': []
    }

    for line in apt_show_output.split('\n'):
        line = line.strip()
        if line.startswith('Version: '):
            info['version'] = line.split(':', 1)[1].strip()
        elif line.startswith('Size: '):
            try:
                info['size'] = int(line.split(':', 1)[1].strip())
                info['size_text'] = _format_size(info['size'])
            except ValueError:
                pass
        elif line.startswith('Architecture: '):
            info['architecture'] = line.split(':', 1)[1].strip()
        elif line.startswith('Installed-Size: '):
            try:
                # Installed-Size is in KB
                info['installed_size'] = int(line.split(':', 1)[1].strip()) * 1024
            except ValueError:
                pass
        elif line.startswith('Depends: '):
            depends_str = line.split(':', 1)[1].strip()
            # Parse basic dependencies (ignore version constraints for now)
            deps = []
            for dep in depends_str.split(','):
                dep = dep.strip().split()[0]  # Get just package name
                if dep and not dep.startswith('${'):  # Skip variable substitutions
                    deps.append(dep)
            info['depends'] = deps[:5]  # Limit to first 5 dependencies

    return info if info['size'] > 0 else None

def _format_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    size_value = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_value < 1024.0:
            return f"{size_value:.1f} {unit}"
        size_value /= 1024.0
    return f"{size_value:.1f} TB"


def check_package_cache(force_update: bool = False) -> Tuple[bool, str]:
    """Optionally refresh the existing system APT package lists."""
    if not force_update:
        return True, ''
    try:
        subprocess.run(
            ['apt', 'update'], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True)
        return True, ''
    except subprocess.CalledProcessError as error:
        return False, _("Failed to update package lists: {}").format(error)


def _extract_dep_package(dep_line: str) -> Optional[str]:
    """Extract package name from apt-cache depends output line."""
    match = re.search(r'^\s*Depends:\s*(\S+)', dep_line)
    if not match:
        return None
    pkg = match.group(1).strip()
    if pkg.startswith('<') and pkg.endswith('>'):
        return None
    return pkg


def _is_versioned_kernel_package(package_name: str) -> bool:
    return bool(re.match(
        r'^linux-(?:image|binary|base|modules|modules-extra)-[0-9]'
        r'[A-Za-z0-9.+:~_-]*$', package_name)) and 'dbg' not in package_name


def resolve_kernel_dependencies(package_name: str) -> List[str]:
    """Return only versioned split-kernel packages required by the image."""
    dependencies = []
    pending = [package_name]
    inspected = set()
    environment = os.environ.copy()
    environment.update({'LC_ALL': 'C', 'LANG': 'C', 'LANGUAGE': 'C'})
    while pending:
        current = pending.pop(0)
        if current in inspected:
            continue
        inspected.add(current)
        try:
            result = subprocess.run(
                ['apt-cache', 'depends', current],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, env=environment)
        except subprocess.CalledProcessError:
            continue
        for line in result.stdout.splitlines():
            dependency = _extract_dep_package(line)
            if (not dependency or dependency == package_name or
                    not _is_versioned_kernel_package(dependency)):
                continue
            if dependency not in dependencies:
                dependencies.append(dependency)
                pending.append(dependency)
    return dependencies


def _detect_kernel_version_from_extracted(temp_dir: str) -> Optional[str]:
    """Detect actual kernel version from extracted package contents."""
    boot_paths = [
        os.path.join(temp_dir, 'boot'),
        os.path.join(temp_dir, 'usr', 'boot'),
    ]
    for boot_path in boot_paths:
        if not os.path.exists(boot_path):
            continue
        for item in os.listdir(boot_path):
            if item.startswith('vmlinuz-'):
                return item.replace('vmlinuz-', '')

    modules_base_paths = [
        os.path.join(temp_dir, 'lib', 'modules'),
        os.path.join(temp_dir, 'usr', 'lib', 'modules'),
    ]
    for modules_base in modules_base_paths:
        if not os.path.exists(modules_base):
            continue
        version_dirs = [
            d for d in os.listdir(modules_base)
            if os.path.isdir(os.path.join(modules_base, d))
        ]
        if version_dirs:
            return version_dirs[0]

    return None


def _extracted_modules_versions(temp_dir: str) -> List[str]:
    """Return list of kernel versions found under extracted lib/modules paths."""
    versions: List[str] = []
    modules_base_paths = [
        os.path.join(temp_dir, 'lib', 'modules'),
        os.path.join(temp_dir, 'usr', 'lib', 'modules'),
    ]

    for modules_base in modules_base_paths:
        if not os.path.exists(modules_base):
            continue
        for item in os.listdir(modules_base):
            item_path = os.path.join(modules_base, item)
            if os.path.isdir(item_path) and item not in versions:
                versions.append(item)

    return versions


def _deb_field(deb_path: str, field: str) -> str:
    result = subprocess.run(
        ['dpkg-deb', '-f', deb_path, field],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ''


def _deb_status_stanza(deb_path: str) -> str:
    result = subprocess.run(
        ['dpkg-deb', '-f', deb_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=True,
    )
    lines = result.stdout.rstrip().splitlines()
    out = []
    inserted = False
    for line in lines:
        out.append(line)
        if line.startswith('Package: ') and not inserted:
            out.append('Status: install ok installed')
            inserted = True
    if not inserted:
        out.insert(0, 'Status: install ok installed')
    return '\n'.join(out).rstrip() + '\n'


def _deb_installed_list(deb_path: str) -> str:
    result = subprocess.run(
        ['dpkg-deb', '-c', deb_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        path = ' '.join(parts[5:])
        if path.startswith('./'):
            path = path[1:]
        if path and path != '/':
            paths.append(path.rstrip('/'))
    return '/.\n' + '\n'.join(sorted(set(paths))) + ('\n' if paths else '')


def preserve_kernel_dpkg_metadata_from_debs(deb_files: List[str], temp_dir: str, kernel_version: Optional[str]) -> None:
    """Validate local archives and produce canonical frozen support data."""
    detected = analyze_and_extract_packages(deb_files, temp_dir)
    if kernel_version and detected != kernel_version:
        raise RuntimeError('Kernel package version does not match extracted payload')


def process_manual_packages(package_paths: List[str], temp_dir: str) -> str:
    """Process manually selected .deb package(s), return display kernel version."""
    try:
        if not package_paths:
            raise RuntimeError('No package files provided')

        for package_path in package_paths:
            try:
                package_mode = os.lstat(package_path).st_mode
            except OSError:
                raise RuntimeError(f'Package not found: {package_path}')
            if not stat.S_ISREG(package_mode):
                raise RuntimeError(
                    f'Package is not a regular non-symlink file: {package_path}')

        actual_kernel_version = analyze_and_extract_packages(
            package_paths, temp_dir)
        display_kernel_version = actual_kernel_version

        # Store both versions for package_kernel() initramfs generation logic.
        LAST_KERNEL_VERSIONS['display_version'] = display_kernel_version
        LAST_KERNEL_VERSIONS['actual_version'] = actual_kernel_version if actual_kernel_version else None

        return str(display_kernel_version)

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to process package(s): {e}")
    except Exception as e:
        raise RuntimeError(f"Error processing manual package(s): {e}")


def process_manual_package(package_path: str, temp_dir: str) -> str:
    """Backward-compatible wrapper for single-file manual package processing."""
    return process_manual_packages([package_path], temp_dir)


def download_kernel_package(package_name: str, temp_dir: str,
                            force_update: bool = False) -> str:
    """Download a real kernel package into the temporary workspace."""
    if (not package_name.startswith('linux-image-') or
            not _is_versioned_kernel_package(package_name)):
        raise RuntimeError(
            "Repository mode requires a real versioned kernel package")
    cache_ok, cache_message = check_package_cache(force_update)
    if not cache_ok:
        raise RuntimeError(cache_message)
    packages = [package_name] + resolve_kernel_dependencies(package_name)
    try:
        result = subprocess.run(
            ['apt-get', 'download'] + packages, cwd=temp_dir, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True)
        if result.stdout:
            print(result.stdout, end='' if result.stdout.endswith('\n') else '\n',
                  file=sys.stderr, flush=True)
        archives = []
        for package in packages:
            matches = glob.glob(os.path.join(temp_dir, '{}_*.deb'.format(package)))
            if not matches:
                raise RuntimeError(
                    "Downloaded .deb file for '{}' was not found".format(package))
            archives.extend(path for path in matches if path not in archives)
        actual_kernel_version = analyze_and_extract_packages(
            archives, temp_dir)
        LAST_KERNEL_VERSIONS['display_version'] = actual_kernel_version
        LAST_KERNEL_VERSIONS['actual_version'] = actual_kernel_version
        return str(actual_kernel_version)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Failed to download package '{}': {}".format(package_name, error))
    except Exception as e:
        raise RuntimeError("Error processing repository kernel: {}".format(e))


def get_non_symlink_modules_dir() -> str:
    """Get modules directory that is not a symlink"""
    if os.path.exists("/lib") and not os.path.islink("/lib"):
        return "/lib/modules"
    elif os.path.exists("/usr/lib") and not os.path.islink("/usr/lib"):
        return "/usr/lib/modules"
    else:
        raise RuntimeError("No valid modules directory found")


def locate_kernel_modules(kernel_version: str) -> str:
    """Locate kernel modules directory for installed kernel"""
    modules_dir = get_non_symlink_modules_dir()
    kernel_path = os.path.join(modules_dir, kernel_version)

    if not os.path.exists(kernel_path):
        raise RuntimeError(f"Kernel modules for {kernel_version} not found")

    return modules_dir


def prepare_temp_modules(kernel_version: str, temp_dir: str, force_reinstall: bool = False) -> None:
    """Prepare temporary kernel modules for repository kernel"""
    import shutil

    target_dir = get_non_symlink_modules_dir()
    target_path = os.path.join(target_dir, kernel_version)

    # Check if kernel is already installed
    if os.path.exists(target_path):
        if not force_reinstall:
            raise RuntimeError(f"KERNEL_EXISTS:{kernel_version}")
        else:
            # Remove existing installation for reinstall
            print(f"I: {_('Removing existing kernel modules for {kernel_version}').format(kernel_version=kernel_version)}")
            shutil.rmtree(target_path)

    # Find extracted modules
    extracted_paths = [
        os.path.join(temp_dir, "lib", "modules", kernel_version),
        os.path.join(temp_dir, "usr", "lib", "modules", kernel_version)
    ]

    # Find and verify modules directory
    found_paths = []
    for path in extracted_paths:
        if os.path.exists(path):
            found_paths.append(path)
            # Verify directory is readable
            try:
                os.listdir(path)
            except Exception as e:
                print(f"E: {_('Error reading modules directory: {error}').format(error=e)}")
            break  # Use first found path

    source_path = None
    for path in extracted_paths:
        if os.path.exists(path):
            source_path = path
            break

    if not source_path:
        raise RuntimeError(_("Kernel modules for {kernel_version} not found in package").format(kernel_version=kernel_version))

    # Copy modules to system location
    print(f"I: {_('Installing kernel modules to {target_path}').format(target_path=target_path)}", flush=True)
    shutil.copytree(source_path, target_path)

    # Run depmod if modules.dep doesn't exist
    modules_dep = os.path.join(target_path, "modules.dep")
    if not os.path.exists(modules_dep):
        print(f"I: {_('Building module dependencies')}", flush=True)
        subprocess.run(['depmod', kernel_version], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def cleanup_temp_modules(kernel_version: str) -> None:
    """Remove temporary kernel modules"""
    try:
        target_dir = get_non_symlink_modules_dir()
        target_path = os.path.join(target_dir, kernel_version)

        if os.path.exists(target_path):
            shutil.rmtree(target_path)
    except Exception:
        pass  # Ignore cleanup errors
