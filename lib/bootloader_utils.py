#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bootloader configuration utilities for MiniOS Kernel Manager
Handles updating syslinux and grub configuration files
"""

import os
import re
import shutil
import gettext
import stat
import tempfile
from typing import Optional, List

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext


def _trusted_directory(path: str) -> bool:
    """Reject symlinked configuration roots and parents before writing."""
    path = os.path.abspath(path)
    current = os.path.sep
    for component in path.strip(os.path.sep).split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            return False
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            return False
    return True


def _atomic_write_config(path: str, content, encoding: str = None) -> None:
    """Atomically replace a regular config without following a symlink."""
    parent = os.path.dirname(path)
    if not _trusted_directory(parent):
        raise RuntimeError('Untrusted boot configuration directory')
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except OSError:
        raise RuntimeError('Boot configuration is missing or untrusted')
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeError('Boot configuration is not a regular file')

    fd, temporary = tempfile.mkstemp(prefix='.minios-kernel-', dir=parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

def update_syslinux_config(minios_path: str, kernel_version: str) -> bool:
    """
    Update syslinux.cfg to use the new kernel
    Returns True if updated or if file doesn't exist (optional)
    """
    def _update_syslinux_file(config_file: str, version: str) -> bool:
        try:
            if not os.path.exists(config_file):
                return True

            if os.path.islink(config_file) or not os.path.isfile(config_file):
                raise RuntimeError('SYSLINUX configuration is not a regular file')
            with open(config_file, 'rb') as f:
                raw_data = f.read()

            content = None
            detected_encoding = None
            for encoding in ['utf-8', 'cp866', 'iso-8859-1']:
                try:
                    content = raw_data.decode(encoding)
                    detected_encoding = encoding
                    break
                except UnicodeDecodeError:
                    continue

            if content is None:
                detected_encoding = 'latin-1'
                content = raw_data.decode(detected_encoding)

            new_content = re.sub(r'(KERNEL\s+/minios/boot/)vmlinuz-[^\s]+', f'\\1vmlinuz-{version}', content)
            new_content = re.sub(r'(initrd=/minios/boot/)initrfs-[^\s]+', f'\\1initrfs-{version}.img', new_content)

            if new_content != content:
                _atomic_write_config(config_file, new_content, detected_encoding)
                print(f"I: {_('Updated SYSLINUX config: {}').format(config_file)}")

            return True
        except Exception as e:
            print(f"W: {_('Failed to update SYSLINUX config {}: {}').format(config_file, e)}")
            return False

    success = True
    syslinux_dir = os.path.join(minios_path, 'boot', 'syslinux')
    syslinux_cfg = os.path.join(syslinux_dir, 'syslinux.cfg')

    if os.path.exists(syslinux_cfg):
        success &= _update_syslinux_file(syslinux_cfg, kernel_version)

    lang_dir = os.path.join(syslinux_dir, 'lang')
    if os.path.exists(lang_dir):
        for lang_file in os.listdir(lang_dir):
            if lang_file.endswith('.cfg'):
                success &= _update_syslinux_file(os.path.join(lang_dir, lang_file), kernel_version)

    return success

def find_grub_config_files(minios_path: str) -> List[str]:
    """
    Find all GRUB configuration files that may contain boot commands:
    - main.cfg (main config for multilingual configuration)
    - grub.multilang.cfg (multilingual configuration)
    - grub.template.cfg (template configuration)  
    - grub.cfg (active configuration)
    """
    grub_dir = os.path.join(minios_path, "boot", "grub")
    config_files = []
    
    # Check for all possible GRUB config files
    for config_name in ["main.cfg", "grub.multilang.cfg", "grub.template.cfg", "grub.cfg"]:
        config_path = os.path.join(grub_dir, config_name)
        if os.path.exists(config_path):
            config_files.append(config_path)
    
    return config_files

def find_grub_config_file(minios_path: str) -> Optional[str]:
    """
    Find the primary GRUB configuration file with priority:
    1. main.cfg 
    2. grub.cfg
    
    This function is kept for backward compatibility.
    For updating all configs, use find_grub_config_files() instead.
    """
    grub_dir = os.path.join(minios_path, "boot", "grub")

    # Priority 1: main.cfg
    main_cfg = os.path.join(grub_dir, "main.cfg")
    if os.path.exists(main_cfg):
        return main_cfg

    # Priority 2: grub.cfg
    grub_cfg = os.path.join(grub_dir, "grub.cfg")
    if os.path.exists(grub_cfg):
        return grub_cfg
        
    return None

def update_grub_config(minios_path: str, kernel_version: str) -> bool:
    """
    Update all GRUB configuration files to use the new kernel
    Handles:
    - Direct linux/initrd commands  
    - search --set -f patterns
    - All other kernel/initrd file references
    - Processes main.cfg, grub.multilang.cfg, grub.template.cfg, and grub.cfg if they exist
    """
    try:
        config_files = find_grub_config_files(minios_path)
        
        if not config_files:
            print(f"W: {_('No GRUB configuration files found')}")
            return False
        
        success = True
        updated_files = []
        
        for config_file in config_files:
            try:
                if os.path.islink(config_file) or not os.path.isfile(config_file):
                    raise RuntimeError('GRUB configuration is not a regular file')
                with open(config_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                original_content = content
                
                # Update direct linux/initrd commands
                content = re.sub(
                    r'linux /minios/boot/vmlinuz[^\s]*',
                    f'linux /minios/boot/vmlinuz-{kernel_version}',
                    content
                )
                content = re.sub(
                    r'initrd /minios/boot/initrfs[^\s]*\.img',
                    f'initrd /minios/boot/initrfs-{kernel_version}.img',
                    content
                )
                
                # Update search --set -f patterns (for main.cfg)
                content = re.sub(
                    r'search --set -f /minios/boot/vmlinuz[^\s]*',
                    f'search --set -f /minios/boot/vmlinuz-{kernel_version}',
                    content
                )
                
                # Update all other vmlinuz/initrfs references
                content = re.sub(
                    r'/minios/boot/vmlinuz[^\s]*(?=\s)',
                    f'/minios/boot/vmlinuz-{kernel_version}',
                    content
                )
                content = re.sub(
                    r'/minios/boot/initrfs[^\s]*\.img',
                    f'/minios/boot/initrfs-{kernel_version}.img',
                    content
                )
                
                # Only write if content changed
                if content != original_content:
                    _atomic_write_config(config_file, content, 'utf-8')
                    updated_files.append(os.path.basename(config_file))
                
            except Exception as e:
                print(f"W: {_('Failed to update GRUB config {}: {}').format(os.path.basename(config_file), e)}")
                success = False
        
        if updated_files:
            print(f"I: {_('Updated GRUB configs: {}').format(', '.join(updated_files))}")
        else:
            print(f"I: {_('No GRUB config changes needed')}")
        
        return success
        
    except Exception as e:
        print(f"E: {_('Error updating grub configs: {error}').format(error=e)}")
        return False

def update_bootloader_configs(minios_path: str, kernel_version: str) -> bool:
    """
    Update all bootloader configurations for the new kernel
    SYSLINUX is optional, GRUB is required
    """
    success = True
    
    # Update GRUB (required)
    if not update_grub_config(minios_path, kernel_version):
        success = False
    
    # Update SYSLINUX (optional)
    if not update_syslinux_config(minios_path, kernel_version):
        success = False
    
    if success and not verify_bootloader_postcondition(minios_path, kernel_version):
        return False
    return success


def verify_bootloader_postcondition(minios_path: str, kernel_version: str) -> bool:
    """Require every concrete boot reference to select the exact new pair."""
    expected_kernel = '/minios/boot/vmlinuz-{}'.format(kernel_version)
    expected_initrd = '/minios/boot/initrfs-{}.img'.format(kernel_version)
    references = []
    config_files = find_grub_config_files(minios_path)
    syslinux_dir = os.path.join(minios_path, 'boot', 'syslinux')
    for path in (os.path.join(syslinux_dir, 'syslinux.cfg'),):
        if os.path.exists(path):
            config_files.append(path)
    language_dir = os.path.join(syslinux_dir, 'lang')
    if os.path.isdir(language_dir):
        config_files.extend(os.path.join(language_dir, name)
                            for name in os.listdir(language_dir)
                            if name.endswith('.cfg'))
    try:
        for path in config_files:
            if os.path.islink(path) or not os.path.isfile(path):
                return False
            with open(path, 'r', encoding='utf-8', errors='replace') as config:
                content = config.read()
            kernels = re.findall(r'/minios/boot/vmlinuz[^\s"\']*', content)
            initrds = re.findall(r'/minios/boot/initrfs[^\s"\']*\.img', content)
            references.extend(kernels + initrds)
            if any(value != expected_kernel for value in kernels):
                return False
            if any(value != expected_initrd for value in initrds):
                return False
    except OSError:
        return False
    return (expected_kernel in references and expected_initrd in references)
