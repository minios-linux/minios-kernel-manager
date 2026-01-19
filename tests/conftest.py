#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pytest fixtures for minios-kernel-manager tests.
"""

import sys
import os
import pytest
import tempfile
import shutil
from unittest.mock import MagicMock, patch

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))


@pytest.fixture
def temp_minios_dir():
    """Create a temporary MiniOS directory structure for testing."""
    tmpdir = tempfile.mkdtemp(prefix="minios-test-")
    
    # Create typical MiniOS structure
    os.makedirs(os.path.join(tmpdir, "boot"))
    os.makedirs(os.path.join(tmpdir, "01-kernel"))
    os.makedirs(os.path.join(tmpdir, "02-firmware"))
    os.makedirs(os.path.join(tmpdir, "kernels"))
    
    yield tmpdir
    
    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_modules_dir():
    """Create a temporary /lib/modules directory structure."""
    tmpdir = tempfile.mkdtemp(prefix="minios-modules-")
    
    # Create sample kernel module directories
    for version in ["6.1.0-1", "6.1.0-2", "6.5.0-1"]:
        os.makedirs(os.path.join(tmpdir, version))
    
    yield tmpdir
    
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sample_apt_cache_show():
    """Sample apt-cache show output."""
    return '''Package: linux-image-6.1.0-18-amd64
Version: 6.1.76-1
Installed-Size: 72692
Maintainer: Debian Kernel Team <debian-kernel@lists.debian.org>
Architecture: amd64
Provides: linux-image, linux-image-6.1.0-18-amd64-unsigned
Depends: kmod, linux-base (>= 4.3~), initramfs-tools (>= 0.120+deb8u2) | linux-initramfs-tool
Size: 68891972
Description: Linux 6.1 for 64-bit PCs (signed)
'''


@pytest.fixture  
def sample_apt_cache_search():
    """Sample apt-cache search output."""
    return '''linux-image-6.1.0-18-amd64 - Linux 6.1 for 64-bit PCs (signed)
linux-image-6.1.0-17-amd64 - Linux 6.1 for 64-bit PCs (signed)
linux-image-6.5.0-1-amd64 - Linux 6.5 for 64-bit PCs (signed)
linux-image-6.1.0-18-amd64-dbg - Debug symbols for linux-image-6.1.0-18-amd64
'''
