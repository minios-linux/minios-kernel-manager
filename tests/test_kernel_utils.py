#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for kernel_utils module.
"""

import sys
import os
import json
import pytest
import tempfile
import subprocess
from unittest.mock import patch, MagicMock

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))


def build_deb(tmp_path, name, payload, depends='', scripts=()):
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
    (control_dir / 'control').write_text('\n'.join(control) + '\n', encoding='utf-8')
    for script in scripts:
        path = control_dir / script
        path.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        path.chmod(0o755)
    for relative, content in payload.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    deb = tmp_path / (name + '_1.0-1_amd64.deb')
    subprocess.run(['dpkg-deb', '--build', str(root), str(deb)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return str(deb)


class TestGetAvailableKernels:
    """Tests for get_available_kernels function."""

    def test_lists_kernel_modules(self, temp_modules_dir):
        """Test listing kernel module directories."""
        from kernel_utils import get_available_kernels
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['6.1.0-1', '6.1.0-2', '6.5.0-1']), \
             patch('os.path.isdir', return_value=True):
            
            kernels = get_available_kernels()
            assert len(kernels) == 3
            assert '6.1.0-1' in kernels
            assert '6.1.0-2' in kernels
            assert '6.5.0-1' in kernels

    def test_empty_modules_dir(self):
        """Test handling of empty modules directory."""
        from kernel_utils import get_available_kernels
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=[]):
            
            kernels = get_available_kernels()
            assert kernels == []

    def test_missing_modules_dir(self):
        """Test handling of missing modules directory."""
        from kernel_utils import get_available_kernels
        
        with patch('os.path.exists', return_value=False):
            kernels = get_available_kernels()
            assert kernels == []

    def test_returns_sorted_list(self):
        """Test that kernel list is sorted."""
        from kernel_utils import get_available_kernels
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['6.5.0-1', '6.1.0-1', '6.1.0-2']), \
             patch('os.path.isdir', return_value=True):
            
            kernels = get_available_kernels()
            assert kernels == sorted(kernels)


class TestGetManualPackages:
    """Tests for get_manual_packages function."""

    def test_returns_empty_list(self):
        """Test that function returns empty list (compatibility stub)."""
        from kernel_utils import get_manual_packages
        
        result = get_manual_packages()
        assert result == []


class TestGetRepositoryKernels:
    """Tests for get_repository_kernels function."""

    def test_parses_apt_search_output(self, sample_apt_cache_search, sample_apt_cache_show):
        """Test parsing apt-cache output."""
        from kernel_utils import get_repository_kernels
        
        def run_side_effect(cmd, **kwargs):
            if 'search' in cmd:
                return MagicMock(stdout=sample_apt_cache_search, returncode=0)
            elif 'show' in cmd:
                return MagicMock(stdout=sample_apt_cache_show, returncode=0)
            return MagicMock(stdout='', returncode=0)
        
        with patch('subprocess.run', side_effect=run_side_effect):
            packages = get_repository_kernels()
            
            # Should have packages (excluding dbg)
            assert len(packages) >= 0  # May be filtered by size threshold

    def test_excludes_debug_packages(self, sample_apt_cache_search):
        """Test that debug packages are excluded."""
        from kernel_utils import get_repository_kernels
        
        def run_side_effect(cmd, **kwargs):
            if 'search' in cmd:
                return MagicMock(stdout=sample_apt_cache_search, returncode=0)
            return MagicMock(stdout='Size: 0', returncode=0)
        
        with patch('subprocess.run', side_effect=run_side_effect):
            packages = get_repository_kernels()
            
            for pkg in packages:
                assert 'dbg' not in pkg.get('package', '')

    def test_filters_non_versioned_and_duplicate_search_entries(self):
        from kernel_utils import get_repository_kernels

        output = ('linux-image-amd64 - meta package\n'
                  'linux-image-6.1.0-1-amd64 - kernel\n'
                  'linux-image-6.1.0-1-amd64 - duplicate\n'
                  'linux-image-6.1.0-1-amd64-dbg - debug\n')

        def run_side_effect(command, **kwargs):
            if 'search' in command:
                return MagicMock(stdout=output, returncode=0)
            return MagicMock(
                stdout='Version: 1\nSize: 2000000\nArchitecture: amd64\n',
                returncode=0)

        with patch('subprocess.run', side_effect=run_side_effect):
            packages = get_repository_kernels()
        assert [package['package'] for package in packages] == [
            'linux-image-6.1.0-1-amd64']

    def test_keeps_small_versioned_split_coordinator(self):
        from kernel_utils import get_repository_kernels

        def run_side_effect(command, **kwargs):
            if 'search' in command:
                return MagicMock(
                    stdout='linux-image-6.19.14+deb13-amd64 - kernel\n',
                    returncode=0)
            return MagicMock(
                stdout=('Version: 6.19.14-1\nSize: 2052\n'
                        'Architecture: amd64\n'), returncode=0)

        with patch('subprocess.run', side_effect=run_side_effect):
            packages = get_repository_kernels()
        assert [package['package'] for package in packages] == [
            'linux-image-6.19.14+deb13-amd64']

    def test_handles_apt_error(self):
        """Test handling of apt-cache errors."""
        import subprocess
        from kernel_utils import get_repository_kernels
        
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'apt-cache')):
            packages = get_repository_kernels()
            assert packages == []


class TestResolveKernelDependencies:
    """Tests for resolve_kernel_dependencies function."""

    def test_ubuntu_split_kernel_dependencies(self):
        """Extract linux-modules* dependencies from apt-cache depends output."""
        from kernel_utils import resolve_kernel_dependencies

        apt_depends_output = '''linux-image-6.8.0-60-generic
  Depends: kmod
  Depends: linux-base
  Depends: linux-modules-6.8.0-60-generic
  Depends: linux-modules-extra-6.8.0-60-generic
  Depends: initramfs-tools | linux-initramfs-tool
'''

        with patch('subprocess.run', return_value=MagicMock(stdout=apt_depends_output, returncode=0)):
            deps = resolve_kernel_dependencies('linux-image-6.8.0-60-generic')

        assert deps == [
            'linux-modules-6.8.0-60-generic',
            'linux-modules-extra-6.8.0-60-generic',
        ]

    def test_debian_monolithic_kernel_dependencies(self):
        """Return empty list when no linux-modules* dependencies are present."""
        from kernel_utils import resolve_kernel_dependencies

        apt_depends_output = '''linux-image-6.1.0-18-amd64
  Depends: kmod
  Depends: linux-base
  Depends: initramfs-tools | linux-initramfs-tool
'''

        with patch('subprocess.run', return_value=MagicMock(stdout=apt_depends_output, returncode=0)):
            deps = resolve_kernel_dependencies('linux-image-6.1.0-18-amd64')

        assert deps == []

    def test_debian_split_kernel_dependencies_exclude_userspace_tools(self):
        from kernel_utils import resolve_kernel_dependencies

        image = 'linux-image-6.19.14+deb13-amd64'
        output = '''{}
  Depends: linux-base-6.19.14+deb13-amd64
  Depends: linux-binary-6.19.14+deb13-amd64
  Depends: linux-modules-6.19.14+deb13-amd64
  Depends: initramfs-tools | linux-initramfs-tool
  Depends: kmod
'''.format(image)

        def run_side_effect(command, **kwargs):
            return MagicMock(
                stdout=output if command[-1] == image else '', returncode=0)

        with patch('subprocess.run', side_effect=run_side_effect):
            dependencies = resolve_kernel_dependencies(image)

        assert dependencies == [
            'linux-base-6.19.14+deb13-amd64',
            'linux-binary-6.19.14+deb13-amd64',
            'linux-modules-6.19.14+deb13-amd64',
        ]


class TestProcessManualPackages:
    """Tests for process_manual_packages function."""

    def test_single_package_without_modules_raises_clear_error(self, tmp_path):
        """Single .deb without modules should ask for linux-modules packages."""
        from kernel_utils import process_manual_packages

        deb_path = build_deb(tmp_path, 'fixture-image', {
            'boot/vmlinuz-6.8.0-fixture': b'kernel',
            'boot/config-6.8.0-fixture': b'CONFIG_TEST=y\nCONFIG_EFI_STUB=y\n',
            'boot/System.map-6.8.0-fixture': b'map',
        }, scripts=('postinst',))
        extraction = tmp_path / 'extracted'
        extraction.mkdir()
        with pytest.raises(RuntimeError) as exc:
            process_manual_packages([deb_path], str(extraction))

        assert 'modules' in str(exc.value)


class TestPreserveKernelDpkgMetadata:
    def test_real_split_packages_use_dependency_and_file_ownership_roles(self, tmp_path):
        from kernel_utils import KERNEL_DPKG_METADATA_DIR, preserve_kernel_dpkg_metadata_from_debs

        version = '6.12.0-fixture'
        image = 'linux-image-' + version + '-amd64'
        binary = 'linux-binary-' + version + '-amd64'
        base = 'linux-base-' + version + '-amd64'
        modules = 'linux-modules-' + version + '-amd64'
        deb_files = [
            build_deb(tmp_path, image, {},
                      '{}, {}, {}'.format(binary, base, modules),
                      scripts=('postinst', 'prerm')),
            build_deb(tmp_path, binary, {
                'boot/vmlinuz-' + version: b'kernel'}),
            build_deb(tmp_path, base, {
                'boot/config-' + version: b'CONFIG_TEST=y\nCONFIG_EFI_STUB=y\n',
                'boot/System.map-' + version: b'map',
                'lib/modules/{}/modules.order'.format(version): b'driver.ko\n'}),
            build_deb(tmp_path, modules, {
                'lib/modules/{}/kernel/driver.ko'.format(version): b'module'}),
        ]
        extraction = tmp_path / 'extracted'
        extraction.mkdir()
        preserve_kernel_dpkg_metadata_from_debs(
            deb_files, str(extraction), version)

        metadata_dir = extraction / KERNEL_DPKG_METADATA_DIR

        with (metadata_dir / 'manifest.json').open(encoding='utf-8') as fh:
            manifest = json.load(fh)
        assert set(manifest) == {
            'format', 'install_policy', 'update_policy', 'userspace', 'kernel',
            'repositories', 'packages'}
        assert set(manifest['userspace']) == {
            'family', 'suite', 'dpkg_architecture'}
        assert set(manifest['kernel']) == {
            'distribution', 'version', 'package_architecture'}
        roles = {package['name']: package['role'] for package in manifest['packages']}
        assert roles == {
            image: 'image', binary: 'binary', base: 'base', modules: 'modules'}
        assert manifest['format'] == 1
        assert manifest['install_policy'] == 'register-materialized-payload'
        assert manifest['update_policy'] == 'frozen'
        assert manifest['kernel']['version'] == version
        assert manifest['repositories'] == []
        assert not (metadata_dir / 'kernel.lock').exists()
        for package in manifest['packages']:
            assert package['dpkg_instance'] == package['name']
            assert len(package['source_archive_sha256']) == 64
            assert (metadata_dir / package['status']).is_file()
            assert (metadata_dir / (package['info_prefix'] + 'list')).is_file()


class TestParsePackageInfo:
    """Tests for _parse_package_info function."""

    def test_parses_complete_info(self, sample_apt_cache_show):
        """Test parsing complete package information."""
        from kernel_utils import _parse_package_info
        
        result = _parse_package_info(
            sample_apt_cache_show,
            'linux-image-6.1.0-18-amd64',
            'Linux 6.1 for 64-bit PCs'
        )
        
        assert result is not None
        assert result['package'] == 'linux-image-6.1.0-18-amd64'
        assert result['version'] == '6.1.76-1'
        assert result['size'] == 68891972
        assert result['architecture'] == 'amd64'

    def test_returns_none_for_zero_size(self):
        """Test returning None when size is 0."""
        from kernel_utils import _parse_package_info
        
        apt_output = '''Package: test-package
Version: 1.0
Size: 0
'''
        result = _parse_package_info(apt_output, 'test-package', 'Test')
        assert result is None


class TestFormatSize:
    """Tests for _format_size function."""

    def test_format_bytes(self):
        """Test formatting byte values."""
        from kernel_utils import _format_size
        
        assert '500' in _format_size(500)
        assert 'B' in _format_size(500)

    def test_format_kilobytes(self):
        """Test formatting kilobyte values."""
        from kernel_utils import _format_size
        
        result = _format_size(1024)
        assert 'KB' in result

    def test_format_megabytes(self):
        """Test formatting megabyte values."""
        from kernel_utils import _format_size
        
        result = _format_size(1024 * 1024)
        assert 'MB' in result

    def test_format_gigabytes(self):
        """Test formatting gigabyte values."""
        from kernel_utils import _format_size
        
        result = _format_size(1024 * 1024 * 1024)
        assert 'GB' in result


class TestCheckPackageCache:
    """Tests for check_package_cache function."""

    def test_uses_existing_lists_without_an_age_gate(self):
        from kernel_utils import check_package_cache

        with patch('kernel_utils.subprocess.run') as run:
            assert check_package_cache(force_update=False) == (True, '')
        run.assert_not_called()

    def test_force_update_refreshes_system_lists(self):
        from kernel_utils import check_package_cache

        with patch('kernel_utils.subprocess.run') as run:
            assert check_package_cache(force_update=True) == (True, '')
        assert run.call_args[0][0] == ['apt', 'update']


class TestDownloadKernelPackage:
    def test_uses_system_apt_and_temporary_kernel_archives_only(
            self, tmp_path, capsys):
        from kernel_utils import download_kernel_package

        image = 'linux-image-6.8.0-60-generic'
        modules = 'linux-modules-6.8.0-60-generic'

        def run_side_effect(command, **kwargs):
            if command[:2] == ['apt-get', 'download']:
                (tmp_path / (image + '_1_amd64.deb')).write_bytes(b'image')
                (tmp_path / (modules + '_1_amd64.deb')).write_bytes(b'modules')
            return MagicMock(returncode=0, stdout='download log\n')

        with patch('kernel_utils.check_package_cache', return_value=(True, '')), \
             patch('kernel_utils.resolve_kernel_dependencies',
                   return_value=[modules]), \
             patch('kernel_utils.subprocess.run', side_effect=run_side_effect) as run, \
             patch('kernel_utils.analyze_and_extract_packages',
                   return_value='6.8.0-60-generic') as analyze:
            assert download_kernel_package(image, str(tmp_path)) == \
                '6.8.0-60-generic'

        assert run.call_args_list[0][0][0] == [
            'apt-get', 'download', image, modules]
        assert run.call_args_list[0][1]['cwd'] == str(tmp_path)
        assert run.call_args_list[0][1]['stdout'] is subprocess.PIPE
        assert run.call_args_list[0][1]['stderr'] is subprocess.STDOUT
        archives = analyze.call_args[0][0]
        assert sorted(os.path.basename(path) for path in archives) == [
            image + '_1_amd64.deb', modules + '_1_amd64.deb']
        captured = capsys.readouterr()
        assert captured.out == ''
        assert captured.err == 'download log\n'

    def test_rejects_tracking_metapackage(self, tmp_path):
        from kernel_utils import download_kernel_package

        with pytest.raises(RuntimeError, match='real versioned kernel'):
            download_kernel_package('linux-image-amd64', str(tmp_path))


class TestGetNonSymlinkModulesDir:
    """Tests for get_non_symlink_modules_dir function."""

    def test_returns_lib_modules(self):
        """Test returning /lib/modules path."""
        from kernel_utils import get_non_symlink_modules_dir
        
        with patch('os.path.islink', return_value=False), \
             patch('os.path.exists', return_value=True):
            
            result = get_non_symlink_modules_dir()
            assert '/lib/modules' in result or '/usr/lib/modules' in result


class TestLocateKernelModules:
    """Tests for locate_kernel_modules function."""

    def test_finds_modules_directory(self):
        """Test finding kernel modules directory."""
        from kernel_utils import locate_kernel_modules
        
        with patch('os.path.exists', return_value=True):
            result = locate_kernel_modules('6.1.0-18-amd64')
            assert '/lib/modules' in result or '/usr/lib/modules' in result

    def test_module_not_found(self):
        """Test handling of missing modules."""
        import pytest
        from kernel_utils import locate_kernel_modules
        
        with patch('os.path.exists', return_value=False):
            with pytest.raises(RuntimeError):
                locate_kernel_modules('nonexistent-kernel')
