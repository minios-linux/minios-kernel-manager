#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kernel package validation, extraction, and format-1 metadata production."""

import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple


SUPPORT_DIR = '.minios-kernel-dpkg'
ROLE_ORDER = {
    'image': 0,
    'binary': 1,
    'base': 2,
    'modules': 3,
    'modules-extra': 4,
    'dependency': 5,
}
CONTROL_FILES = ('preinst', 'postinst', 'prerm', 'postrm', 'triggers',
                 'conffiles', 'templates', 'config', 'shlibs', 'symbols')
DEPMOD_OUTPUTS = {
    'modules.alias', 'modules.alias.bin', 'modules.builtin.bin', 'modules.dep',
    'modules.dep.bin', 'modules.devname', 'modules.softdep', 'modules.symbols',
    'modules.symbols.bin', 'modules.weakdep',
}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, 'rb') as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _regular_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _parse_control(text: str) -> Dict[str, str]:
    fields = {}
    current = None
    for line in text.splitlines():
        if line[:1] in (' ', '\t') and current:
            fields[current] += '\n' + line
            continue
        if ': ' not in line:
            current = None
            continue
        current, value = line.split(': ', 1)
        fields[current] = value
    return fields


def _control_text(deb_path: str) -> str:
    result = subprocess.run(
        ['dpkg-deb', '-f', deb_path], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, universal_newlines=True, check=True)
    return result.stdout.rstrip() + '\n'


def _status_text(control_text: str) -> str:
    lines = control_text.rstrip().splitlines()
    output = []
    inserted = False
    for line in lines:
        output.append(line)
        if line.startswith('Package: ') and not inserted:
            output.append('Status: install ok installed')
            inserted = True
    if not inserted:
        raise RuntimeError('Package control data has no Package field')
    return '\n'.join(output) + '\n'


def _dependency_groups(fields: Dict[str, str]) -> List[List[str]]:
    groups = []
    value = ','.join(filter(None, (fields.get('Pre-Depends', ''),
                                   fields.get('Depends', ''))))
    for group in value.replace('\n', ' ').split(','):
        alternatives = []
        for alternative in group.split('|'):
            name = re.split(r'\s*\(', alternative.strip(), 1)[0].strip()
            name = re.sub(r':(?:any|native|[A-Za-z0-9_-]+)$', '', name)
            if name and not name.startswith('${'):
                alternatives.append(name)
        if alternatives:
            groups.append(alternatives)
    return groups


def _dependency_constraints(fields: Dict[str, str]) -> Dict[str, Tuple[str, str]]:
    constraints = {}
    value = ','.join(filter(None, (fields.get('Pre-Depends', ''),
                                   fields.get('Depends', ''))))
    for alternative in re.split(r'[,|]', value.replace('\n', ' ')):
        match = re.match(
            r'\s*([^\s(:]+)(?::[^\s(]+)?\s*\((<<|<=|=|>=|>>)\s*([^\s)]+)\)',
            alternative)
        if match:
            constraints[match.group(1)] = (match.group(2), match.group(3))
    return constraints


def _walk_payload(root: str) -> List[str]:
    paths = []
    for parent, directories, filenames in os.walk(root):
        directories.sort()
        filenames.sort()
        for name in filenames:
            path = os.path.join(parent, name)
            relative = os.path.relpath(path, root)
            paths.append('/' + relative.replace(os.sep, '/'))
        for name in directories:
            path = os.path.join(parent, name)
            if os.path.islink(path):
                relative = os.path.relpath(path, root)
                paths.append('/' + relative.replace(os.sep, '/'))
    return paths


def _validate_module_tree(package_root: str, paths: List[str]) -> List[str]:
    validated = []
    for path in paths:
        module_match = re.match(
            r'^/((?:usr/)?lib/modules/([^/]+))/(.*)$', path)
        if not module_match:
            validated.append(path)
            continue
        relative = module_match.group(3)
        source = os.path.join(package_root, path.lstrip('/'))
        if relative in ('build', 'source') and os.path.islink(source):
            os.unlink(source)
            continue
        if os.path.islink(source):
            target = os.readlink(source)
            if os.path.isabs(target):
                raise RuntimeError(
                    'Kernel module payload has an absolute symlink: {}'.format(path))
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            boundary = '/' + module_match.group(1) + '/'
            if not resolved.startswith(boundary):
                raise RuntimeError(
                    'Kernel module payload symlink escapes its version tree: {}'.format(
                        path))
        elif (_kernel_path_kind(path) == 'module-index' and
              os.path.basename(path) not in DEPMOD_OUTPUTS):
            raise RuntimeError(
                'Kernel module tree has an unexpected unowned file: {}'.format(path))
        validated.append(path)
    return validated


def _kernel_path_kind(path: str) -> Optional[str]:
    if re.match(r'^/boot/vmlinuz-[^/]+$', path):
        return 'vmlinuz'
    if re.match(r'^/boot/config-[^/]+$', path):
        return 'config'
    if re.match(r'^/boot/System\.map-[^/]+$', path):
        return 'base'
    if re.match(r'^/(?:usr/)?lib/modules/[^/]+/.+\.ko(?:\.(?:xz|zst|gz))?$', path):
        return 'module'
    if re.match(r'^/(?:usr/)?lib/modules/[^/]+/modules\.(?:order|builtin(?:\..*)?)$', path):
        return 'base'
    if re.match(r'^/(?:usr/)?lib/modules/[^/]+/', path):
        return 'module-index'
    return None


def _meaningful_payload(package: dict) -> List[str]:
    ancillary_prefixes = (
        '/usr/share/bug/',
        '/usr/share/doc/',
        '/usr/share/lintian/',
    )
    return [path for path in package['paths']
            if not path.startswith(ancillary_prefixes)]


def _path_kernel_version(path: str) -> Optional[str]:
    match = re.match(r'^/boot/(?:vmlinuz|config|System\.map)-(.+)$', path)
    if match:
        return match.group(1)
    match = re.match(r'^/(?:usr/)?lib/modules/([^/]+)/', path)
    return match.group(1) if match else None


def _depends_on(graph: Dict[str, List[str]], start: str, target: str) -> bool:
    pending = list(graph.get(start, []))
    seen = set()
    while pending:
        package = pending.pop()
        if package == target:
            return True
        if package in seen:
            continue
        seen.add(package)
        pending.extend(graph.get(package, []))
    return False


def _classify_packages(packages: List[dict]) -> str:
    by_name = {package['name']: package for package in packages}
    if len(by_name) != len(packages):
        raise RuntimeError('The package set contains duplicate binary package names')

    graph = {}
    for package in packages:
        dependencies = []
        for alternatives in package['dependency_groups']:
            present = [name for name in alternatives if name in by_name]
            if len(present) > 1:
                raise RuntimeError(
                    'Dependency alternatives are ambiguous in the selected set: {}'.format(
                        ', '.join(present)))
            if present:
                dependencies.append(present[0])
        graph[package['name']] = dependencies

    versions = set()
    for package in packages:
        kinds = set()
        kernel_paths = []
        for path in package['paths']:
            kind = _kernel_path_kind(path)
            if kind:
                kinds.add(kind)
                kernel_paths.append(path)
                version = _path_kernel_version(path)
                if version:
                    versions.add(version)
        package['kinds'] = kinds
        package['kernel_paths'] = kernel_paths
    if len(versions) != 1:
        raise RuntimeError(
            'Package set must contain exactly one kernel version; found {}'.format(
                ', '.join(sorted(versions)) if versions else 'none'))
    kernel_version = next(iter(versions))

    vmlinuz_owners = [p for p in packages if 'vmlinuz' in p['kinds']]
    config_owners = [p for p in packages if 'config' in p['kinds']]
    system_map_owners = [p for p in packages if any(
        path == '/boot/System.map-{}'.format(kernel_version)
        for path in p['kernel_paths'])]
    module_owners = [p for p in packages if 'module' in p['kinds']]
    if (len(vmlinuz_owners) != 1 or len(config_owners) != 1 or
            len(system_map_owners) != 1 or not module_owners):
        raise RuntimeError(
            'Kernel set requires one vmlinuz, one config, one System.map, '
            'and loadable kernel modules')

    complete = [p for p in packages
                if {'vmlinuz', 'config', 'module'}.issubset(p['kinds'])]
    core_owners = set(p['name'] for p in vmlinuz_owners + config_owners + module_owners)
    if complete:
        if len(complete) != 1 or core_owners != {complete[0]['name']}:
            raise RuntimeError('Kernel set mixes monolithic and split package ownership')
        complete[0]['role'] = 'image'
    else:
        explicit_extra = [
            package for package in module_owners
            if package['name'].startswith('linux-modules-extra-')]
        for package in module_owners:
            other_module_owners = [other for other in module_owners
                                   if other is not package]
            is_extra = package in explicit_extra
            if not explicit_extra:
                is_extra = any(
                    _depends_on(graph, package['name'], other['name'])
                    for other in other_module_owners)
            package['role'] = 'modules-extra' if is_extra else 'modules'
        if sum(1 for package in module_owners
               if package['role'] == 'modules') != 1:
            raise RuntimeError('Split kernel has no unambiguous primary modules package')

        boot_owner = vmlinuz_owners[0]
        has_lifecycle = bool(boot_owner['control_files'] &
                             {'preinst', 'postinst', 'prerm', 'postrm'})
        if 'config' in boot_owner['kinds'] or has_lifecycle:
            boot_owner['role'] = 'image'
        else:
            boot_owner['role'] = 'binary'
        config_owner = config_owners[0]
        if not config_owner.get('role'):
            config_owner['role'] = 'base'

        if not any(p.get('role') == 'image' for p in packages):
            payload_roles = [p for p in packages if p.get('role') in
                             ('binary', 'base', 'modules', 'modules-extra')]
            coordinators = []
            for package in packages:
                lifecycle = package['control_files'] & {
                    'preinst', 'postinst', 'prerm', 'postrm'}
                if (not package.get('role') and lifecycle and
                        not _meaningful_payload(package) and
                        all(_depends_on(graph, package['name'], owner['name'])
                            for owner in payload_roles)):
                    coordinators.append(package)
            if len(coordinators) != 1:
                raise RuntimeError('Split kernel requires one lifecycle image coordinator')
            coordinators[0]['role'] = 'image'

        if len([p for p in packages if p.get('role') == 'image']) != 1:
            raise RuntimeError('Split kernel has an ambiguous image package')

    for package in packages:
        if package.get('role'):
            continue
        package['role'] = 'dependency'

    role_prefixes = {
        'image': 'linux-image-',
        'binary': 'linux-binary-',
        'base': 'linux-base-',
        'modules': 'linux-modules-',
        'modules-extra': 'linux-modules-extra-',
    }
    for package in packages:
        prefix = role_prefixes.get(package['role'])
        if prefix and not package['name'].startswith(prefix):
            raise RuntimeError(
                'Package {} is incompatible with its observed {} role'.format(
                    package['name'], package['role']))

    image = next(p for p in packages if p.get('role') == 'image')
    split_packages = [p for p in packages if p.get('role') in
                      ('binary', 'base', 'modules', 'modules-extra')]
    if split_packages:
        actual_roles = set(package['role'] for package in split_packages)
        if ({'binary', 'base'} & actual_roles and
                not {'binary', 'base', 'modules'}.issubset(actual_roles)):
            raise RuntimeError('Debian split kernel package closure is incomplete')
        for package in split_packages:
            if not _depends_on(graph, image['name'], package['name']):
                raise RuntimeError(
                    'Kernel image does not depend on its {} package'.format(
                        package['role']))
    return kernel_version


def _userspace_identity() -> dict:
    values = {}
    try:
        with open('/etc/os-release', 'r', encoding='utf-8') as release:
            for line in release:
                if '=' in line:
                    key, value = line.rstrip().split('=', 1)
                    values[key] = value.strip('"')
    except OSError:
        pass
    result = subprocess.run(
        ['dpkg', '--print-architecture'], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, universal_newlines=True, check=False)
    system_id = values.get('ID', '').lower()
    family = 'ubuntu' if system_id == 'ubuntu' else 'debian'
    identity = {
        'family': family,
        'suite': values.get('VERSION_CODENAME', ''),
        'dpkg_architecture': result.stdout.strip(),
    }
    if not all(identity.values()):
        raise RuntimeError('Userspace distribution and dpkg identity are required')
    return identity


def _canonical_instance(name: str, architecture: str,
                        native_architecture: str = None) -> str:
    if not name or not architecture:
        raise RuntimeError('Package name and architecture are required')
    if native_architecture and architecture in ('all', native_architecture):
        return name
    return '{}:{}'.format(name, architecture)


def _copy_control_files(package: dict, info_dir: str, instance: str) -> None:
    for name in CONTROL_FILES:
        source = os.path.join(package['control_dir'], name)
        if _regular_file(source):
            shutil.copy2(source, os.path.join(info_dir, '{}.{}'.format(instance, name)))


def _verify_package_constraints(packages: List[dict]) -> None:
    by_name = {package['name']: package for package in packages}
    for package in packages:
        if package.get('role') == 'dependency':
            continue
        for alternatives in package['dependency_groups']:
            if not any(name in by_name for name in alternatives):
                continue
            satisfied = False
            for name in alternatives:
                dependency = by_name.get(name)
                if not dependency:
                    continue
                constraint = package['dependency_constraints'].get(name)
                if not constraint:
                    satisfied = True
                    break
                result = subprocess.run(
                    ['dpkg', '--compare-versions', dependency['version'],
                     constraint[0], constraint[1]], check=False)
                if result.returncode == 0:
                    satisfied = True
                    break
            if not satisfied:
                raise RuntimeError(
                    'Resolved package dependency is not satisfied for {}'.format(
                        package['name']))


def _validate_kernel_architecture(packages: List[dict], kernel_version: str,
                                  userspace_architecture: str,
                                  kernel_architecture: str) -> None:
    if userspace_architecture == 'amd64' and kernel_architecture == 'i386':
        raise RuntimeError('An i386 kernel cannot run amd64 userspace')
    expected = '/boot/config-{}'.format(kernel_version)
    config_path = None
    for package in packages:
        if expected in package.get('kernel_paths', []):
            candidate = os.path.join(package['root'], expected.lstrip('/'))
            if _regular_file(candidate):
                config_path = candidate
                break
    if not config_path:
        raise RuntimeError('Kernel package closure has no matching config')
    with open(config_path, 'r', encoding='utf-8', errors='replace') as config_file:
        symbols = set(line.strip() for line in config_file if line.endswith('=y\n'))
    if 'CONFIG_EFI_STUB=y' not in symbols:
        raise RuntimeError('Selected kernel lacks CONFIG_EFI_STUB=y')
    if userspace_architecture == 'i386' and kernel_architecture == 'amd64':
        required = {'CONFIG_BINFMT_ELF=y', 'CONFIG_IA32_EMULATION=y',
                    'CONFIG_EFI_MIXED=y'}
        missing = sorted(required - symbols)
        if missing:
            raise RuntimeError(
                'Selected mixed-mode kernel lacks {}'.format(', '.join(missing)))


def _write_support_data(packages: List[dict], temp_dir: str,
                        kernel_version: str) -> None:
    support = os.path.join(temp_dir, SUPPORT_DIR)
    if os.path.exists(support):
        shutil.rmtree(support)
    status_dir = os.path.join(support, 'status.d')
    info_dir = os.path.join(support, 'info')
    payload_dir = os.path.join(support, 'payload.d')
    for directory in (status_dir, info_dir, payload_dir):
        os.makedirs(directory)

    userspace = _userspace_identity()
    manifest_packages = []
    source_payload = {}
    for package in packages:
        role = package['role']
        if role == 'dependency':
            continue
        instance = _canonical_instance(
            package['name'], package['architecture'],
            userspace['dpkg_architecture'])
        entry = {
            'role': role,
            'name': package['name'],
            'version': package['version'],
            'architecture': package['architecture'],
            'dpkg_instance': instance,
            'source_package': package['source_package'],
            'source_archive_sha256': package.get('archive_sha256'),
            'registration': 'synthetic-installed',
            'apt_mark': 'manual' if role == 'image' else 'auto',
            'hold': role == 'image',
            'status': 'status.d/{}.status'.format(instance),
            'info_prefix': 'info/{}.'.format(instance),
            'payload_manifest': 'payload.d/{}.json'.format(instance),
        }
        with open(os.path.join(status_dir, '{}.status'.format(instance)),
                  'w', encoding='utf-8') as status_file:
            status_file.write(_status_text(package['control_text']))
        _copy_control_files(package, info_dir, instance)
        source_payload[instance] = [
            path for path in package['kernel_paths']
            if _kernel_path_kind(path) != 'module-index']
        with open(os.path.join(info_dir, '{}.list'.format(instance)),
                  'w', encoding='utf-8') as list_file:
            list_file.write('/.\n')
        manifest_packages.append(entry)

    package_architectures = sorted(set(
        package['architecture'] for package in packages
        if package['role'] != 'dependency' and package['architecture'] != 'all'))
    if len(package_architectures) != 1:
        raise RuntimeError('Kernel packages must use one package architecture')
    _validate_kernel_architecture(
        packages, kernel_version, userspace['dpkg_architecture'],
        package_architectures[0])
    manifest = {
        'format': 1,
        'install_policy': 'register-materialized-payload',
        'update_policy': 'frozen',
        'userspace': userspace,
        'kernel': {
            'distribution': userspace['suite'],
            'version': kernel_version,
            'package_architecture': package_architectures[0],
        },
        'repositories': [],
        'packages': sorted(manifest_packages,
                           key=lambda item: (ROLE_ORDER[item['role']],
                                             item['dpkg_instance'])),
    }
    with open(os.path.join(support, 'manifest.json'), 'w',
              encoding='utf-8') as manifest_file:
        json.dump(manifest, manifest_file, indent=2, sort_keys=True)
        manifest_file.write('\n')
    with open(os.path.join(support, '.source-payload.json'), 'w',
              encoding='utf-8') as source_file:
        json.dump(source_payload, source_file, sort_keys=True)


def analyze_and_extract_packages(deb_files: List[str], temp_dir: str) -> str:
    """Validate archives, extract one coherent kernel, and write format 1."""
    if not deb_files:
        raise RuntimeError('No package archives were supplied')
    analysis_root = tempfile.mkdtemp(prefix='.kernel-control-', dir=temp_dir)
    packages = []
    seen_instances = set()
    try:
        for index, deb_path in enumerate(deb_files):
            if not _regular_file(deb_path):
                raise RuntimeError(
                    'Package is not a regular non-symlink file: {}'.format(deb_path))
            package_root = os.path.join(analysis_root, str(index), 'payload')
            control_dir = os.path.join(analysis_root, str(index), 'control')
            os.makedirs(package_root)
            os.makedirs(control_dir)
            control_text = _control_text(deb_path)
            fields = _parse_control(control_text)
            name = fields.get('Package', '')
            version = fields.get('Version', '')
            architecture = fields.get('Architecture', '')
            instance = _canonical_instance(name, architecture)
            if not version or instance in seen_instances:
                raise RuntimeError('Package identity is missing or duplicated: {}'.format(instance))
            seen_instances.add(instance)
            subprocess.run(['dpkg-deb', '-x', deb_path, package_root], check=True)
            subprocess.run(['dpkg-deb', '-e', deb_path, control_dir], check=True)
            source_package = fields.get('Source', name).split()[0]
            paths = _validate_module_tree(package_root, _walk_payload(package_root))
            package = {
                'name': name,
                'version': version,
                'architecture': architecture,
                'source_package': source_package,
                'archive_sha256': _sha256(deb_path),
                'control_text': control_text,
                'control_dir': control_dir,
                'control_files': set(os.listdir(control_dir)),
                'dependency_groups': _dependency_groups(fields),
                'dependency_constraints': _dependency_constraints(fields),
                'paths': paths,
                'root': package_root,
                'deb_path': deb_path,
            }
            packages.append(package)

        kernel_version = _classify_packages(packages)
        _verify_package_constraints(packages)
        _write_support_data(packages, temp_dir, kernel_version)

        # Merge only after the complete archive set has passed role/coherence
        # validation. Maintainer scripts are extracted as data and never run.
        for package in packages:
            if package['role'] != 'dependency' and package.get('deb_path'):
                subprocess.run(
                    ['dpkg-deb', '-x', package['deb_path'], temp_dir], check=True)
        return kernel_version
    finally:
        shutil.rmtree(analysis_root, ignore_errors=True)


def finalize_payload_metadata(temp_dir: str, staged_root: str,
                              modules_base: str, kernel_version: str) -> None:
    """Generate final transformed ownership records after staging and depmod."""
    support = os.path.join(temp_dir, SUPPORT_DIR)
    source_path = os.path.join(support, '.source-payload.json')
    manifest_path = os.path.join(support, 'manifest.json')
    with open(source_path, 'r', encoding='utf-8') as source_file:
        source_payload = json.load(source_file)
    with open(manifest_path, 'r', encoding='utf-8') as manifest_file:
        manifest = json.load(manifest_file)
    if manifest.get('kernel', {}).get('version') != kernel_version:
        raise RuntimeError('Staged kernel version does not match format-1 metadata')

    for package in manifest['packages']:
        instance = package['dpkg_instance']
        if package['registration'] != 'synthetic-installed':
            continue
        records = []
        final_paths = []
        for original in source_payload.get(instance, []):
            transformed = original
            if re.match(r'^/(?:usr/)?lib/modules/', original):
                suffix = re.sub(r'^/(?:usr/)?lib/modules/', '', original)
                transformed = '/' + modules_base.strip('/') + '/' + suffix
                if transformed.endswith('.ko.xz') or transformed.endswith('.ko.zst'):
                    transformed = transformed.rsplit('.', 1)[0]
                final_file = os.path.join(staged_root, transformed.lstrip('/'))
            elif original.startswith('/boot/vmlinuz-'):
                final_file = os.path.join(temp_dir, original.lstrip('/'))
            elif original in (
                    '/boot/config-{}'.format(kernel_version),
                    '/boot/System.map-{}'.format(kernel_version)):
                source = os.path.join(temp_dir, original.lstrip('/'))
                final_file = os.path.join(staged_root, original.lstrip('/'))
                if not _regular_file(source):
                    raise RuntimeError(
                        'Final package payload is missing: {}'.format(transformed))
                if os.path.lexists(final_file):
                    if (not _regular_file(final_file) or
                            _sha256(final_file) != _sha256(source)):
                        raise RuntimeError(
                            'Kernel boot metadata has duplicate ownership: {}'.format(
                                transformed))
                else:
                    os.makedirs(os.path.dirname(final_file), exist_ok=True)
                    shutil.copy2(source, final_file)
            else:
                final_file = os.path.join(staged_root, original.lstrip('/'))
                if not os.path.lexists(final_file):
                    final_file = os.path.join(temp_dir, original.lstrip('/'))
            if not os.path.lexists(final_file):
                raise RuntimeError('Final package payload is missing: {}'.format(transformed))
            final_paths.append(transformed)
            if os.path.isfile(final_file) and not os.path.islink(final_file):
                records.append({
                    'path': transformed,
                    'sha256': _sha256(final_file),
                    'type': 'file',
                })
            elif os.path.islink(final_file):
                records.append({
                    'path': transformed,
                    'type': 'symlink',
                    'target': os.readlink(final_file),
                })
        list_path = os.path.join(support, 'info', '{}.list'.format(instance))
        with open(list_path, 'w', encoding='utf-8') as list_file:
            list_file.write('/.\n')
            if final_paths:
                list_file.write('\n'.join(sorted(set(final_paths))) + '\n')
        md5_path = os.path.join(support, 'info', '{}.md5sums'.format(instance))
        with open(md5_path, 'w', encoding='utf-8') as md5_file:
            for record in sorted(records, key=lambda item: item['path']):
                if record['type'] != 'file':
                    continue
                path = record['path']
                if path.startswith('/boot/vmlinuz-'):
                    materialized = os.path.join(temp_dir, path.lstrip('/'))
                else:
                    materialized = os.path.join(staged_root, path.lstrip('/'))
                md5_file.write('{}  {}\n'.format(
                    _md5(materialized), path.lstrip('/')))
        if package.get('payload_manifest'):
            payload_path = os.path.join(support, package['payload_manifest'])
            with open(payload_path, 'w', encoding='utf-8') as payload_file:
                json.dump({'format': 1, 'dpkg_instance': instance,
                           'files': sorted(records, key=lambda item: item['path'])},
                          payload_file, indent=2, sort_keys=True)
                payload_file.write('\n')
    os.unlink(source_path)


def validate_embedded_support_tree(support_root: str, kernel_version: str,
                                   allow_legacy: bool = False) -> dict:
    """Validate canonical support files before publication or activation."""
    manifest_path = os.path.join(support_root, 'manifest.json')
    if not _regular_file(manifest_path):
        raise RuntimeError('Kernel bundle has no regular format-1 manifest')
    with open(manifest_path, 'r', encoding='utf-8') as manifest_file:
        manifest = json.load(manifest_file)
    legacy_fields = {'format', 'kernel_version', 'packages'}
    if allow_legacy and set(manifest) == legacy_fields:
        if (manifest.get('format') != 1 or
                manifest.get('kernel_version') != kernel_version or
                not isinstance(manifest.get('packages'), list) or
                not manifest['packages']):
            raise RuntimeError('Legacy kernel manifest identity is invalid')
        for package in manifest['packages']:
            if (not isinstance(package, dict) or
                    set(package) != {'name', 'version', 'architecture'} or
                    not all(isinstance(package.get(field), str) and
                            package.get(field)
                            for field in ('name', 'version', 'architecture'))):
                raise RuntimeError('Legacy kernel package record is invalid')
            name = package['name']
            if not _regular_file(os.path.join(
                    support_root, 'status.d', name + '.status')):
                raise RuntimeError('Legacy kernel status record is missing')
            if not _regular_file(os.path.join(
                    support_root, 'info', name + '.list')):
                raise RuntimeError('Legacy kernel file list is missing')
        return manifest
    required = ('format', 'install_policy', 'update_policy', 'userspace',
                'kernel', 'repositories', 'packages')
    if set(manifest) != set(required):
        raise RuntimeError('Kernel manifest is not canonical format 1')
    if (manifest['format'] != 1 or
            manifest['install_policy'] != 'register-materialized-payload' or
            manifest['kernel'].get('version') != kernel_version):
        raise RuntimeError('Kernel manifest identity does not match the bundle')
    if manifest.get('update_policy') not in ('track', 'frozen'):
        raise RuntimeError('Unknown kernel update policy')
    if set(manifest['userspace']) != {
            'family', 'suite', 'dpkg_architecture'}:
        raise RuntimeError('Kernel manifest userspace identity is incomplete')
    if set(manifest['kernel']) != {
            'distribution', 'version', 'package_architecture'}:
        raise RuntimeError('Kernel manifest kernel identity is incomplete')
    tracking = [p for p in manifest['packages']
                if p.get('role') == 'tracking-meta']
    if manifest['update_policy'] == 'track':
        if len(tracking) != 1 or not manifest['repositories']:
            raise RuntimeError(
                'Tracked kernel requires one meta and a signed repository')
    elif tracking or manifest['repositories']:
        raise RuntimeError('Frozen kernel cannot claim tracking metadata')
    if len([p for p in manifest['packages'] if p.get('role') == 'image']) != 1:
        raise RuntimeError('Kernel manifest requires one image role')
    roles = set(package.get('role') for package in manifest['packages'])
    split_roles = {'binary', 'base', 'modules'}
    if {'binary', 'base'}.intersection(roles) and not split_roles.issubset(roles):
        raise RuntimeError('Kernel manifest has an incomplete Debian split layout')
    for repository in manifest['repositories']:
        release = repository.get('release_identity', {})
        keyring = repository.get('keyring', {})
        if (not re.match(r'^[0-9a-f]{64}$',
                        release.get('inrelease_sha256', '')) or
                not keyring.get('fingerprints')):
            raise RuntimeError('Kernel repository identity is incomplete')
        keyring_path = keyring.get('path', '')
        keyring_file = os.path.join(support_root, keyring_path)
        if (keyring_path.startswith('/') or '..' in keyring_path.split('/') or
                not _regular_file(keyring_file) or
                _sha256(keyring_file) != keyring.get('sha256')):
            raise RuntimeError(
                'Kernel repository keyring identity does not match')
    seen = set()
    allowed_roles = set(ROLE_ORDER) | {'tracking-meta', 'base-meta'}
    for package in manifest['packages']:
        required_package = ('role', 'name', 'version', 'architecture',
                            'dpkg_instance', 'source_package',
                            'source_archive_sha256', 'registration', 'apt_mark',
                            'hold')
        if any(field not in package for field in required_package):
            raise RuntimeError('Kernel package record is incomplete')
        if package['role'] not in allowed_roles:
            raise RuntimeError('Kernel package role is invalid')
        if not re.match(r'^[0-9a-f]{64}$',
                        package.get('source_archive_sha256') or ''):
            raise RuntimeError('Kernel source archive hash is invalid')
        instance = package.get('dpkg_instance')
        expected = _canonical_instance(
            package.get('name'), package.get('architecture'),
            manifest['userspace'].get('dpkg_architecture'))
        if instance != expected or instance in seen:
            raise RuntimeError('Kernel manifest has a non-canonical package instance')
        seen.add(instance)
        if package.get('registration') == 'synthetic-installed':
            if package['role'] == 'dependency':
                raise RuntimeError('Dependency package cannot be synthetically installed')
            expected_fields = set(required_package) | {'status', 'info_prefix'}
            if package.get('role') in ('image', 'binary', 'base', 'modules',
                                       'modules-extra'):
                expected_fields.add('payload_manifest')
            if set(package) != expected_fields:
                raise RuntimeError('Synthetic kernel package record is not canonical')
            for field in ('status', 'info_prefix'):
                relative = package.get(field, '')
                if not relative or relative.startswith('/') or '..' in relative.split('/'):
                    raise RuntimeError('Kernel package metadata path is not confined')
            if not _regular_file(os.path.join(support_root, package['status'])):
                raise RuntimeError('Kernel package status record is missing')
            if not _regular_file(os.path.join(
                    support_root, package['info_prefix'] + 'list')):
                raise RuntimeError('Kernel package file list is missing')
            if not _regular_file(os.path.join(
                    support_root, package['info_prefix'] + 'md5sums')):
                raise RuntimeError('Kernel package transformed checksums are missing')
            payload = package.get('payload_manifest')
            if payload and not _regular_file(os.path.join(support_root, payload)):
                raise RuntimeError('Kernel package payload manifest is missing')
        elif package.get('registration') != 'verify-installed':
            raise RuntimeError('Kernel package registration mode is invalid')
        elif set(package) != set(required_package):
            raise RuntimeError('Verified dependency package record is not canonical')
        elif (package['role'] != 'dependency' or
              package.get('apt_mark') != 'unchanged'):
            raise RuntimeError('Only target dependencies may use verify-installed')
        if manifest['update_policy'] == 'track':
            if package['role'] == 'tracking-meta':
                if package.get('apt_mark') != 'manual' or package.get('hold'):
                    raise RuntimeError('Tracking package policy is invalid')
            elif package['role'] != 'dependency':
                if package.get('apt_mark') != 'auto' or package.get('hold'):
                    raise RuntimeError('Tracked kernel package policy is invalid')
        elif package['role'] == 'image':
            if package.get('apt_mark') != 'manual' or not package.get('hold'):
                raise RuntimeError('Frozen image package policy is invalid')
        elif package['role'] != 'dependency':
            if package.get('apt_mark') != 'auto' or package.get('hold'):
                raise RuntimeError('Frozen subordinate package policy is invalid')
    if glob.glob(os.path.join(support_root, '**', '*.deb'), recursive=True):
        raise RuntimeError('Kernel support data must not contain source archives')
    return manifest
