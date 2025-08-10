#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compression utilities for MiniOS Kernel Manager
Handles compression method detection and parameter configuration
"""

import shutil
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


def get_available_compressions() -> List[str]:
    """Get list of available compression methods"""
    available = []
    
    for compression, tool in COMPRESSION_TOOLS.items():
        if shutil.which(tool):
            available.append(compression)
    
    # Sort by speed (fastest first)
    sorted_available = []
    for method in SPEED_ORDER:
        if method in available:
            sorted_available.append(method)
    
    return sorted_available


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