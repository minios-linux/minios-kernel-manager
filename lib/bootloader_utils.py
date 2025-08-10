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
    """
    try:
        syslinux_cfg = os.path.join(minios_path, "boot", "syslinux.cfg")
        
        if not os.path.exists(syslinux_cfg):
            return False
        
        with open(syslinux_cfg, 'r', encoding='utf-8') as f:
            content = f.read()
        
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
        
        return True
        
    except Exception as e:
        print(f"E: {_('Error updating syslinux config: {error}').format(error=e)}")
        return False

def update_grub_config(minios_path: str, kernel_version: str) -> bool:
    """
    Update grub.cfg to use the new kernel
    """
    try:
        grub_cfg = os.path.join(minios_path, "boot", "grub", "grub.cfg")
        
        if not os.path.exists(grub_cfg):
            return False
        
        with open(grub_cfg, 'r', encoding='utf-8') as f:
            content = f.read()
        
        content = re.sub(
            r'set linux_image="/minios/boot/vmlinuz[^"]*"',
            f'set linux_image="/minios/boot/vmlinuz-{kernel_version}"',
            content
        )
        content = re.sub(
            r'set initrd_img="/minios/boot/initrfs[^"]*\.img"',
            f'set initrd_img="/minios/boot/initrfs-{kernel_version}.img"',
            content
        )
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
        
        with open(grub_cfg, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return True
        
    except Exception as e:
        print(f"E: {_('Error updating grub config: {error}').format(error=e)}")
        return False

def update_bootloader_configs(minios_path: str, kernel_version: str) -> bool:
    """
    Update all bootloader configurations for the new kernel
    """
    success = True
    
    if not update_syslinux_config(minios_path, kernel_version):
        success = False
    
    if not update_grub_config(minios_path, kernel_version):
        success = False
    
    return success
