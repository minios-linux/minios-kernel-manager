#!/usr/bin/env python3

import hashlib
import json
import lzma
import os
import shutil
import subprocess

import pytest


def build_deb(tmp_path, name, payload, depends='', scripts=(), symlinks=()):
    root = tmp_path / (name + '-root')
    control_dir = root / 'DEBIAN'
    control_dir.mkdir(parents=True)
    control = [
        'Package: {}'.format(name),
        'Version: 1.0-1',
        'Architecture: amd64',
        'Maintainer: Fixture <fixture@example.invalid>',
        'Description: kernel fixture',
    ]
    if depends:
        control.append('Depends: ' + depends)
    (control_dir / 'control').write_text(
        '\n'.join(control) + '\n', encoding='utf-8')
    for script in scripts:
        path = control_dir / script
        path.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        path.chmod(0o755)
    for relative, content in payload.items():
        if relative.startswith('boot/config-') and b'CONFIG_EFI_STUB=y' not in content:
            content += b'\nCONFIG_EFI_STUB=y\n'
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for relative, target in symlinks:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    deb = tmp_path / (name + '_1.0-1_amd64.deb')
    subprocess.run(['dpkg-deb', '--build', str(root), str(deb)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return str(deb)


def split_fixture(tmp_path, compressed=False):
    version = '6.12.0-fixture'
    image = 'linux-image-' + version + '-amd64'
    binary = 'linux-binary-' + version + '-amd64'
    base = 'linux-base-' + version + '-amd64'
    modules = 'linux-modules-' + version + '-amd64'
    module = lzma.compress(b'fixture module') if compressed else b'fixture module'
    extension = '.ko.xz' if compressed else '.ko'
    packages = [
        build_deb(tmp_path, image, {
            'usr/share/lintian/overrides/' + image: b'coordinator metadata'},
                  '{}, {}, {}'.format(binary, base, modules),
                  scripts=('postinst', 'prerm')),
        build_deb(tmp_path, binary, {
            'boot/vmlinuz-' + version: b'fixture kernel'}),
        build_deb(tmp_path, base, {
            'boot/config-' + version: b'CONFIG_SQUASHFS=y\n',
            'boot/System.map-' + version: b'ffffffff T fixture_symbol\n',
            'lib/modules/{}/modules.order'.format(version):
                ('kernel/driver{}\n'.format(extension)).encode('ascii')}),
        build_deb(tmp_path, modules, {
            'lib/modules/{}/kernel/driver{}'.format(version, extension): module}),
    ]
    return version, packages


def test_rejects_incomplete_and_hybrid_split_sets(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages

    version, packages = split_fixture(tmp_path)
    incomplete = tmp_path / 'incomplete'
    incomplete.mkdir()
    with pytest.raises(RuntimeError, match='modules'):
        analyze_and_extract_packages(packages[:-1], str(incomplete))

    monolithic = build_deb(tmp_path, 'linux-image-' + version + '-monolithic', {
        'boot/vmlinuz-' + version: b'kernel',
        'boot/config-' + version: b'config',
        'boot/System.map-' + version: b'map',
        'lib/modules/{}/kernel/core.ko'.format(version): b'core',
    }, scripts=('postinst',))
    extra = build_deb(tmp_path, 'linux-modules-extra-' + version + '-amd64', {
        'lib/modules/{}/kernel/extra.ko'.format(version): b'extra'})
    hybrid = tmp_path / 'hybrid'
    hybrid.mkdir()
    with pytest.raises(RuntimeError, match='mixes monolithic and split'):
        analyze_and_extract_packages([monolithic, extra], str(hybrid))


def test_accepts_monolithic_layout_from_observed_payload(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages

    version = '6.1.0-monolithic'
    archive = build_deb(tmp_path, 'linux-image-' + version + '-amd64', {
        'boot/vmlinuz-' + version: b'kernel',
        'boot/config-' + version: b'config',
        'boot/System.map-' + version: b'map',
        'lib/modules/{}/kernel/core.ko'.format(version): b'core',
    }, scripts=('postinst', 'prerm'))
    extracted = tmp_path / 'monolithic-extracted'
    extracted.mkdir()
    assert analyze_and_extract_packages([archive], str(extracted)) == version
    manifest = json.loads((
        extracted / '.minios-kernel-dpkg/manifest.json').read_text(
            encoding='utf-8'))
    assert [(package['name'], package['role'])
            for package in manifest['packages']] == [
                 ('linux-image-' + version + '-amd64', 'image')]
    assert manifest['format'] == 1
    assert manifest['update_policy'] == 'frozen'
    assert manifest['repositories'] == []
    package = manifest['packages'][0]
    assert package['apt_mark'] == 'manual'
    assert package['hold'] is True
    assert manifest['kernel']['distribution']
    with open(archive, 'rb') as archive_file:
        assert package['source_archive_sha256'] == hashlib.sha256(
            archive_file.read()).hexdigest()
    support = extracted / '.minios-kernel-dpkg'
    assert (support / package['status']).is_file()
    assert (support / (package['info_prefix'] + 'list')).is_file()
    assert package['payload_manifest'] == (
        'payload.d/linux-image-{}-amd64.json'.format(version))


def test_transient_non_kernel_dependency_is_not_manifested_or_extracted(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages

    version = '6.1.0-transient'
    image = 'linux-image-' + version + '-amd64'
    dependency = 'kernel-helper'
    archives = [
        build_deb(tmp_path, image, {
            'boot/vmlinuz-' + version: b'kernel',
            'boot/config-' + version: b'config',
            'boot/System.map-' + version: b'map',
            'lib/modules/{}/kernel/core.ko'.format(version): b'module',
        }, dependency, scripts=('preinst', 'postinst', 'prerm', 'postrm')),
        build_deb(tmp_path, dependency, {
            'usr/bin/kernel-helper': b'userspace helper'}),
    ]
    extraction = tmp_path / 'monolithic-with-dependency'
    extraction.mkdir()
    assert analyze_and_extract_packages(archives, str(extraction)) == version

    support = extraction / '.minios-kernel-dpkg'
    manifest = json.loads((support / 'manifest.json').read_text(encoding='utf-8'))
    packages = {package['name']: package for package in manifest['packages']}
    assert manifest['update_policy'] == 'frozen'
    assert manifest['repositories'] == []
    assert set(packages) == {image}
    assert packages[image]['role'] == 'image'
    assert dependency not in packages
    for package in packages.values():
        assert len(package['source_archive_sha256']) == 64
    for script in ('preinst', 'postinst', 'prerm', 'postrm'):
        assert (support / (packages[image]['info_prefix'] + script)).is_file()
    assert not (extraction / 'usr/bin/kernel-helper').exists()
    assert not list(support.rglob('*.deb'))


def test_split_modules_extra_role_uses_identity_with_image_dependency(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages

    version = '6.12.0-extra'
    image = 'linux-image-' + version + '-amd64'
    binary = 'linux-binary-' + version + '-amd64'
    base = 'linux-base-' + version + '-amd64'
    modules = 'linux-modules-' + version + '-amd64'
    modules_extra = 'linux-modules-extra-' + version + '-amd64'
    archives = [
        build_deb(tmp_path, image, {},
                  '{}, {}, {}, {}'.format(
                      binary, base, modules, modules_extra),
                  scripts=('postinst',)),
        build_deb(tmp_path, binary, {
            'boot/vmlinuz-' + version: b'kernel'}),
        build_deb(tmp_path, base, {
            'boot/config-' + version: b'config',
            'boot/System.map-' + version: b'map'}),
        build_deb(tmp_path, modules, {
            'lib/modules/{}/kernel/core.ko'.format(version): b'core'}),
        build_deb(tmp_path, modules_extra, {
            'lib/modules/{}/kernel/optional.ko'.format(version): b'optional'}),
    ]
    extracted = tmp_path / 'extra-extracted'
    extracted.mkdir()
    analyze_and_extract_packages(archives, str(extracted))
    manifest = json.loads((
        extracted / '.minios-kernel-dpkg/manifest.json').read_text(
            encoding='utf-8'))
    roles = {package['name']: package['role'] for package in manifest['packages']}
    assert roles == {
        image: 'image',
        binary: 'binary',
        base: 'base',
        modules: 'modules',
        modules_extra: 'modules-extra',
    }
    assert manifest['update_policy'] == 'frozen'
    assert manifest['repositories'] == []
    support = extracted / '.minios-kernel-dpkg'
    for package in manifest['packages']:
        if package['role'] == 'image':
            assert package['apt_mark'] == 'manual'
            assert package['hold'] is True
        else:
            assert package['apt_mark'] == 'auto'
            assert package['hold'] is False
        assert len(package['source_archive_sha256']) == 64
        assert (support / package['status']).is_file()
        assert (support / (package['info_prefix'] + 'list')).is_file()
        assert package['payload_manifest'].startswith('payload.d/')


def test_final_payload_hashes_follow_normalization_decompression_and_depmod(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages, finalize_payload_metadata

    version, packages = split_fixture(tmp_path, compressed=True)
    extraction = tmp_path / 'payload-extracted'
    extraction.mkdir()
    assert analyze_and_extract_packages(packages, str(extraction)) == version

    staged = tmp_path / 'staged'
    module_dir = staged / 'usr/lib/modules' / version / 'kernel'
    module_dir.mkdir(parents=True)
    (module_dir / 'driver.ko').write_bytes(b'fixture module')
    index_dir = staged / 'usr/lib/modules' / version
    (index_dir / 'modules.order').write_text(
        'kernel/driver.ko\n', encoding='utf-8')
    (index_dir / 'modules.dep').write_text(
        'kernel/driver.ko:\n', encoding='utf-8')
    boot = staged / 'boot'
    boot.mkdir()
    shutil.copy2(extraction / 'boot' / ('config-' + version),
                 boot / ('config-' + version))

    finalize_payload_metadata(
        str(extraction), str(staged), 'usr/lib/modules', version)
    support = extraction / '.minios-kernel-dpkg'
    manifest = json.loads((support / 'manifest.json').read_text(encoding='utf-8'))
    drivers = next(package for package in manifest['packages']
                   if package['role'] == 'modules')
    payload = json.loads((support / drivers['payload_manifest']).read_text(
        encoding='utf-8'))
    assert payload == {
        'format': 1,
        'dpkg_instance': 'linux-modules-{}-amd64'.format(version),
        'files': [{
        'path': '/usr/lib/modules/{}/kernel/driver.ko'.format(version),
        'sha256': hashlib.sha256(b'fixture module').hexdigest(),
        'type': 'file',
    }]}
    modules = next(package for package in manifest['packages']
                   if package['role'] == 'modules')
    modules_payload = json.loads((support / modules['payload_manifest']).read_text(
        encoding='utf-8'))
    assert not any(file_record['path'].endswith('/modules.dep')
                   for file_record in modules_payload['files'])
    info_prefix = support / modules['info_prefix']
    assert os.path.isfile(str(info_prefix) + 'md5sums')
    assert not (support / '.source-payload.json').exists()


def test_rejects_kernel_without_system_map(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages

    version = '6.1.0-no-map'
    archive = build_deb(tmp_path, 'linux-image-' + version + '-amd64', {
        'boot/vmlinuz-' + version: b'kernel',
        'boot/config-' + version: b'config',
        'lib/modules/{}/kernel/core.ko'.format(version): b'core',
    })
    extracted = tmp_path / 'missing-map'
    extracted.mkdir()
    with pytest.raises(RuntimeError, match='System.map'):
        analyze_and_extract_packages([archive], str(extracted))


def test_rejects_module_symlink_that_escapes_version_tree(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages

    version = '6.1.0-escape'
    archive = build_deb(tmp_path, 'linux-image-' + version + '-amd64', {
        'boot/vmlinuz-' + version: b'kernel',
        'boot/config-' + version: b'config',
        'boot/System.map-' + version: b'map',
        'lib/modules/{}/kernel/core.ko'.format(version): b'core',
    }, symlinks=((
        'lib/modules/{}/leak'.format(version), '/etc/shadow'),))
    extracted = tmp_path / 'escaping-symlink'
    extracted.mkdir()
    with pytest.raises(RuntimeError, match='absolute symlink'):
        analyze_and_extract_packages([archive], str(extracted))


def test_real_squashfs_preflight_rejects_changed_external_kernel(tmp_path):
    from kernel_acquisition import analyze_and_extract_packages, finalize_payload_metadata
    from minios_utils import validate_kernel_bundle_artifacts

    if not shutil.which('mksquashfs') or not shutil.which('unsquashfs'):
        pytest.skip('SquashFS tools are unavailable')
    version, packages = split_fixture(tmp_path)
    extraction = tmp_path / 'bundle-extracted'
    extraction.mkdir()
    analyze_and_extract_packages(packages, str(extraction))
    staged = tmp_path / 'bundle-root'
    shutil.copytree(extraction / 'lib', staged / 'lib')
    module_root = staged / 'lib/modules' / version
    (module_root / 'modules.dep').write_text(
        'kernel/driver.ko:\n', encoding='utf-8')
    finalize_payload_metadata(str(extraction), str(staged), 'lib/modules', version)
    support_target = staged / 'usr/share/minios/kernel-dpkg'
    support_target.parent.mkdir(parents=True)
    shutil.copytree(extraction / '.minios-kernel-dpkg', support_target)
    assert (staged / 'boot' / ('config-' + version)).is_file()
    assert (staged / 'boot' / ('System.map-' + version)).is_file()
    squashfs = tmp_path / ('01-kernel-' + version + '.sb')
    subprocess.run([
        'mksquashfs', str(staged), str(squashfs), '-noappend', '-quiet',
        '-processors', '1'], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    vmlinuz = extraction / 'boot' / ('vmlinuz-' + version)
    initrd = tmp_path / ('initrfs-' + version + '.img')
    initrd.write_bytes(b'initrd')
    manifest = validate_kernel_bundle_artifacts(
        version, str(squashfs), str(vmlinuz), str(initrd))
    assert manifest['kernel']['version'] == version
    vmlinuz.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='payload hash mismatch'):
        validate_kernel_bundle_artifacts(
            version, str(squashfs), str(vmlinuz), str(initrd))


def test_activation_compatibility_accepts_only_complete_legacy_121_metadata(
        tmp_path):
    from kernel_acquisition import validate_embedded_support_tree

    version = '6.1.0-legacy'
    package = 'linux-image-' + version + '-amd64'
    support = tmp_path / 'support'
    (support / 'status.d').mkdir(parents=True)
    (support / 'info').mkdir()
    (support / 'manifest.json').write_text(json.dumps({
        'format': 1,
        'kernel_version': version,
        'packages': [{
            'name': package,
            'version': '1.0-1',
            'architecture': 'amd64',
        }],
    }), encoding='utf-8')
    (support / 'status.d' / (package + '.status')).write_text(
        'Package: {}\nStatus: install ok installed\n'.format(package),
        encoding='utf-8')
    (support / 'info' / (package + '.list')).write_text(
        '/.\n', encoding='utf-8')

    manifest = validate_embedded_support_tree(
        str(support), version, allow_legacy=True)
    assert manifest['kernel_version'] == version
    with pytest.raises(RuntimeError, match='canonical format 1'):
        validate_embedded_support_tree(str(support), version)


def test_activation_accepts_canonical_track_bundle_from_01_kernel(tmp_path):
    from kernel_acquisition import validate_embedded_support_tree

    version = '6.12.101+deb13-amd64'
    support = tmp_path / 'track-support'
    for directory in ('status.d', 'info', 'payload.d', 'keyrings'):
        (support / directory).mkdir(parents=True, exist_ok=True)
    keyring = support / 'keyrings/debian.gpg'
    keyring.write_bytes(b'debian-keyring')

    packages = []
    for role, name in (
            ('tracking-meta', 'linux-image-amd64'),
            ('base-meta', 'linux-base-amd64'),
            ('image', 'linux-image-' + version)):
        status = 'status.d/' + name + '.status'
        info_prefix = 'info/' + name + '.'
        (support / status).write_text(
            'Package: {}\nStatus: install ok installed\n'.format(name),
            encoding='utf-8')
        (support / (info_prefix + 'list')).write_text('/.\n', encoding='utf-8')
        (support / (info_prefix + 'md5sums')).write_text('', encoding='utf-8')
        package = {
            'role': role,
            'name': name,
            'version': '1.0-1',
            'architecture': 'amd64',
            'dpkg_instance': name,
            'source_package': 'linux-signed-amd64',
            'source_archive_sha256': 'a' * 64,
            'registration': 'synthetic-installed',
            'apt_mark': 'manual' if role == 'tracking-meta' else 'auto',
            'hold': False,
            'status': status,
            'info_prefix': info_prefix,
        }
        if role == 'image':
            payload = 'payload.d/' + name + '.json'
            (support / payload).write_text(json.dumps({
                'format': 1, 'dpkg_instance': name, 'files': [],
            }), encoding='utf-8')
            package['payload_manifest'] = payload
        packages.append(package)

    (support / 'manifest.json').write_text(json.dumps({
        'format': 1,
        'install_policy': 'register-materialized-payload',
        'update_policy': 'track',
        'userspace': {
            'family': 'debian', 'suite': 'trixie',
            'dpkg_architecture': 'amd64',
        },
        'kernel': {
            'distribution': 'trixie', 'version': version,
            'package_architecture': 'amd64',
        },
        'repositories': [{
            'release_identity': {
                'inrelease_sha256': 'b' * 64,
            },
            'keyring': {
                'path': 'keyrings/debian.gpg',
                'sha256': hashlib.sha256(b'debian-keyring').hexdigest(),
                'fingerprints': ['A' * 40],
            },
        }],
        'packages': packages,
    }), encoding='utf-8')

    manifest = validate_embedded_support_tree(str(support), version)
    assert manifest['update_policy'] == 'track'
