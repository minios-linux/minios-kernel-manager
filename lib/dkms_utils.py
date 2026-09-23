#!/usr/bin/env python3
"""Build selected, curated DKMS drivers for a packaged repository kernel."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from typing import List, Tuple


DKMS_SUPPORT_DIR = '.minios-kernel-dkms'
_ACTIVE_PROCESS = None

# Stable IDs are shared by the CLI and GUI. Package selection mirrors the DKMS
# section of linux-live/scripts/01-kernel/packages.list.
DRIVER_CATALOG = (
    {'id': 'ntfs3', 'label': 'NTFS3', 'package': 'ntfs3-dkms'},
    {'id': 'broadcom-sta', 'label': 'Broadcom STA', 'package': 'broadcom-sta-dkms'},
    {'id': 'aufs', 'label': 'AUFS', 'package': 'aufs-dkms'},
    {'id': 'aufs-ng', 'label': 'AUFS-NG', 'package': 'aufs-ng-dkms'},
    {'id': 'dynblk', 'label': 'DynBlk', 'package': 'dynblk-dkms'},
    {'id': 'zfs', 'label': 'OpenZFS', 'package': 'zfs-dkms'},
    {'id': 'rtl8723cs', 'label': 'Realtek RTL8723CS', 'package': 'realtek-rtl8723cs-dkms'},
    {'id': 'rtl8821au', 'label': 'Realtek RTL8821AU', 'package': 'realtek-rtl8821au-dkms'},
    {'id': 'rtl8821cu', 'label': 'Realtek RTL8821CU', 'package': 'realtek-rtl8821cu-dkms'},
    {'id': 'rtl88xxau', 'label': 'Realtek RTL88xxAU', 'package': 'realtek-rtl88xxau-dkms'},
    {'id': 'rtl8188eus', 'label': 'Realtek RTL8188EUS', 'package': 'realtek-rtl8188eus-dkms'},
    {'id': 'rtl8814au', 'label': 'Realtek RTL8814AU', 'package': 'realtek-rtl8814au-dkms'},
    {'id': 'rtl88x2bu', 'label': 'Realtek RTL88x2BU', 'package': 'realtek-rtl88x2bu-dkms'},
)

_CAPABILITIES = {
    'aufs': ('CONFIG_AUFS_FS', None),
    'ntfs3': ('CONFIG_NTFS3_FS', None),
    'btf_modules': ('CONFIG_DEBUG_INFO_BTF_MODULES', None),
    'rtw88_8723cs': ('CONFIG_RTW88_8723CS', 'rtw88_8723cs'),
    'rtw88_8821au': ('CONFIG_RTW88_8821AU', 'rtw88_8821au'),
    'rtw88_8821cu': ('CONFIG_RTW88_8821CU', 'rtw88_8821cu'),
    'rtw88_8814au': ('CONFIG_RTW88_8814AU', 'rtw88_8814au'),
    'rtw88_8822bu': ('CONFIG_RTW88_8822BU', 'rtw88_8822bu'),
}


def get_driver_catalog() -> List[dict]:
    """Return a copy of the curated driver catalog."""
    return [dict(item) for item in DRIVER_CATALOG]


def _regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _modules_path(temp_dir: str, kernel_version: str) -> Tuple[str, str]:
    for relative in ('usr/lib/modules', 'lib/modules'):
        path = os.path.join(temp_dir, relative, kernel_version)
        if os.path.isdir(path) and not os.path.islink(path):
            return path, relative
    raise RuntimeError('Extracted kernel modules were not found for {}'.format(kernel_version))


def _read_release_variant() -> str:
    for path in ('/etc/minios-release', '/etc/os-release'):
        try:
            with open(path, 'r', encoding='utf-8') as stream:
                values = {}
                for line in stream:
                    if '=' in line:
                        key, value = line.rstrip().split('=', 1)
                        values[key] = value.strip('"\'').lower()
                variant = values.get('PACKAGE_VARIANT') or values.get('EDITION')
                if variant:
                    return variant
        except OSError:
            continue
    return ''


def _host_distribution() -> Tuple[str, str]:
    values = {}
    try:
        with open('/etc/os-release', 'r', encoding='utf-8') as stream:
            for line in stream:
                if '=' in line:
                    key, value = line.rstrip().split('=', 1)
                    values[key] = value.strip('"\'')
    except OSError:
        pass
    profile = 'ubuntu' if values.get('ID', '').lower() == 'ubuntu' else 'debian'
    return values.get('VERSION_CODENAME', ''), profile


def get_compatible_driver_ids(kernel_package: str, architecture: str) -> List[str]:
    """Return catalog IDs compatible with repository package metadata."""
    match = re.search(r'(\d+)\.(\d+)', kernel_package or '')
    if not match or not architecture:
        return []
    suite, profile = _host_distribution()
    distribution_arch = architecture
    if architecture == 'i386' and 'pae' in kernel_package.lower():
        distribution_arch = 'i386-pae'
    context = {
        'distribution': suite,
        'profile': profile,
        'architecture': distribution_arch,
        'variant': _read_release_variant(),
        'series': '{}.{}'.format(match.group(1), match.group(2)),
        'capabilities': [],
    }
    compatible = []
    for driver in DRIVER_CATALOG:
        try:
            requests, _skipped = resolve_driver_selection(
                [driver['id']], context)
        except RuntimeError:
            continue
        if requests:
            compatible.append(driver['id'])
    return compatible


def detect_kernel_context(temp_dir: str, kernel_version: str) -> dict:
    """Detect the CondinAPT fields used by the 01-kernel driver list."""
    manifest_path = os.path.join(temp_dir, '.minios-kernel-dpkg', 'manifest.json')
    with open(manifest_path, 'r', encoding='utf-8') as stream:
        manifest = json.load(stream)
    userspace = manifest['userspace']
    kernel = manifest['kernel']
    match = re.match(r'^(\d+)\.(\d+)(?:[.-]|$)', kernel_version)
    if not match:
        raise RuntimeError('Unable to determine kernel series from {}'.format(kernel_version))

    config_path = os.path.join(temp_dir, 'boot', 'config-' + kernel_version)
    if not _regular_file(config_path):
        config_path = os.path.join(temp_dir, 'usr', 'boot', 'config-' + kernel_version)
    if not _regular_file(config_path):
        raise RuntimeError('Kernel configuration is missing for {}'.format(kernel_version))
    with open(config_path, 'r', encoding='utf-8', errors='replace') as stream:
        config = set(line.strip().split('=', 1)[0] for line in stream
                     if line.rstrip().endswith(('=y', '=m')))

    modules_path, _relative = _modules_path(temp_dir, kernel_version)
    aliases = ''
    alias_path = os.path.join(modules_path, 'modules.alias')
    if _regular_file(alias_path):
        with open(alias_path, 'r', encoding='utf-8', errors='replace') as stream:
            aliases = stream.read()
    capabilities = []
    for capability, (symbol, module) in _CAPABILITIES.items():
        if symbol in config or (module and re.search(r'\s{}$'.format(
                re.escape(module)), aliases, re.MULTILINE)):
            capabilities.append(capability)
    userspace_arch = userspace['dpkg_architecture']
    package_arch = kernel['package_architecture']
    if userspace_arch != package_arch:
        raise RuntimeError(
            'DKMS requires kernel and userspace package architectures to match')
    distribution_arch = package_arch
    if package_arch == 'i386' and 'pae' in kernel_version.lower():
        distribution_arch = 'i386-pae'
    return {
        'distribution': userspace['suite'],
        'profile': userspace['family'],
        'architecture': distribution_arch,
        'package_architecture': package_arch,
        'provider': 'distribution',
        'variant': _read_release_variant(),
        'series': '{}.{}'.format(match.group(1), match.group(2)),
        'capabilities': capabilities,
    }


def resolve_driver_selection(driver_ids: List[str], context: dict) -> Tuple[List[dict], List[str]]:
    """Resolve selected IDs to package requests and already-present drivers."""
    catalog = {item['id']: item for item in DRIVER_CATALOG}
    unknown = sorted(set(driver_ids) - set(catalog))
    if unknown:
        raise RuntimeError('Unknown DKMS driver(s): {}'.format(', '.join(unknown)))
    if len(driver_ids) != len(set(driver_ids)):
        raise RuntimeError('A DKMS driver may only be selected once')

    suite = context['distribution']
    arch = context['architecture']
    capabilities = set(context['capabilities'])
    requests = []
    skipped = []
    legacy = {'buster', 'beowulf', 'bullseye', 'chimaera', 'bookworm',
              'daedalus', 'bionic', 'focal', 'jammy'}
    excluded_8723 = {'buster', 'beowulf', 'bullseye', 'chimaera', 'bionic',
                     'focal', 'jammy', 'noble'}

    for driver_id in driver_ids:
        item = dict(catalog[driver_id])
        package = item['package']
        unavailable = None
        capability = None
        if driver_id == 'ntfs3':
            capability = 'ntfs3'
        elif driver_id == 'broadcom-sta' and (suite == 'jammy' or arch == 'i386'):
            unavailable = 'not supported on Ubuntu Jammy or i386'
        elif driver_id == 'aufs':
            capability = 'aufs'
            if context['profile'] != 'debian' or suite not in {'buster', 'beowulf'}:
                unavailable = 'available only for Debian Buster/Beowulf kernels'
        elif driver_id == 'aufs-ng':
            capability = 'aufs'
            if context['series'] != '6.12' or arch == 'i386':
                unavailable = 'available only for non-i386 Linux 6.12 kernels'
        elif driver_id == 'dynblk' and context['series'] != '6.12':
            unavailable = 'available only for Linux 6.12 kernels'
        elif driver_id == 'zfs' and arch != 'amd64':
            unavailable = 'available only for amd64'
        elif driver_id == 'rtl8723cs':
            capability = 'rtw88_8723cs'
            if suite in excluded_8723:
                unavailable = 'not supported by this distribution release'
        elif driver_id == 'rtl8821au':
            capability = 'rtw88_8821au'
        elif driver_id == 'rtl8821cu':
            capability = 'rtw88_8821cu'
        elif driver_id == 'rtl8814au':
            capability = 'rtw88_8814au'
            if arch in {'i386', 'i386-pae'} and suite in legacy:
                package += '=5.8.5.1~git20240527.d8208c8-0kali1'
        elif (driver_id == 'rtl88xxau' and
              arch in {'i386', 'i386-pae'} and suite in legacy):
            package += '=5.6.4.2~git20240726.63cf0b4-0kali1'
        elif driver_id == 'rtl88x2bu' and suite in {
                'bookworm', 'daedalus', 'trixie', 'excalibur', 'sid'}:
            unavailable = 'not supported by this distribution release'

        if capability and capability in capabilities:
            skipped.append(driver_id)
            continue
        if unavailable:
            raise RuntimeError('{} is unavailable: {}'.format(item['label'], unavailable))
        item['package_spec'] = package
        requests.append(item)
    return requests, skipped


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _sandbox_path() -> str:
    installed = '/usr/lib/minios-kernel-manager/dkms-sandbox'
    if os.path.isfile(installed):
        return installed
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'libexec',
                                        'dkms-sandbox'))


def finalize_driver_manifest(source: str, destination: str, modules_dir: str) -> None:
    """Describe the decompressed modules actually published in SquashFS."""
    with open(source, 'r', encoding='utf-8') as stream:
        manifest = json.load(stream)
    for item in manifest['files']:
        relative = re.sub(r'(\.ko)\.(xz|gz|zst)$', r'\1', item['path'])
        if os.path.isabs(relative) or '..' in relative.split('/'):
            raise RuntimeError('Invalid DKMS module path: {}'.format(relative))
        path = os.path.join(modules_dir, relative)
        if not _regular_file(path):
            raise RuntimeError('Staged DKMS module is missing: {}'.format(relative))
        item['path'] = relative
        item['sha256'] = _sha256(path)
    with open(destination, 'w', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write('\n')


def terminate_active_build() -> None:
    """Stop and reap the active sandbox before its workspace is removed."""
    global _ACTIVE_PROCESS
    process = _ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def build_selected_drivers(temp_dir: str, kernel_version: str,
                           driver_ids: List[str]) -> dict:
    """Build selected drivers in an overlay chroot and merge verified modules."""
    if not driver_ids:
        return {'drivers': [], 'skipped': []}
    context = detect_kernel_context(temp_dir, kernel_version)
    requests, skipped = resolve_driver_selection(driver_ids, context)
    if not requests:
        return {'drivers': [], 'skipped': skipped}

    modules_path, modules_relative = _modules_path(temp_dir, kernel_version)
    workspace = os.path.join(temp_dir, '.dkms-build')
    os.mkdir(workspace, 0o700)
    command = [
        'unshare', '--mount', '--pid', '--fork', '--kill-child',
        _sandbox_path(), workspace, temp_dir, modules_relative,
        kernel_version,
    ] + [item['package_spec'] for item in requests]
    global _ACTIVE_PROCESS
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True)
    _ACTIVE_PROCESS = process
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
        process.stdout.close()
        process.wait()
        returncode = process.returncode
    finally:
        _ACTIVE_PROCESS = None
    if returncode != 0:
        raise RuntimeError('DKMS driver build failed (exit {})'.format(returncode))

    result_modules = os.path.join(workspace, 'result', 'modules')
    built_files = []
    if not os.path.isdir(result_modules):
        raise RuntimeError('DKMS build produced no module result directory')
    for parent, directories, filenames in os.walk(result_modules):
        directories[:] = [name for name in directories
                          if not os.path.islink(os.path.join(parent, name))]
        for filename in filenames:
            source = os.path.join(parent, filename)
            relative = os.path.relpath(source, result_modules)
            if (not _regular_file(source) or
                    not re.search(r'\.ko(?:\.(?:xz|zst|gz))?$', filename)):
                continue
            destination = os.path.join(modules_path, relative)
            if os.path.lexists(destination):
                raise RuntimeError('DKMS driver would replace kernel module {}'.format(relative))
            verify = subprocess.run(
                ['modinfo', '-F', 'vermagic', source], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True, check=False)
            if verify.returncode != 0 or not verify.stdout.startswith(kernel_version + ' '):
                raise RuntimeError('DKMS module has incompatible vermagic: {}'.format(relative))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
            built_files.append({'path': relative, 'sha256': _sha256(destination)})
    if not built_files:
        raise RuntimeError('Selected DKMS packages produced no modules for {}'.format(kernel_version))

    support_dir = os.path.join(temp_dir, DKMS_SUPPORT_DIR)
    runtime_source = os.path.join(workspace, 'result', 'runtime')
    runtime_target = os.path.join(support_dir, 'runtime')
    if os.path.isdir(runtime_source):
        shutil.copytree(runtime_source, runtime_target, symlinks=False)
    versions = {}
    versions_path = os.path.join(workspace, 'result', 'packages.tsv')
    if _regular_file(versions_path):
        with open(versions_path, 'r', encoding='utf-8') as stream:
            for line in stream:
                name, version = line.rstrip('\n').split('\t', 1)
                versions[name] = version
    manifest = {
        'format': 1,
        'kernel_version': kernel_version,
        'context': context,
        'skipped_in_tree': skipped,
        'drivers': [
            {'id': item['id'], 'package': item['package'],
             'package_version': versions.get(item['package'], '')}
            for item in requests
        ],
        'files': built_files,
    }
    os.makedirs(support_dir, exist_ok=True)
    with open(os.path.join(support_dir, 'manifest.json'), 'w', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return manifest
