#!/usr/bin/env python3
"""Focused tests for crypto-aware initramfs generation."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))


class TestCryptoCapability:
    def test_uses_the_initrd_capability_marker_only(self):
        from build_utils import INITRD_CRYPTO_CAPABILITY_MARKER, running_initrd_has_crypto_capability

        with patch('os.path.exists', side_effect=lambda path: path == INITRD_CRYPTO_CAPABILITY_MARKER):
            assert running_initrd_has_crypto_capability() is True

    def test_crypto_is_not_enabled_without_marker(self):
        from build_utils import running_initrd_has_crypto_capability

        with patch('os.path.exists', return_value=False):
            assert running_initrd_has_crypto_capability() is False

    def test_rejects_active_encrypted_persistence_without_dm_crypt(self):
        from build_utils import generate_initramfs

        with patch('build_utils.detect_initramfs_builder', return_value='livekit'), \
             patch('build_utils.get_non_symlink_modules_dir', return_value='/lib/modules'), \
             patch('build_utils.running_initrd_has_crypto_capability', return_value=True), \
             patch('build_utils.encrypted_persistence_is_active', return_value=True), \
             patch('build_utils.kernel_supports_dm_crypt', return_value=False):
            with pytest.raises(RuntimeError, match='encrypted persistence'):
                generate_initramfs('6.1-test', '/output')


class TestCryptoBuilderArguments:
    def test_mkdracut_receives_crypt_only_when_capable(self):
        from build_utils import _generate_initramfs_dracut

        output_image = '/output/initrfs-test.img'
        process = MagicMock()
        process.stdout.readline.side_effect = ['', '']
        process.returncode = 0
        with patch('os.path.exists', side_effect=lambda path: path in ('/run/initramfs/dracut-mos/mkdracut', output_image)), \
             patch('subprocess.Popen', return_value=process) as popen:
            _generate_initramfs_dracut('test', 'test', output_image, '/lib/modules', crypto_capable=True)

        assert '--crypt' in popen.call_args.args[0]

    def test_mkinitrfs_omits_crypt_when_not_capable(self):
        from build_utils import _generate_initramfs_livekit

        process = MagicMock()
        process.stdout.readline.side_effect = ['/tmp/initrfs-test.img\n', '']
        process.returncode = 0
        exists = lambda path: path in ('/run/initramfs/mkinitrfs', '/tmp/initrfs-test.img')
        with patch('os.path.exists', side_effect=exists), \
             patch('shutil.copy2'), patch('os.remove'), \
             patch('subprocess.Popen', return_value=process) as popen:
            _generate_initramfs_livekit('test', 'test', '/output/initrfs-test.img', '/lib/modules', crypto_capable=False)

        assert '--crypt' not in popen.call_args.args[0]


class TestCryptoInitramfsValidation:
    def test_lsinitramfs_requires_cryptsetup_and_dm_crypt_in_output(self):
        from build_utils import validate_crypto_initramfs

        result = MagicMock(returncode=0, stdout='usr/sbin/cryptsetup\n', stderr='')
        with patch('shutil.which', side_effect=lambda tool: '/usr/bin/lsinitramfs' if tool == 'lsinitramfs' else None), \
             patch('subprocess.run', return_value=result) as run:
            with pytest.raises(RuntimeError, match='lacks its crypto marker'):
                validate_crypto_initramfs('/output/initrfs-test.img')
        assert run.call_args.args[0][0] == 'lsinitramfs'

    def test_lsinitramfs_accepts_crypto_capable_output(self):
        from build_utils import validate_crypto_initramfs

        result = MagicMock(
            returncode=0,
            stdout='etc/minios-initramfs-crypt\nusr/sbin/cryptsetup\nlib/modules/dm-crypt.ko\n',
            stderr='',
        )
        with patch('shutil.which', side_effect=lambda tool: '/usr/bin/lsinitramfs' if tool == 'lsinitramfs' else None), \
             patch('subprocess.run', return_value=result):
            validate_crypto_initramfs('/output/initrfs-test.img')

    def test_dracut_uses_lsinitrd_when_available(self):
        from build_utils import validate_crypto_initramfs

        result = MagicMock(
            returncode=0,
            stdout='etc/minios-initramfs-crypt\nusr/sbin/cryptsetup\nlib/modules/dm-crypt.ko\n',
            stderr='',
        )
        with patch('shutil.which', side_effect=lambda tool: '/usr/bin/{}'.format(tool)), \
             patch('subprocess.run', return_value=result) as run:
            validate_crypto_initramfs('/output/initrfs-test.img', 'dracut')
        assert run.call_args.args[0][0] == 'lsinitrd'

    def test_fails_clearly_without_an_initrd_inspector(self):
        from build_utils import validate_crypto_initramfs

        with patch('shutil.which', return_value=None):
            with pytest.raises(RuntimeError, match='neither lsinitramfs nor lsinitrd is available'):
                validate_crypto_initramfs('/output/initrfs-test.img')
