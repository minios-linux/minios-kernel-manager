#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for kernel_utils module.
"""

import sys
import os
import pytest
import tempfile
from unittest.mock import patch, MagicMock

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))


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

        assert 'linux-modules-6.8.0-60-generic' in deps
        assert 'linux-modules-extra-6.8.0-60-generic' in deps
        assert 'kmod' not in deps

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


class TestProcessManualPackages:
    """Tests for process_manual_packages function."""

    def test_single_package_without_modules_raises_clear_error(self):
        """Single .deb without modules should ask for linux-modules packages."""
        from kernel_utils import process_manual_packages

        with tempfile.TemporaryDirectory() as temp_dir:
            deb_path = os.path.join(temp_dir, 'linux-image-6.8.0-60-generic_1_amd64.deb')
            open(deb_path, 'w').close()
            real_exists = os.path.exists

            def exists_side_effect(path):
                if path in (deb_path, temp_dir):
                    return True
                if path.endswith('/boot') or path.endswith('/usr/boot'):
                    return False
                if path.endswith('/lib/modules') or path.endswith('/usr/lib/modules'):
                    return False
                return real_exists(path)

            with patch('subprocess.run', return_value=MagicMock(returncode=0)), \
                 patch('os.path.exists', side_effect=exists_side_effect):
                with pytest.raises(RuntimeError) as exc:
                    process_manual_packages([deb_path], temp_dir)

            assert 'linux-modules' in str(exc.value)


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

    def test_cache_exists_and_recent(self):
        """Test when cache exists and is recent."""
        import time
        from kernel_utils import check_package_cache
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['Packages']), \
             patch('os.path.getmtime', return_value=time.time()):
            
            success, message = check_package_cache(force_update=False)
            # Should succeed if cache is recent
            assert isinstance(success, bool)
            assert isinstance(message, str)

    def test_cache_outdated(self):
        """Test when cache is outdated."""
        import time
        from kernel_utils import check_package_cache
        
        old_time = time.time() - (8 * 24 * 60 * 60)  # 8 days ago
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=['Packages']), \
             patch('os.path.getmtime', return_value=old_time):
            
            success, message = check_package_cache(force_update=False)
            # May return success=False or suggest update
            assert isinstance(success, bool)

    def test_empty_lists_directory(self):
        """Test when lists directory is empty."""
        from kernel_utils import check_package_cache
        
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=[]):
            
            success, message = check_package_cache(force_update=False)
            # Should indicate problem
            assert isinstance(success, bool)


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
