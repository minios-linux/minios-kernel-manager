import json
import hashlib
import subprocess
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
    assert 'find "${MODULE_UPPER}" -type f' in source
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


def _context(suite='buster', arch='amd64', series='4.19', capabilities=(),
             provider='distribution'):
    return {'distribution': suite, 'architecture': arch, 'series': series,
            'capabilities': list(capabilities), 'provider': provider,
            'profile': 'debian', 'variant': 'standard'}


@pytest.mark.parametrize('arch', ['amd64', 'i386', 'i386-pae'])
@pytest.mark.parametrize('suite', ['buster', 'beowulf', 'bullseye', 'chimaera',
                                  'bookworm', 'daedalus', 'bionic', 'focal', 'jammy'])
def test_legacy_realtek_versions_on_all_architectures(arch, suite):
    requests, skipped = resolve_driver_selection(
        ['rtl88xxau', 'rtl8814au'], _context(suite=suite, arch=arch))
    assert [item['package_spec'] for item in requests] == [
        'realtek-rtl88xxau-dkms=5.6.4.2~git20240726.63cf0b4-0kali1',
        'realtek-rtl8814au-dkms=5.8.5.1~git20240527.d8208c8-0kali1']
    assert skipped == []


def test_legacy_realtek_dependency_is_pinned_when_not_selected():
    requests, _ = resolve_driver_selection(['rtl88xxau'], _context())
    assert [item['package_spec'] for item in requests] == [
        'realtek-rtl88xxau-dkms=5.6.4.2~git20240726.63cf0b4-0kali1',
        'realtek-rtl8814au-dkms=5.8.5.1~git20240527.d8208c8-0kali1']


@pytest.mark.parametrize('context', [_context(suite='trixie', series='6.12'),
                                     _context(provider='minios', series='6.12')])
def test_modern_realtek_versions_are_not_pinned(context):
    requests, _ = resolve_driver_selection(['rtl88xxau', 'rtl8814au'], context)
    assert [item['package_spec'] for item in requests] == [
        'realtek-rtl88xxau-dkms', 'realtek-rtl8814au-dkms']


@pytest.mark.parametrize('series', ['4.9', '4.15', '4.19', '5.4', '5.10', '5.15'])
def test_old_kernel_selection(series):
    context = _context(series=series)
    with pytest.raises(RuntimeError, match='NTFS3.*no sources'):
        resolve_driver_selection(['ntfs3'], context)
    requests, _ = resolve_driver_selection(['rtl8188eus'], context)
    assert requests[0]['package_spec'] == (
        'realtek-rtl8188eus-dkms=5.3.9~git20230921.3fae723-0kali2')


@pytest.mark.parametrize('series', ['6.1', '6.4', '6.5', '6.6', '6.7', '6.8',
                                   '6.9', '6.10', '6.11', '6.12', '6.13'])
def test_ntfs3_supported_kernel_series(series):
    requests, _ = resolve_driver_selection(['ntfs3', 'rtl8188eus'],
                                            _context(series=series))
    assert [item['package_spec'] for item in requests] == [
        'ntfs3-dkms', 'realtek-rtl8188eus-dkms']
    requests, skipped = resolve_driver_selection(
        ['ntfs3'], _context(series=series, capabilities=['ntfs3']))
    assert requests == [] and skipped == ['ntfs3']


def test_ntfs3_unknown_series_and_gui_filter(monkeypatch):
    with pytest.raises(RuntimeError, match='NTFS3.*no sources'):
        resolve_driver_selection(['ntfs3'], _context(series='6.14'))
    monkeypatch.setattr('dkms_utils._host_distribution', lambda: ('buster', 'debian'))
    compatible = get_compatible_driver_ids('linux-image-4.19.0-21-amd64', 'amd64')
    assert 'ntfs3' not in compatible
    assert 'rtl8188eus' in compatible


def test_sandbox_collects_legacy_and_modern_install_paths(tmp_path):
    upper = tmp_path / 'upper'
    result = tmp_path / 'result'
    paths = ['updates/dkms/modern.ko.xz', 'updates/legacy.ko',
             'kernel/drivers/net/8188eu.ko', 'kernel/fs/aufs/aufs.ko']
    for path in paths + ['modules.dep']:
        target = upper / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.encode())
    source = (Path(__file__).resolve().parents[1] / 'libexec/dkms-sandbox').read_text()
    collector = source.split(': > /run/minios-built-modules', 1)[1].split(
        'for RUNTIME_DIR', 1)[0]
    collector = ': > /run/minios-built-modules' + collector
    collector = collector.replace('/run/minios-built-modules', str(tmp_path / 'list'))
    subprocess.run(['bash', '-eu', '-c',
                    'MODULE_UPPER="$1"; RESULT="$2"; ' + collector,
                    'collector', str(upper), str(result)], check=True)
    for path in paths:
        assert (result / 'modules' / path).read_bytes() == path.encode()
    assert not (result / 'modules/modules.dep').exists()
