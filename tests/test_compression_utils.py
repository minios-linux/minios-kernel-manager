#!/usr/bin/env python3

from unittest.mock import MagicMock, patch


def test_available_compressions_come_from_mksquashfs_advertisement():
    from compression_utils import get_available_compressions

    help_output = '''
Compressors available:
    gzip (default)
    lz4
    xz
    zstd
'''
    result = MagicMock(stdout='', stderr=help_output, returncode=0)
    with patch('compression_utils.shutil.which',
               side_effect=lambda tool: '/usr/bin/' + tool), \
         patch('compression_utils.subprocess.run', return_value=result) as run:
        assert get_available_compressions() == ['lz4', 'gzip', 'zstd', 'xz']

    assert run.call_args[0][0] == ['mksquashfs', '-help']


def test_unadvertised_external_helper_does_not_enable_compressor():
    from compression_utils import get_available_compressions

    result = MagicMock(
        stdout='Compressors available:\n    gzip (default)\n',
        stderr='', returncode=0)
    with patch('compression_utils.shutil.which', return_value='/usr/bin/tool'), \
         patch('compression_utils.subprocess.run', return_value=result):
        assert get_available_compressions() == ['gzip']
