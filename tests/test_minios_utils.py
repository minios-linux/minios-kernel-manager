#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for minios_utils module.
"""

import sys
import os
import subprocess
import stat
import pytest
from unittest.mock import patch, MagicMock

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

    def test_parses_findmnt_target_with_spaces(self):
        from minios_utils import find_minios_directory

        target = '/media/Mini OS'
        with patch('minios_utils._is_valid_minios_directory', side_effect=lambda path: path == target + '/minios'), \
             patch('subprocess.run', return_value=MagicMock(stdout=target + '\n', returncode=0)):
            assert find_minios_directory() == target + '/minios'


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

    @pytest.mark.parametrize('version', ['../outside', '/tmp/outside', '', '.', '..'])
    def test_rejects_path_traversal(self, temp_minios_dir, version):
        from minios_utils import get_kernel_path

        with pytest.raises(ValueError):
            get_kernel_path(temp_minios_dir, version)


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
        
        source_dir = tempfile.mkdtemp()
        sqfs_path = os.path.join(source_dir, '01-kernel-6.1.0-test.sb')
        vmlinuz_path = os.path.join(source_dir, 'vmlinuz-6.1.0-test')
        initramfs_path = os.path.join(source_dir, 'initrfs-6.1.0-test.img')
        for path, content in (
                (sqfs_path, b'squashfs content'),
                (vmlinuz_path, b'vmlinuz content'),
                (initramfs_path, b'initramfs content')):
            with open(path, 'wb') as artifact:
                artifact.write(content)
        
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
            os.rmdir(source_dir)

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

    def test_failure_preserves_existing_repository_entry(self, temp_minios_dir):
        from minios_utils import package_kernel_to_repository

        existing = os.path.join(temp_minios_dir, 'kernels', '6.1.0-test')
        os.makedirs(existing)
        sentinel = os.path.join(existing, 'sentinel')
        open(sentinel, 'w').close()

        assert not package_kernel_to_repository(temp_minios_dir, '6.1.0-test', '/missing', '/missing', '/missing')
        assert os.path.exists(sentinel)

    def test_rejects_symlinked_repository(self, temp_minios_dir, tmp_path):
        from minios_utils import package_kernel_to_repository

        os.rmdir(os.path.join(temp_minios_dir, 'kernels'))
        outside = tmp_path / 'outside'
        outside.mkdir()
        os.symlink(str(outside), os.path.join(temp_minios_dir, 'kernels'))

        assert not package_kernel_to_repository(temp_minios_dir, '6.1.0-test', '/missing', '/missing', '/missing')
        assert not list(outside.iterdir())

    def test_rejects_symlink_artifact_without_publishing(self, temp_minios_dir, tmp_path):
        from minios_utils import package_kernel_to_repository

        source = tmp_path / 'source'
        source.mkdir()
        victim = source / 'victim'
        victim.write_bytes(b'victim')
        squashfs = source / '01-kernel-test.sb'
        os.symlink(str(victim), str(squashfs))
        vmlinuz = source / 'vmlinuz-test'
        initramfs = source / 'initrfs-test.img'
        vmlinuz.write_bytes(b'kernel')
        initramfs.write_bytes(b'initrd')

        assert not package_kernel_to_repository(
            temp_minios_dir, 'test', str(squashfs), str(vmlinuz),
            str(initramfs))
        assert not os.path.lexists(
            os.path.join(temp_minios_dir, 'kernels', 'test'))
        assert victim.read_bytes() == b'victim'


class TestPublishKernelArtifacts:
    def test_refuses_existing_symlink_and_rolls_back_other_artifacts(self, tmp_path):
        from minios_utils import publish_kernel_artifacts

        source = tmp_path / 'source'
        output = tmp_path / 'output'
        source.mkdir()
        output.mkdir()
        victim = tmp_path / 'victim'
        victim.write_bytes(b'unchanged')
        names = ('01-kernel-test.sb', 'vmlinuz-test', 'initrfs-test.img')
        artifacts = []
        for name in names:
            path = source / name
            path.write_bytes(name.encode('ascii'))
            artifacts.append(str(path))
        os.symlink(str(victim), str(output / 'vmlinuz-test'))

        with pytest.raises(OSError):
            publish_kernel_artifacts(str(output), artifacts)

        assert not (output / '01-kernel-test.sb').exists()
        assert os.path.islink(str(output / 'vmlinuz-test'))
        assert victim.read_bytes() == b'unchanged'

    def test_publishes_only_complete_regular_files(self, tmp_path):
        from minios_utils import publish_kernel_artifacts

        source = tmp_path / 'source'
        output = tmp_path / 'output'
        source.mkdir()
        artifacts = []
        for name in ('01-kernel-test.sb', 'vmlinuz-test', 'initrfs-test.img'):
            path = source / name
            path.write_bytes(name.encode('ascii'))
            artifacts.append(str(path))

        published = publish_kernel_artifacts(str(output), artifacts)

        assert {os.path.basename(path) for path in published} == {
            os.path.basename(path) for path in artifacts}
        for source_path in artifacts:
            destination = output / os.path.basename(source_path)
            assert destination.read_bytes() == open(source_path, 'rb').read()
            assert not os.path.islink(str(destination))


class TestDeletePackagedKernel:
    def test_rejects_traversal_and_preserves_outside_file(self, temp_minios_dir, tmp_path):
        from minios_utils import delete_packaged_kernel

        outside = tmp_path / 'outside'
        outside.mkdir()
        assert delete_packaged_kernel(temp_minios_dir, '../outside') is False
        assert outside.exists()

    def test_refuses_repository_symlink_without_following_it(self, temp_minios_dir, tmp_path):
        from minios_utils import delete_packaged_kernel

        outside = tmp_path / 'outside'
        outside.mkdir()
        link = os.path.join(temp_minios_dir, 'kernels', 'test')
        os.symlink(str(outside), link)

        assert delete_packaged_kernel(temp_minios_dir, 'test') is False
        assert os.path.islink(link)
        assert outside.exists()

    def test_refuses_active_kernel(self, temp_minios_dir):
        from minios_utils import delete_packaged_kernel

        os.makedirs(os.path.join(temp_minios_dir, 'kernels', 'active'))
        with open(os.path.join(temp_minios_dir, 'boot', 'active-kernel'), 'w') as marker:
            marker.write('active')

        assert delete_packaged_kernel(temp_minios_dir, 'active') is False
        assert os.path.isdir(os.path.join(temp_minios_dir, 'kernels', 'active'))

    def test_refuses_running_kernel(self, temp_minios_dir):
        from minios_utils import delete_packaged_kernel

        path = os.path.join(temp_minios_dir, 'kernels', 'running')
        os.makedirs(path)
        with patch('minios_utils.is_kernel_currently_running', return_value=True):
            assert delete_packaged_kernel(temp_minios_dir, 'running') is False
        assert os.path.isdir(path)


class TestActivateKernel:
    def test_rejects_symlinked_kernel_entry(self, temp_minios_dir, tmp_path):
        from minios_utils import activate_kernel

        outside = tmp_path / 'kernel'
        outside.mkdir()
        os.symlink(str(outside), os.path.join(temp_minios_dir, 'kernels', 'new'))

        assert activate_kernel(temp_minios_dir, 'new') is False

    def test_marker_write_is_atomic_and_preserves_mode(self, temp_minios_dir):
        from minios_utils import activate_kernel

        marker = os.path.join(temp_minios_dir, 'boot', 'active-kernel')
        with open(marker, 'w') as fh:
            fh.write('old')
        os.chmod(marker, 0o640)
        for path in (
                os.path.join(temp_minios_dir, '01-kernel-old.sb'),
                os.path.join(temp_minios_dir, 'boot', 'vmlinuz-old'),
                os.path.join(temp_minios_dir, 'boot', 'initrfs-old.img')):
            with open(path, 'wb') as artifact:
                artifact.write(b'old')
        source_dir = os.path.join(temp_minios_dir, 'kernels', 'new')
        os.makedirs(source_dir)
        for name in ('01-kernel-new.sb', 'vmlinuz-new', 'initrfs-new.img'):
            open(os.path.join(source_dir, name), 'w').close()

        with patch('minios_utils.is_kernel_currently_running', return_value=False), \
             patch('minios_utils._update_bootloader_configs', return_value=True):
            assert activate_kernel(temp_minios_dir, 'new') is True

        assert open(marker).read() == 'new'
        assert os.stat(marker).st_mode & 0o777 == 0o640

    def test_marker_failure_rolls_back_files_bootloader_and_marker(self, temp_minios_dir):
        import minios_utils
        from minios_utils import activate_kernel

        old = 'old'
        new = 'new'
        marker = os.path.join(temp_minios_dir, 'boot', 'active-kernel')
        with open(marker, 'w') as marker_file:
            marker_file.write(old)
        grub_dir = os.path.join(temp_minios_dir, 'boot', 'grub')
        os.makedirs(grub_dir)
        grub = os.path.join(grub_dir, 'grub.cfg')
        with open(grub, 'w') as config:
            config.write('old config')

        for version, content in ((old, b'old'), (new, b'new')):
            if version == new:
                base = os.path.join(temp_minios_dir, 'kernels', version)
                os.makedirs(base)
                paths = (
                    os.path.join(base, '01-kernel-new.sb'),
                    os.path.join(base, 'vmlinuz-new'),
                    os.path.join(base, 'initrfs-new.img'))
            else:
                paths = (
                    os.path.join(temp_minios_dir, '01-kernel-old.sb'),
                    os.path.join(temp_minios_dir, 'boot', 'vmlinuz-old'),
                    os.path.join(temp_minios_dir, 'boot', 'initrfs-old.img'))
            for path in paths:
                with open(path, 'wb') as artifact:
                    artifact.write(content)

        real_atomic_write = minios_utils._atomic_write

        def fail_new_marker(path, content):
            if path == marker and content == new:
                raise OSError('marker write failed')
            return real_atomic_write(path, content)

        def update_bootloader(path, version):
            with open(grub, 'w') as config:
                config.write('new config')
            return True

        with patch('minios_utils.is_kernel_currently_running', return_value=False), \
             patch('minios_utils._update_bootloader_configs', side_effect=update_bootloader), \
             patch('minios_utils._atomic_write', side_effect=fail_new_marker):
            assert activate_kernel(temp_minios_dir, new) is False

        assert open(marker).read() == old
        assert open(grub).read() == 'old config'
        for path in (
                os.path.join(temp_minios_dir, '01-kernel-old.sb'),
                os.path.join(temp_minios_dir, 'boot', 'vmlinuz-old'),
                os.path.join(temp_minios_dir, 'boot', 'initrfs-old.img')):
            assert open(path, 'rb').read() == b'old'
        assert not os.path.exists(os.path.join(temp_minios_dir, '01-kernel-new.sb'))

    def test_subsequent_switch_accepts_only_identical_retained_bundle(self, temp_minios_dir):
        from minios_utils import activate_kernel

        old = 'old'
        new = 'new'
        with open(os.path.join(temp_minios_dir, 'boot', 'active-kernel'), 'w') as marker:
            marker.write(old)
        for path in (
                os.path.join(temp_minios_dir, '01-kernel-old.sb'),
                os.path.join(temp_minios_dir, 'boot', 'vmlinuz-old'),
                os.path.join(temp_minios_dir, 'boot', 'initrfs-old.img')):
            with open(path, 'wb') as artifact:
                artifact.write(b'old')
        new_repository = os.path.join(temp_minios_dir, 'kernels', new)
        os.makedirs(new_repository)
        for name in ('01-kernel-new.sb', 'vmlinuz-new', 'initrfs-new.img'):
            with open(os.path.join(new_repository, name), 'wb') as artifact:
                artifact.write(b'new')

        with patch('minios_utils.is_kernel_currently_running', return_value=False), \
             patch('minios_utils._update_bootloader_configs', return_value=True):
            assert activate_kernel(temp_minios_dir, new) is True
            assert activate_kernel(temp_minios_dir, old) is True

        assert open(os.path.join(temp_minios_dir, 'boot', 'active-kernel')).read() == old
        assert set(os.listdir(new_repository)) == {
            '01-kernel-new.sb', 'vmlinuz-new', 'initrfs-new.img'}

    @pytest.mark.parametrize('retained_state', ['incomplete', 'different'])
    def test_rejects_bad_retained_repository_copy(self, temp_minios_dir, retained_state):
        from minios_utils import deactivate_current_kernel

        version = 'active'
        with open(os.path.join(temp_minios_dir, 'boot', 'active-kernel'), 'w') as marker:
            marker.write(version)
        active_paths = (
            os.path.join(temp_minios_dir, '01-kernel-active.sb'),
            os.path.join(temp_minios_dir, 'boot', 'vmlinuz-active'),
            os.path.join(temp_minios_dir, 'boot', 'initrfs-active.img'))
        for path in active_paths:
            with open(path, 'wb') as artifact:
                artifact.write(b'active')
        repository = os.path.join(temp_minios_dir, 'kernels', version)
        os.makedirs(repository)
        names = ['01-kernel-active.sb', 'vmlinuz-active']
        if retained_state == 'different':
            names.append('initrfs-active.img')
        for name in names:
            with open(os.path.join(repository, name), 'wb') as artifact:
                artifact.write(b'different' if name.startswith('initrfs') else b'active')

        with patch('minios_utils.is_kernel_currently_running', return_value=False):
            assert deactivate_current_kernel(temp_minios_dir) is False
        assert all(os.path.exists(path) for path in active_paths)


class TestPrivatePackagingWorkspace:
    def test_custom_workspace_is_private_and_owned_by_backend_user(self, tmp_path):
        from minios_utils import get_temp_dir_with_space_check

        workspace = get_temp_dir_with_space_check(
            required_mb=0, custom_temp_dir=str(tmp_path))
        try:
            workspace_stat = os.lstat(workspace)
            assert workspace_stat.st_uid == os.geteuid()
            assert stat.S_IMODE(workspace_stat.st_mode) == 0o700
            assert not os.path.islink(workspace)
        finally:
            os.rmdir(workspace)

    def test_rejects_symlinked_custom_workspace_parent(self, tmp_path):
        from minios_utils import get_temp_dir_with_space_check

        real_parent = tmp_path / 'real'
        real_parent.mkdir()
        link = tmp_path / 'link'
        os.symlink(str(real_parent), str(link))
        with pytest.raises(RuntimeError, match='symlinked or untrusted'):
            get_temp_dir_with_space_check(
                required_mb=0, custom_temp_dir=str(link))

    def test_deactivation_rolls_back_partial_moves(self, temp_minios_dir):
        from minios_utils import deactivate_current_kernel

        version = 'old'
        paths = [
            os.path.join(temp_minios_dir, '01-kernel-old.sb'),
            os.path.join(temp_minios_dir, 'boot', 'vmlinuz-old'),
        ]
        for path in paths:
            open(path, 'w').close()
        with open(os.path.join(temp_minios_dir, 'boot', 'active-kernel'), 'w') as fh:
            fh.write(version)
        os.makedirs(os.path.join(temp_minios_dir, 'kernels', version))
        open(os.path.join(temp_minios_dir, 'kernels', version, '01-kernel-old.sb'), 'w').close()

        with patch('minios_utils.is_kernel_currently_running', return_value=False):
            assert deactivate_current_kernel(temp_minios_dir) is False
        assert all(os.path.exists(path) for path in paths)
    def test_bootloader_failure_rolls_back_new_files_and_restores_previous(self, temp_minios_dir):
        from minios_utils import activate_kernel

        old = 'old'
        new = 'new'
        old_files = [
            os.path.join(temp_minios_dir, '01-kernel-{}.sb'.format(old)),
            os.path.join(temp_minios_dir, 'boot', 'vmlinuz-{}'.format(old)),
            os.path.join(temp_minios_dir, 'boot', 'initrfs-{}.img'.format(old)),
        ]
        for path in old_files:
            open(path, 'w').close()
        with open(os.path.join(temp_minios_dir, 'boot', 'active-kernel'), 'w') as fh:
            fh.write(old)
        source_dir = os.path.join(temp_minios_dir, 'kernels', new)
        os.makedirs(source_dir)
        for name in ('01-kernel-{}.sb'.format(new), 'vmlinuz-{}'.format(new), 'initrfs-{}.img'.format(new)):
            open(os.path.join(source_dir, name), 'w').close()

        with patch('minios_utils.is_kernel_currently_running', return_value=False), \
             patch('minios_utils._update_bootloader_configs', return_value=False):
            assert activate_kernel(temp_minios_dir, new) is False

        assert all(os.path.exists(path) for path in old_files)
        assert not os.path.exists(os.path.join(temp_minios_dir, '01-kernel-{}.sb'.format(new)))


class TestFormatSize:
    """Tests for _format_size function."""

    def test_format_bytes(self):
        """Test formatting byte values."""
        from minios_utils import _format_size
        
        assert _format_size(500) == "500.0 B"

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
            mock_exists.side_effect = lambda p: False
            
            result = get_system_type()
            assert result in [
                'Live system (running from media)',
                'Installed system'
            ]


class TestGetUnionFilesystemType:
    """Tests for get_union_filesystem_type function."""

    def test_detect_overlayfs(self):
        """Test detecting OverlayFS."""
        from minios_utils import get_union_filesystem_type

        mount_output = "overlay on / type overlay (rw,relatime)\n"

        with patch('subprocess.run', return_value=MagicMock(stdout=mount_output, returncode=0)):
            result = get_union_filesystem_type()
            assert result == 'overlayfs'

    def test_detect_aufs(self):
        """Test detecting AUFS."""
        from minios_utils import get_union_filesystem_type

        mount_output = "none on / type aufs (rw,relatime)\n"

        with patch('subprocess.run', return_value=MagicMock(stdout=mount_output, returncode=0)):
            result = get_union_filesystem_type()
            assert result == 'aufs'


class TestCliHelp:
    """Tests for help output."""

    def test_main_help_does_not_require_root(self, capsys):
        """Test that CLI help is available without root privileges."""
        from minios_kernel import main

        with patch.object(sys, 'argv', ['minios-kernel', '--help']), \
             patch('os.geteuid', return_value=1000), \
             pytest.raises(SystemExit) as exc:
            main()

        captured = capsys.readouterr()

        assert exc.value.code == 0
        assert 'usage:' in captured.out
        assert 'delete' in captured.out
        assert 'requires root privileges' not in captured.err

    def test_gui_launcher_help(self):
        """Test that GUI launcher prints a usage message."""
        launcher = os.path.join(os.path.dirname(__file__), '..', 'bin', 'minios-kernel-manager')

        result = subprocess.run(
            [launcher, '--help'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )

        assert result.returncode == 0
        assert 'Usage: minios-kernel-manager' in result.stdout
        assert 'minios-kernel --help' in result.stdout
        assert result.stderr == ''
