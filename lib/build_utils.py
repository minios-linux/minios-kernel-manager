#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build utilities for MiniOS KernelPack GUI
Handles SquashFS creation, initramfs generation, and file operations
"""

import os
import sys
import subprocess
import shutil
import glob
from typing import Optional, Callable

# Handle imports based on whether we're installed or running from source
if os.path.exists('/usr/lib/minios-kernel-manager'):
    from compression_utils import get_compression_params
    from kernel_utils import get_non_symlink_modules_dir
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from compression_utils import get_compression_params
    from kernel_utils import get_non_symlink_modules_dir


def copy_vmlinuz(kernel_version: str, temp_dir: str, output_dir: str, kernel_source: str = "local") -> str:
    """Copy vmlinuz file for the selected kernel"""
    output_path = os.path.join(output_dir, f"vmlinuz-{kernel_version}")
    
    # Search paths for vmlinuz
    search_paths = [
        os.path.join(temp_dir, "boot", f"vmlinuz-{kernel_version}"),
        f"/boot/vmlinuz-{kernel_version}",
        f"/run/initramfs/memory/data/minios/boot/vmlinuz-{kernel_version}",
        f"/run/initramfs/memory/data/minios/boot/vmlinuz",
        f"/run/initramfs/memory/toram/minios/boot/vmlinuz-{kernel_version}",
        f"/run/initramfs/memory/toram/minios/boot/vmlinuz"
    ]
    
    vmlinuz_path = None
    for path in search_paths:
        if os.path.exists(path):
            vmlinuz_path = path
            break
    
    if not vmlinuz_path:
        raise RuntimeError(f"vmlinuz for kernel {kernel_version} not found")
    
    shutil.copy2(vmlinuz_path, output_path)
    return output_path


def create_squashfs_image(kernel_version: str, compression: str, output_dir: str, logger: Optional[Callable] = None) -> str:
    """Create SquashFS image of kernel modules"""
    output_image = os.path.join(output_dir, f"01-kernel-{kernel_version}.sb")
    
    # Remove existing image
    if os.path.exists(output_image):
        os.remove(output_image)
    
    # Get modules directory
    modules_dir = get_non_symlink_modules_dir()
    kernel_modules_path = os.path.join(modules_dir, kernel_version)
    
    if not os.path.exists(kernel_modules_path):
        raise RuntimeError(f"Kernel modules directory not found: {kernel_modules_path}")
    
    # Get compression parameters
    comp_params = get_compression_params(compression, 'squashfs')
    
    # Check mksquashfs version for -no-strip support
    try:
        result = subprocess.run(['mksquashfs', '-version'], 
                              capture_output=True, text=True, check=True)
        version_line = result.stderr.split('\n')[0]  # Version info is in stderr
        version_str = version_line.split()[-1]  # Get version number
        major, minor = map(int, version_str.split('.')[:2])
        use_no_strip = (major > 4) or (major == 4 and minor >= 5)
    except (subprocess.CalledProcessError, ValueError, IndexError):
        use_no_strip = False
    
    # Build mksquashfs command
    cmd = [
        'mksquashfs', kernel_modules_path, output_image,
        '-comp', compression
    ]
    
    if comp_params:
        cmd.extend(comp_params.split())
    
    cmd.extend([
        '-b', '1024K',
        '-always-use-fragments',
        '-noappend',
        '-quiet'
    ])
    
    if use_no_strip:
        cmd.append('-no-strip')
    
    # Execute mksquashfs
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    if logger:
        for line in iter(process.stdout.readline, ''):
            logger(line.strip())
    
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Failed to create SquashFS image")
    
    return output_image


def generate_initramfs(kernel_version: str, compression: str, output_dir: str, logger: Optional[Callable] = None) -> str:
    """Generate initramfs image"""
    output_image = os.path.join(output_dir, f"initrfs-{kernel_version}.img")
    
    # Get modules directory
    modules_dir = get_non_symlink_modules_dir()
    
    # Check if mkinitrfs exists
    mkinitrfs_path = "/run/initramfs/mkinitrfs"
    if not os.path.exists(mkinitrfs_path):
        raise RuntimeError("mkinitrfs not found - this tool requires MiniOS live environment")
    
    # Execute mkinitrfs with default parameters: -k KERNEL -n -c -dm
    cmd = [mkinitrfs_path, "-k", kernel_version, "-n", "-c", "-dm"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    output_lines = []
    if logger:
        for line in iter(process.stdout.readline, ''):
            logger(line.strip())
            output_lines.append(line)
    
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Failed to generate initramfs")

    temp_initramfs_path = "".join(output_lines).strip().split('\n')[-1]
    if not temp_initramfs_path or not os.path.exists(temp_initramfs_path):
        raise RuntimeError(f"mkinitrfs did not return valid initramfs path")
    
    # Copy to final location  
    shutil.copy2(temp_initramfs_path, output_image)
    
    # Copy log if available
    log_source = os.path.join(os.path.dirname(temp_initramfs_path), 
                             "..", "livekit", "initramfs.log")
    if os.path.exists(log_source):
        log_dest = os.path.join(output_dir, f"initramfs-{kernel_version}.log")
        shutil.copy2(log_source, log_dest)
    
    return output_image