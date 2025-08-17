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
from typing import Optional, List

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
        syslinux_cfg = os.path.join(minios_path, "boot", "syslinux", "syslinux.cfg")
        
        # SYSLINUX is optional
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
                    with open(config_file, 'w', encoding='utf-8') as f:
                        f.write(content)
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
    
    return success
