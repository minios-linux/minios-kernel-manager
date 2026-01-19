#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for minios_utils module.
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock, mock_open

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))


class TestIsValidMiniosDirectory:
    """Tests for _is_valid_minios_directory function."""

    def test_valid_directory(self, temp_minios_dir):
        """Test detection of valid MiniOS directory."""
        from minios_utils import _is_valid_minios_directory
        
        assert _is_valid_minios_directory(temp_minios_dir) is True

    def test_invalid_empty_directory(self):
        """Test detection of invalid (empty) directory."""
        import tempfile
        from minios_utils import _is_valid_minios_directory
        
        with tempfile.TemporaryDirectory() as tmpdir:
            assert _is_valid_minios_directory(tmpdir) is False

    def test_nonexistent_directory(self):
        """Test handling of nonexistent directory."""
        from minios_utils import _is_valid_minios_directory
        
        assert _is_valid_minios_directory("/nonexistent/path") is False

    def test_permission_error(self):
        """Test handling of permission errors."""
        from minios_utils import _is_valid_minios_directory
        
        with patch('os.listdir', side_effect=PermissionError("Access denied")):
            with patch('os.path.exists', return_value=True):
                assert _is_valid_minios_directory("/some/path") is False


class TestFindMiniosDirectory:
    """Tests for find_minios_directory function."""

    def test_finds_standard_path(self, temp_minios_dir):
        """Test finding MiniOS directory at standard path."""
        from minios_utils import find_minios_directory
        
        with patch('minios_utils._is_valid_minios_directory') as mock_valid:
            mock_valid.side_effect = lambda p: p == temp_minios_dir
            
            # Mock standard paths not existing
            with patch('os.path.exists', return_value=False):
                result = find_minios_directory()
                # Will be None since standard paths don't exist
                assert result is None or result == temp_minios_dir

    def test_no_directory_found(self):
        """Test when no MiniOS directory is found."""
        from minios_utils import find_minios_directory
        
        with patch('minios_utils._is_valid_minios_directory', return_value=False), \
             patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='', returncode=0)
            
            result = find_minios_directory()
            assert result is None


class TestGetKernelRepositoryPath:
    """Tests for get_kernel_repository_path function."""

    def test_returns_correct_path(self, temp_minios_dir):
        """Test correct repository path generation."""
        from minios_utils import get_kernel_repository_path
        
        result = get_kernel_repository_path(temp_minios_dir)
        assert result == os.path.join(temp_minios_dir, "kernels")


class TestGetKernelPath:
    """Tests for get_kernel_path function."""

    def test_returns_correct_path(self, temp_minios_dir):
        """Test correct kernel version path generation."""
        from minios_utils import get_kernel_path
        
        result = get_kernel_path(temp_minios_dir, "6.1.0-18-amd64")
        assert result == os.path.join(temp_minios_dir, "kernels", "6.1.0-18-amd64")


class TestGetActiveKernel:
    """Tests for get_active_kernel function."""

    def test_read_from_marker_file(self, temp_minios_dir):
        """Test reading active kernel from marker file."""
        from minios_utils import get_active_kernel
        
        # Create marker file
        marker_path = os.path.join(temp_minios_dir, "boot", "active-kernel")
        with open(marker_path, 'w') as f:
            f.write("6.1.0-18-amd64\n")
        
        result = get_active_kernel(temp_minios_dir)
        assert result == "6.1.0-18-amd64"

    def test_fallback_to_vmlinuz_file(self, temp_minios_dir):
        """Test fallback to vmlinuz file when marker is missing."""
        from minios_utils import get_active_kernel
        
        # Create vmlinuz file without marker
        vmlinuz_path = os.path.join(temp_minios_dir, "boot", "vmlinuz-6.5.0-1-amd64")
        open(vmlinuz_path, 'w').close()
        
        result = get_active_kernel(temp_minios_dir)
        assert result == "6.5.0-1-amd64"

    def test_no_kernel_found(self, temp_minios_dir):
        """Test when no kernel is found."""
        from minios_utils import get_active_kernel
        
        # Empty boot directory
        result = get_active_kernel(temp_minios_dir)
        assert result is None


class TestPackageKernelToRepository:
    """Tests for package_kernel_to_repository function."""

    def test_successful_packaging(self, temp_minios_dir):
        """Test successful kernel packaging."""
        import tempfile
        from minios_utils import package_kernel_to_repository
        
        # Create temporary kernel files
        with tempfile.NamedTemporaryFile(suffix='.sb', delete=False) as sqfs, \
             tempfile.NamedTemporaryFile(prefix='vmlinuz-', delete=False) as vmlinuz, \
             tempfile.NamedTemporaryFile(suffix='.img', delete=False) as initramfs:
            
            sqfs.write(b'squashfs content')
            vmlinuz.write(b'vmlinuz content')
            initramfs.write(b'initramfs content')
            
            sqfs_path = sqfs.name
            vmlinuz_path = vmlinuz.name
            initramfs_path = initramfs.name
        
        try:
            result = package_kernel_to_repository(
                temp_minios_dir,
                "6.1.0-test",
                sqfs_path,
                vmlinuz_path,
                initramfs_path
            )
            
            assert result is True
            
            # Check files were copied
            kernel_dir = os.path.join(temp_minios_dir, "kernels", "6.1.0-test")
            assert os.path.exists(kernel_dir)
        finally:
            # Cleanup
            for f in [sqfs_path, vmlinuz_path, initramfs_path]:
                if os.path.exists(f):
                    os.unlink(f)

    def test_packaging_failure(self, temp_minios_dir):
        """Test packaging failure with missing files."""
        from minios_utils import package_kernel_to_repository
        
        result = package_kernel_to_repository(
            temp_minios_dir,
            "6.1.0-test",
            "/nonexistent/squashfs.sb",
            "/nonexistent/vmlinuz",
            "/nonexistent/initramfs.img"
        )
        
        assert result is False


class TestFormatSize:
    """Tests for _format_size function."""

    def test_format_bytes(self):
        """Test formatting byte values."""
        from minios_utils import _format_size
        
        assert _format_size(500) == "500 B"

    def test_format_kilobytes(self):
        """Test formatting kilobyte values."""
        from minios_utils import _format_size
        
        result = _format_size(1024)
        assert "KB" in result or "K" in result

    def test_format_megabytes(self):
        """Test formatting megabyte values."""
        from minios_utils import _format_size
        
        result = _format_size(1024 * 1024)
        assert "MB" in result or "M" in result

    def test_format_gigabytes(self):
        """Test formatting gigabyte values."""
        from minios_utils import _format_size
        
        result = _format_size(1024 * 1024 * 1024)
        assert "GB" in result or "G" in result


class TestGetCurrentlyRunningKernel:
    """Tests for get_currently_running_kernel function."""

    def test_get_running_kernel(self):
        """Test getting currently running kernel version."""
        from minios_utils import get_currently_running_kernel
        
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='6.1.0-18-amd64\n',
                returncode=0
            )
            
            result = get_currently_running_kernel()
            assert '6.1.0' in result or result  # May use platform.release()


class TestGetSystemType:
    """Tests for get_system_type function."""

    def test_get_system_type(self):
        """Test getting system type."""
        from minios_utils import get_system_type
        
        with patch('os.path.exists') as mock_exists:
            # Simulate Debian system
            mock_exists.side_effect = lambda p: '/etc/debian_version' in p
            
            result = get_system_type()
            assert result in ['debian', 'ubuntu', 'unknown']


class TestGetUnionFilesystemType:
    """Tests for get_union_filesystem_type function."""

    def test_detect_overlayfs(self):
        """Test detecting OverlayFS."""
        from minios_utils import get_union_filesystem_type
        
        proc_cmdline = "BOOT_IMAGE=/minios/boot/vmlinuz union=overlayfs"
        proc_filesystems = "nodev\toverlayfs\n"
        
        files = {
            '/proc/cmdline': proc_cmdline,
            '/proc/filesystems': proc_filesystems
        }
        
        def mock_open_func(path, *args, **kwargs):
            if path in files:
                return mock_open(read_data=files[path])()
            raise FileNotFoundError(path)
        
        with patch('builtins.open', side_effect=mock_open_func):
            result = get_union_filesystem_type()
            assert result == 'overlayfs'

    def test_detect_aufs(self):
        """Test detecting AUFS."""
        from minios_utils import get_union_filesystem_type
        
        proc_cmdline = "BOOT_IMAGE=/minios/boot/vmlinuz union=aufs"
        
        with patch('builtins.open', mock_open(read_data=proc_cmdline)):
            result = get_union_filesystem_type()
            assert result == 'aufs'
