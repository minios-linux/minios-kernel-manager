#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compression utilities for MiniOS Kernel Manager
Handles compression method detection and parameter configuration
"""

import os
import re
import shutil
import subprocess
from typing import List, Dict, Tuple


# Compression tools mapping
COMPRESSION_TOOLS = {
    'lz4': 'lz4',
    'lzo': 'lzop', 
    'gzip': 'gzip',
    'zstd': 'zstd',
    'lzma': 'lzma',
    'xz': 'xz',
    'bzip2': 'bzip2'
}

# SquashFS compression parameters
SQFS_COMPRESSION_PARAMS = {
    'lz4': '-Xhc',
    'lzo': '',
    'gzip': '-Xcompression-level 9',
    'zstd': '-Xcompression-level 19',
    'lzma': '-Xdict-size 1M',
    'xz': '-Xbcj x86',
    'bzip2': '-Xblock-size 256K'
}

# Speed order (fastest to slowest)
SPEED_ORDER = ['lz4', 'lzo', 'gzip', 'zstd', 'lzma', 'xz', 'bzip2']


def get_mksquashfs_compressions() -> List[str]:
    """Return compressors advertised by the installed mksquashfs binary."""
    if not shutil.which('mksquashfs'):
        return []

    env = os.environ.copy()
    env['LC_ALL'] = 'C'
    env['LANG'] = 'C'
    env['LANGUAGE'] = 'C'
    try:
        result = subprocess.run(
            ['mksquashfs', '-help'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
            env=env,
        )
    except OSError:
        return []

    help_text = '{}\n{}'.format(result.stdout or '', result.stderr or '')
    advertised = []
    for method in SPEED_ORDER:
        if re.search(r'^\s*{}(?:\s|\(|$)'.format(re.escape(method)),
                     help_text, re.MULTILINE):
            advertised.append(method)
    return advertised


def get_available_compressions() -> List[str]:
    """Get compressors supported by the actual SquashFS encoder."""
    return get_mksquashfs_compressions()


def get_compression_params(compression: str, image_type: str = 'squashfs') -> str:
    """Get compression parameters for given method and image type"""
    if image_type == 'squashfs':
        return SQFS_COMPRESSION_PARAMS.get(compression, '')
    else:
        # For initramfs, no special parameters needed
        return ''


def get_compression_description(compression: str) -> str:
    """Get human-readable description of compression method"""
    descriptions = {
        'lz4': 'Extreme speed, low compression ratio',
        'lzo': 'Very fast, low compression ratio', 
        'gzip': 'Fast, moderate compression ratio',
        'zstd': 'Balanced speed and compression',
        'lzma': 'Slow, high compression ratio',
        'xz': 'Slowest, highest compression ratio',
        'bzip2': 'Very slow, slightly better than xz'
    }
    return descriptions.get(compression, 'Unknown compression method')
