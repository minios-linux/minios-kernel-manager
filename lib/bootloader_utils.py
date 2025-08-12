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
from typing import Optional

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext

def update_syslinux_config(minios_path: str, kernel_version: str) -> bool:
    """
    Update syslinux.cfg to use the new kernel
    Returns True if updated or if file doesn't exist (optional)
    """
    try:
        syslinux_cfg = os.path.join(minios_path, "boot", "syslinux.cfg")
        
        # SYSLINUX is optional - return True if not present
        if not os.path.exists(syslinux_cfg):
            return True
        
        with open(syslinux_cfg, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Update SYSLINUX patterns
        content = re.sub(
            r'KERNEL /minios/boot/vmlinuz[^\s]*',
            f'KERNEL /minios/boot/vmlinuz-{kernel_version}',
            content
        )
        content = re.sub(
            r'initrd=/minios/boot/initrfs[^\s]*\.img',
            f'initrd=/minios/boot/initrfs-{kernel_version}.img',
            content
        )
        
        with open(syslinux_cfg, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"I: {_('Updated SYSLINUX config')}")
        return True
        
    except Exception as e:
        print(f"E: {_('Error updating syslinux config: {error}').format(error=e)}")
        return False

def find_grub_config_file(minios_path: str) -> Optional[str]:
    """
    Find the primary GRUB configuration file with priority:
    1. main.cfg (new structure - contains boot commands)  
    2. grub.cfg (fallback - may contain boot commands)
    """
    grub_dir = os.path.join(minios_path, "boot", "grub")
    
    # Priority 1: main.cfg (new structure)
    main_cfg = os.path.join(grub_dir, "main.cfg")
    if os.path.exists(main_cfg):
        return main_cfg
        
    # Priority 2: grub.cfg (fallback)
    grub_cfg = os.path.join(grub_dir, "grub.cfg")
    if os.path.exists(grub_cfg):
        return grub_cfg
        
    return None

def update_grub_config(minios_path: str, kernel_version: str) -> bool:
    """
    Update GRUB configuration to use the new kernel
    Handles:
    - Direct linux/initrd commands  
    - search --set -f patterns
    - All other kernel/initrd file references
    """
    try:
        config_file = find_grub_config_file(minios_path)
        
        if not config_file:
            print(f"W: {_('No GRUB configuration file found')}")
            return False
        
        with open(config_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
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
        
        # Update search --set -f patterns (for main.cfg format)
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
        
        with open(config_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"I: {_('Updated GRUB config: {}').format(os.path.basename(config_file))}")
        return True
        
    except Exception as e:
        print(f"E: {_('Error updating grub config: {error}').format(error=e)}")
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
    
    # Update SYSLINUX (optional - always returns True if missing)
    if not update_syslinux_config(minios_path, kernel_version):
        success = False
    
    return success
