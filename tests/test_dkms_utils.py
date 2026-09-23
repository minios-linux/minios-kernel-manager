import json
import hashlib
from pathlib import Path

import pytest

from dkms_utils import (detect_kernel_context, get_compatible_driver_ids,
                        get_driver_catalog, finalize_driver_manifest,
                        resolve_driver_selection)


def _kernel_tree(tmp_path, capabilities=()):
    support = tmp_path / '.minios-kernel-dpkg'
    support.mkdir()
    manifest = {
        'userspace': {
            'family': 'debian',
            'suite': 'trixie',
            'dpkg_architecture': 'amd64',
        },
        'kernel': {
            'distribution': 'trixie',
            'version': '6.12.1-amd64',
            'package_architecture': 'amd64',
        },
    }
    (support / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    boot = tmp_path / 'boot'
    boot.mkdir()
    config_lines = ['CONFIG_EFI_STUB=y']
    if 'ntfs3' in capabilities:
        config_lines.append('CONFIG_NTFS3_FS=m')
    (boot / 'config-6.12.1-amd64').write_text(
        '\n'.join(config_lines) + '\n', encoding='utf-8')
    modules = tmp_path / 'usr/lib/modules/6.12.1-amd64'
    modules.mkdir(parents=True)
    aliases = ''
    if 'rtw88_8821au' in capabilities:
        aliases = 'alias usb:test rtw88_8821au\n'
    (modules / 'modules.alias').write_text(aliases, encoding='utf-8')


def test_catalog_matches_01_kernel_dkms_package_names():
    live_root = Path(__file__).resolve().parents[3]
    package_list = live_root / 'linux-live/scripts/01-kernel/packages.list'
    if not package_list.exists():
        pytest.skip('linux-live source is not available in standalone checkout')
    lines = package_list.read_text(encoding='utf-8').splitlines()
    start = lines.index('# BEGIN DKMS PACKAGES') + 1
    package_names = set()
    for line in lines[start:]:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        package_names.add(line.split()[0].split('=', 1)[0])
    assert {item['package'] for item in get_driver_catalog()} == package_names


def test_dkms_workspace_does_not_use_tmpfs():
    helper = (Path(__file__).resolve().parents[1] / 'libexec/dkms-sandbox')
    source = helper.read_text(encoding='utf-8')
    assert 'mount -t tmpfs' not in source
    assert '< <(' not in source
    assert '${MODULE_UPPER}/updates/dkms' in source
    assert 'ROOT_UPPER="${WORKSPACE}/root-upper"' in source


def test_gui_compatibility_filters_series_specific_drivers():
    compatible = get_compatible_driver_ids(
        'linux-image-6.1.0-30-amd64', 'amd64')
    assert 'dynblk' not in compatible
    assert 'rtl8188eus' in compatible


def test_context_detects_config_and_module_capabilities(tmp_path):
    _kernel_tree(tmp_path, capabilities=('ntfs3', 'rtw88_8821au'))

    context = detect_kernel_context(str(tmp_path), '6.12.1-amd64')

    assert context['series'] == '6.12'
    assert context['architecture'] == 'amd64'
    assert {'ntfs3', 'rtw88_8821au'} <= set(context['capabilities'])


def test_selection_skips_driver_already_provided_by_kernel(tmp_path):
    _kernel_tree(tmp_path, capabilities=('ntfs3',))
    context = detect_kernel_context(str(tmp_path), '6.12.1-amd64')

    requests, skipped = resolve_driver_selection(['ntfs3', 'dynblk'], context)

    assert [item['id'] for item in requests] == ['dynblk']
    assert skipped == ['ntfs3']


def test_selection_rejects_driver_outside_01_kernel_conditions(tmp_path):
    _kernel_tree(tmp_path)
    context = detect_kernel_context(str(tmp_path), '6.12.1-amd64')

    with pytest.raises(RuntimeError, match='RTL88x2BU is unavailable'):
        resolve_driver_selection(['rtl88x2bu'], context)


@pytest.mark.parametrize('variant', ['standard', 'toolbox', 'ultra', 'flux', ''])
def test_zfs_selection_is_independent_of_edition(tmp_path, variant):
    _kernel_tree(tmp_path)
    context = detect_kernel_context(str(tmp_path), '6.12.1-amd64')
    context['variant'] = variant
    requests, skipped = resolve_driver_selection(['zfs'], context)
    assert [item['package_spec'] for item in requests] == ['zfs-dkms']
    assert skipped == []
    context['architecture'] = 'i386'
    with pytest.raises(RuntimeError, match='available only for amd64'):
        resolve_driver_selection(['zfs'], context)


@pytest.mark.parametrize('suffix', ['', '.xz', '.gz', '.zst'])
def test_final_manifest_describes_published_module(tmp_path, suffix):
    source = tmp_path / 'source.json'
    output = tmp_path / 'final.json'
    modules = tmp_path / 'modules'
    module = modules / 'updates/dkms/driver.ko'
    module.parent.mkdir(parents=True)
    module.write_bytes(b'published module')
    source.write_text(json.dumps({'files': [{
        'path': 'updates/dkms/driver.ko' + suffix, 'sha256': 'old'}]}))
    finalize_driver_manifest(str(source), str(output), str(modules))
    assert json.loads(output.read_text())['files'] == [{
        'path': 'updates/dkms/driver.ko',
        'sha256': hashlib.sha256(b'published module').hexdigest()}]
    assert json.loads(source.read_text())['files'][0]['sha256'] == 'old'
