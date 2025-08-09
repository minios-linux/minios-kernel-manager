#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build utilities for MiniOS Kernel Manager
Handles SquashFS creation, initramfs generation, and file operations
"""

import os
import sys
import subprocess
import shutil
import glob
import tempfile
import gettext
from typing import Optional, Callable

# Handle imports based on whether we're installed or running from source
if os.path.exists('/usr/lib/minios-kernel-manager'):
    from compression_utils import get_compression_params
    from kernel_utils import get_non_symlink_modules_dir
    from minios_utils import get_temp_dir_with_space_check
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from compression_utils import get_compression_params
    from kernel_utils import get_non_symlink_modules_dir
    from minios_utils import get_temp_dir_with_space_check

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext


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
        raise RuntimeError(_("vmlinuz for kernel {kernel_version} not found").format(kernel_version=kernel_version))
    
    shutil.copy2(vmlinuz_path, output_path)
    return output_path


def create_squashfs_image(kernel_version: str, compression: str, output_dir: str, 
                         logger: Optional[Callable] = None, temp_dir: str = None) -> str:
    """Create SquashFS image of kernel modules
    
    Args:
        kernel_version: Version of the kernel
        compression: Compression algorithm to use
        output_dir: Directory to save the output file
        logger: Optional logging function
        temp_dir: Temporary directory with extracted deb contents (if None, uses system modules)
    """
    output_image = os.path.join(output_dir, f"01-kernel-{kernel_version}.sb")
    
    # Remove existing image
    if os.path.exists(output_image):
        os.remove(output_image)
    
    temp_squashfs_dir = None  # Track temporary directory for cleanup
    
    if temp_dir and os.path.exists(temp_dir):
        # Find modules directory in extracted deb contents
        possible_modules_paths = [
            os.path.join(temp_dir, "usr", "lib", "modules", kernel_version),
            os.path.join(temp_dir, "lib", "modules", kernel_version)
        ]
        
        modules_path = None
        modules_base = None
        for path in possible_modules_paths:
            if os.path.exists(path):
                modules_path = path
                if "usr/lib/modules" in path:
                    modules_base = "usr/lib/modules"
                else:
                    modules_base = "lib/modules"
                break
        
        if not modules_path:
            raise RuntimeError(_("Kernel modules not found in extracted deb package for kernel {kernel_version}").format(kernel_version=kernel_version))
        
        # Create temporary structure with proper paths for SquashFS
        temp_squashfs_dir = get_temp_dir_with_space_check(200, f"squashfs_{kernel_version}_")  # SquashFS needs less space than full packaging
        target_modules_dir = os.path.join(temp_squashfs_dir, modules_base)
        os.makedirs(target_modules_dir, exist_ok=True)
        
        # Copy modules to proper structure
        shutil.copytree(modules_path, os.path.join(target_modules_dir, kernel_version))
        
        source_path = temp_squashfs_dir
        print(f"I: {_('Using extracted deb modules with structure: {path}').format(path=f'{temp_squashfs_dir}/{modules_base}/{kernel_version}')}")
    else:
        # Fallback: use system modules directory
        modules_dir = get_non_symlink_modules_dir()
        kernel_modules_path = os.path.join(modules_dir, kernel_version)
        
        if not os.path.exists(kernel_modules_path):
            raise RuntimeError(_("Kernel modules directory not found: {path}").format(path=kernel_modules_path))
        
        source_path = kernel_modules_path
        print(f"I: {_('Using system modules directory: {path}').format(path=source_path)}")
    
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
        'mksquashfs', source_path, output_image,
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
    print(f"I: {_('Starting SquashFS compression with {compression}...').format(compression=compression)}", flush=True)
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    if logger:
        for line in iter(process.stdout.readline, ''):
            if line.strip():
                logger(line.strip())
    else:
        # Show progress for standalone execution
        for line in iter(process.stdout.readline, ''):
            if line.strip() and ('[' in line and ']' in line):
                # This is a progress line from mksquashfs
                print(f"\r{line.strip()}", end="", flush=True)
    
    process.wait()
    if process.returncode != 0:
        # Cleanup temporary directory if it was created
        if temp_squashfs_dir and os.path.exists(temp_squashfs_dir):
            shutil.rmtree(temp_squashfs_dir)
        raise RuntimeError(_("Failed to create SquashFS image"))
    
    print(f"\nI: {_('SquashFS image completed: {path}').format(path=output_image)}", flush=True)
    
    # Cleanup temporary directory if it was created
    if temp_squashfs_dir and os.path.exists(temp_squashfs_dir):
        shutil.rmtree(temp_squashfs_dir)
    
    return output_image


def generate_initramfs(kernel_version: str, compression: str, output_dir: str, logger: Optional[Callable] = None, temp_dir: str = None) -> str:
    """Generate initramfs image"""
    output_image = os.path.join(output_dir, f"initrfs-{kernel_version}.img")
    
    # Get modules directory
    modules_dir = get_non_symlink_modules_dir()
    
    # Check if mkinitrfs exists
    mkinitrfs_path = "/run/initramfs/mkinitrfs"
    if not os.path.exists(mkinitrfs_path):
        raise RuntimeError(_("mkinitrfs not found - this tool requires MiniOS live environment"))
    
    # Handle module path for extracted deb packages
    system_modules_path = os.path.join(modules_dir, kernel_version)
    temp_symlink_created = False
    
    if temp_dir and os.path.exists(temp_dir):
        # Find modules directory in extracted deb contents
        possible_modules_paths = [
            os.path.join(temp_dir, "usr", "lib", "modules", kernel_version),
            os.path.join(temp_dir, "lib", "modules", kernel_version)
        ]
        
        extracted_modules_path = None
        for path in possible_modules_paths:
            if os.path.exists(path):
                extracted_modules_path = path
                break
        
        if extracted_modules_path:
            # Create or recreate temporary symlink for mkinitrfs
            try:
                os.makedirs(modules_dir, exist_ok=True)
                
                # Handle existing path
                should_create_symlink = True
                
                if os.path.islink(system_modules_path):
                    # Remove existing symlink
                    os.remove(system_modules_path)
                    print(f"I: {_('Removed existing symlink: {}').format(system_modules_path)}")
                elif os.path.exists(system_modules_path):
                    # Path exists and it's not a symlink (probably a real directory)
                    print(f"I: {_('Using existing modules directory: {}').format(system_modules_path)}")
                    should_create_symlink = False
                    temp_symlink_created = False
                
                # Create symlink only if path is free
                if should_create_symlink:
                    os.symlink(extracted_modules_path, system_modules_path)
                    temp_symlink_created = True
                    print(f"I: {_('Created temporary symlink: {system_path} -> {extracted_path}').format(system_path=system_modules_path, extracted_path=extracted_modules_path)}")
            except OSError as e:
                print(f"Warning: Failed to create symlink {system_modules_path}: {e}")
    
    # Store symlink info for cleanup
    cleanup_symlink = system_modules_path if temp_symlink_created else None
    
    # Execute mkinitrfs with default parameters: -k KERNEL -n -c -dm
    cmd = [mkinitrfs_path, "-k", kernel_version, "-n", "-c", "-dm"]

    # Add config file path if available
    if temp_dir:
        config_path = os.path.join(temp_dir, "boot", f"config-{kernel_version}")
        if os.path.exists(config_path):
            cmd.extend(["--config-file", config_path])
    
    # Run mkinitrfs with real-time output
    print(f"I: {_('Starting initramfs generation...')}", flush=True)
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                 text=True, bufsize=1, universal_newlines=True)
        
        output_lines = []
        while True:
            line = process.stdout.readline()
            if not line:
                break
            output_lines.append(line)
            
            # Show meaningful progress lines
            line_stripped = line.strip()
            if line_stripped and not line_stripped.startswith('+'):  # Skip debug lines
                if any(keyword in line_stripped.lower() for keyword in 
                      ['copying', 'creating', 'compressing', 'blocks']):
                    print(f"I: {line_stripped}", flush=True)
        
        process.wait()
        if process.returncode != 0:
            raise RuntimeError(_("mkinitrfs failed with return code {code}").format(code=process.returncode))
        
        output = ''.join(output_lines)
        
    except Exception as e:
        if hasattr(e, 'returncode'):
            raise RuntimeError(_("mkinitrfs failed: {error}").format(error=e))
        else:
            raise RuntimeError(_("mkinitrfs error: {error}").format(error=e))
    
    # Always show some output in logger if provided
    if logger:
        for line in output.splitlines():
            line_stripped = line.strip()
            if line_stripped and not line_stripped.startswith('+'):
                logger(line_stripped)
    
    # Parse output to find initramfs path
    # mkinitrfs outputs the path as the last meaningful line (ends with .img)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    temp_initramfs_path = None
    
    # Find the last line that looks like an initramfs path
    for line in reversed(lines):
        if line.endswith('.img') and os.path.exists(line):
            temp_initramfs_path = line
            break
    
    if not temp_initramfs_path:
        # Debug: show actual output
        print(f"I: {_('mkinitrfs output was:')}", flush=True)
        for i, line in enumerate(lines, 1):
            print(f"I: {i}: {line}", flush=True)
        raise RuntimeError(_("mkinitrfs did not return valid initramfs path"))
    
    # Copy to final location  
    print(f"I: {_('Copying initramfs from {path}').format(path=temp_initramfs_path)}", flush=True)
    shutil.copy2(temp_initramfs_path, output_image)
    print(f"I: {_('Initramfs created: {path}').format(path=output_image)}", flush=True)
    
    # Clean up temporary initramfs file
    try:
        os.remove(temp_initramfs_path)
    except OSError:
        pass  # Ignore cleanup errors
    
    # Clean up temporary symlink if created
    if cleanup_symlink and os.path.islink(cleanup_symlink):
        try:
            os.remove(cleanup_symlink)
            print(f"I: {_('Removed temporary symlink: {path}').format(path=cleanup_symlink)}")
        except OSError as e:
            print(f"Warning: Failed to remove temporary symlink {cleanup_symlink}: {e}")
    
    # Copy log if available
    log_source = os.path.join(os.path.dirname(temp_initramfs_path), 
                             "..", "livekit", "initramfs.log")
    if os.path.exists(log_source):
        log_dest = os.path.join(output_dir, f"initramfs-{kernel_version}.log")
        shutil.copy2(log_source, log_dest)
    
    return output_image