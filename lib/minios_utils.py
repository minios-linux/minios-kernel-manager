#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniOS utilities for kernel management
Handles MiniOS directory detection, permission checks, and file operations
"""

import os
import shutil
import subprocess
import glob
import tempfile
import gettext
from typing import Optional, List, Tuple

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext

def find_minios_directory() -> Optional[str]:
    """Find MiniOS directory on the system"""
    # Common locations where MiniOS might be mounted
    common_paths = [
        "/run/initramfs/memory/data/minios",
        "/run/initramfs/memory/toram/minios", 
        "/media/*/minios",
        "/mnt/*/minios",
        "/minios"
    ]
    
    # Check each path
    for path_pattern in common_paths:
        if '*' in path_pattern:
            # Handle wildcard paths
            for path in glob.glob(path_pattern):
                if _is_valid_minios_directory(path):
                    return path
        else:
            # Direct path check
            if _is_valid_minios_directory(path_pattern):
                return path_pattern
    
    # Try to find mounted filesystems with minios folder
    try:
        result = subprocess.run(['findmnt', '-t', 'vfat,ext4,ntfs'], 
                              capture_output=True, text=True)
        for line in result.stdout.split('\n')[1:]:  # Skip header
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    mount_point = parts[1]
                    minios_path = os.path.join(mount_point, 'minios')
                    if _is_valid_minios_directory(minios_path):
                        return minios_path
    except:
        pass
    
    return None

def _is_valid_minios_directory(path: str) -> bool:
    """Check if directory looks like a valid MiniOS directory"""
    if not os.path.exists(path):
        return False
    
    # Check for typical MiniOS structure
    expected_items = ['boot', '01-kernel*', '02-firmware*']
    found_items = 0
    
    try:
        items = os.listdir(path)
        for item in items:
            if item == 'boot':
                found_items += 1
            elif item.startswith('01-kernel'):
                found_items += 1
            elif item.startswith('02-firmware'):
                found_items += 1
    except PermissionError:
        return False
    
    return found_items >= 1  # At least one expected item

def get_kernel_repository_path(minios_path: str) -> str:
    """Get the path to the kernel repository."""
    return os.path.join(minios_path, "kernels")

def get_kernel_path(minios_path: str, kernel_version: str) -> str:
    """Get the path to a specific kernel version in the repository."""
    return os.path.join(get_kernel_repository_path(minios_path), kernel_version)

def package_kernel_to_repository(minios_path: str, kernel_version: str,
                                 squashfs_file: str, vmlinuz_file: str, initramfs_file: str) -> bool:
    """Packages a kernel and places it in the inactive kernel repository."""
    kernel_repo_path = get_kernel_repository_path(minios_path)
    kernel_version_path = get_kernel_path(minios_path, kernel_version)
    
    try:
        os.makedirs(kernel_version_path, exist_ok=True)
        
        shutil.copy2(squashfs_file, os.path.join(kernel_version_path, os.path.basename(squashfs_file)))
        shutil.copy2(vmlinuz_file, os.path.join(kernel_version_path, os.path.basename(vmlinuz_file)))
        shutil.copy2(initramfs_file, os.path.join(kernel_version_path, os.path.basename(initramfs_file)))
        
        return True
    except Exception as e:
        print(f"Failed to package kernel to repository: {e}")
        # Cleanup partial installation
        if os.path.exists(kernel_version_path):
            shutil.rmtree(kernel_version_path)
        return False

def get_active_kernel(minios_path: str) -> Optional[str]:
    """Gets the version of the currently active kernel from boot marker."""
    # First try to get active kernel from boot marker
    marker_file = os.path.join(minios_path, "boot", "active-kernel")
    
    if os.path.exists(marker_file):
        try:
            with open(marker_file, 'r') as f:
                kernel_version = f.read().strip()
                if kernel_version:
                    return kernel_version
        except Exception as e:
            print(f"Warning: Error reading active kernel marker {marker_file}: {e}")
    
    # Fallback: check for vmlinuz files in boot directory
    boot_path = os.path.join(minios_path, "boot")
    if not os.path.exists(boot_path):
        return None

    vmlinuz_files = glob.glob(os.path.join(boot_path, "vmlinuz-*"))
    if not vmlinuz_files:
        return None

    try:
        first_file = os.path.basename(vmlinuz_files[0])
        return first_file.replace("vmlinuz-", "")
    except IndexError:
        return None

def get_active_kernel_files(minios_path: str, kernel_version: str = None) -> List[str]:
    """Gets list of active kernel files (vmlinuz, initramfs, squashfs)."""
    files = []
    
    if kernel_version:
        # Get files for specific kernel version
        boot_path = os.path.join(minios_path, "boot")
        if os.path.exists(boot_path):
            vmlinuz_file = os.path.join(boot_path, f"vmlinuz-{kernel_version}")
            if os.path.exists(vmlinuz_file):
                files.append(vmlinuz_file)
            
            initramfs_file = os.path.join(boot_path, f"initrfs-{kernel_version}.img")
            if os.path.exists(initramfs_file):
                files.append(initramfs_file)
        
        squashfs_file = os.path.join(minios_path, f"01-kernel-{kernel_version}.sb")
        if os.path.exists(squashfs_file):
            files.append(squashfs_file)
    else:
        # Get all active files (original behavior)
        boot_path = os.path.join(minios_path, "boot")
        if os.path.exists(boot_path):
            vmlinuz_files = glob.glob(os.path.join(boot_path, "vmlinuz-*"))
            files.extend(vmlinuz_files)
            
            # Check for initramfs files
            initramfs_files = glob.glob(os.path.join(boot_path, "initrfs-*.img"))
            files.extend(initramfs_files)
        
        # Check for squashfs files in minios root
        squashfs_files = glob.glob(os.path.join(minios_path, "01-kernel-*.sb"))
        files.extend(squashfs_files)
    
    return files

def _get_filesystem_type(path: str) -> str:
    """Get filesystem type for a given path."""
    try:
        result = subprocess.run(['stat', '-f', '-c', '%T', path], 
                              capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback method using /proc/mounts
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mount_point, fs_type = parts[1], parts[2]
                        if path.startswith(mount_point):
                            return fs_type
        except:
            pass
    return "unknown"

def _update_bootloader_configs(minios_path: str, kernel_version: str) -> bool:
    """Update GRUB and Syslinux configuration files with new kernel version."""
    success = True
    
    # Check filesystem type for informational purposes
    fs_type = _get_filesystem_type(minios_path)
    print(f"Updating bootloader configs on filesystem type: {fs_type}")
    
    # Update Syslinux configuration
    syslinux_cfg = os.path.join(minios_path, "boot", "syslinux.cfg")
    if os.path.exists(syslinux_cfg):
        success &= _update_syslinux_config(syslinux_cfg, kernel_version)
    
    # Update GRUB configuration
    grub_cfg = os.path.join(minios_path, "boot", "grub", "grub.cfg")
    if os.path.exists(grub_cfg):
        success &= _update_grub_config(grub_cfg, kernel_version)
    
    return success

def _update_syslinux_config(config_file: str, kernel_version: str) -> bool:
    """Update Syslinux configuration file with new kernel paths."""
    try:
        # Check if file exists
        if not os.path.exists(config_file):
            print(f"Syslinux config file not found: {config_file}")
            return True  # Not an error if file doesn't exist
        
        # Try to make file writable (may not work on non-POSIX filesystems)
        try:
            os.chmod(config_file, 0o644)
        except (OSError, NotImplementedError):
            pass  # Filesystem doesn't support chmod
        
        # Read current configuration
        with open(config_file, 'r') as f:
            content = f.read()
        
        # Replace kernel and initrd paths in all KERNEL and APPEND lines
        import re
        
        # Pattern to match kernel paths
        kernel_pattern = r'(KERNEL\s+/minios/boot/)vmlinuz-[^\s]+'
        initrd_pattern = r'(initrd=/minios/boot/)initrfs-[^\s]+'
        
        # Replace with new kernel version
        new_content = re.sub(kernel_pattern, f'\\1vmlinuz-{kernel_version}', content)
        new_content = re.sub(initrd_pattern, f'\\1initrfs-{kernel_version}.img', new_content)
        
        # Write back if changed
        if new_content != content:
            with open(config_file, 'w') as f:
                f.write(new_content)
            print(f"Updated Syslinux configuration: {config_file}")
        
        return True
        
    except Exception as e:
        print(f"Warning: Failed to update Syslinux config {config_file}: {e}")
        return False

def _update_grub_config(config_file: str, kernel_version: str) -> bool:
    """Update GRUB configuration file with new kernel paths."""
    try:
        # Check if file exists
        if not os.path.exists(config_file):
            print(f"GRUB config file not found: {config_file}")
            return True  # Not an error if file doesn't exist
        
        # Try to make file writable (may not work on non-POSIX filesystems)
        try:
            os.chmod(config_file, 0o644)
        except (OSError, NotImplementedError):
            pass  # Filesystem doesn't support chmod
        
        # Read current configuration
        with open(config_file, 'r') as f:
            content = f.read()
        
        # Replace kernel and initrd paths
        import re
        
        # Pattern to match kernel paths in variable definitions
        linux_image_pattern = r'(set linux_image=")([^"]+)"'
        initrd_img_pattern = r'(set initrd_img=")([^"]+)"'
        
        # Pattern to match kernel paths in menuentry lines
        vmlinuz_pattern = r'(/minios/boot/)vmlinuz-[^\s]+'
        initrfs_pattern = r'(/minios/boot/)initrfs-[^\s]+'
        search_pattern = r'(search --set -f /minios/boot/)vmlinuz-[^\s]+'
        
        # Replace with new kernel version
        new_content = re.sub(linux_image_pattern, f'\\1/minios/boot/vmlinuz-{kernel_version}"', content)
        new_content = re.sub(initrd_img_pattern, f'\\1/minios/boot/initrfs-{kernel_version}.img"', new_content)
        new_content = re.sub(vmlinuz_pattern, f'\\1vmlinuz-{kernel_version}', new_content)
        new_content = re.sub(initrfs_pattern, f'\\1initrfs-{kernel_version}.img', new_content)
        new_content = re.sub(search_pattern, f'\\1vmlinuz-{kernel_version}', new_content)
        
        # Write back if changed
        if new_content != content:
            with open(config_file, 'w') as f:
                f.write(new_content)
            print(f"Updated GRUB configuration: {config_file}")
        
        return True
        
    except Exception as e:
        print(f"Warning: Failed to update GRUB config {config_file}: {e}")
        return False

def deactivate_current_kernel(minios_path: str) -> bool:
    """Moves or copies the currently active kernel files to the kernel repository."""
    active_kernel_version = get_active_kernel(minios_path)
    if not active_kernel_version:
        return True # Nothing to do

    # Always ensure the repository directory exists
    kernel_version_path = get_kernel_path(minios_path, active_kernel_version)
    os.makedirs(kernel_version_path, exist_ok=True)

    # Determine operation based on kernel status
    is_running = is_kernel_currently_running(active_kernel_version)
    operation = "copy" if is_running else "move"
    
    try:
        # Get files belonging to the specific active kernel version
        active_files = get_active_kernel_files(minios_path, active_kernel_version)
        
        if not active_files:
            print(f"No active files found for kernel {active_kernel_version}")
            return True
        
        print(f"Deactivating kernel {active_kernel_version}: will {operation} {len(active_files)} file(s)")
        
        for f in active_files:
            if os.path.exists(f):
                dest_path = os.path.join(kernel_version_path, os.path.basename(f))
                
                if is_running:
                    # Copy files if kernel is running (keep originals)
                    shutil.copy2(f, dest_path)
                    print(f"Copied {os.path.basename(f)} to repository (running kernel)")
                else:
                    # Move files if kernel is not running
                    shutil.move(f, dest_path)
                    print(f"Moved {os.path.basename(f)} to repository")
            else:
                print(f"Warning: Expected file {f} not found")
        
        if is_running:
            print(f"Active kernel {active_kernel_version} is running - files copied to repository and left in place")
        else:
            print(f"Active kernel {active_kernel_version} deactivated - files moved to repository")
            
        return True
    except Exception as e:
        print(f"Failed to deactivate current kernel {active_kernel_version}: {e}")
        # Attempt to rollback is complex, for now we fail
        return False

def activate_kernel(minios_path: str, kernel_version: str) -> bool:
    """Activates a kernel from the repository."""
    # Handle running kernel activation
    if is_kernel_currently_running(kernel_version):
        current_active = get_active_kernel(minios_path)
        if current_active == kernel_version:
            print(f"Kernel {kernel_version} is already active and running.")
            return True
        else:
            # Deactivate current and use running kernel files
            if not deactivate_current_kernel(minios_path):
                return False
            
            # Update bootloader configurations
            _update_bootloader_configs(minios_path, kernel_version)
            
            # Create active kernel marker
            marker_file = os.path.join(minios_path, "boot", "active-kernel")
            os.makedirs(os.path.dirname(marker_file), exist_ok=True)
            with open(marker_file, 'w') as f:
                f.write(kernel_version)
            
            print(f"Activated running kernel {kernel_version} (files already in place).")
            return True
    
    if not deactivate_current_kernel(minios_path):
        return False

    kernel_version_path = get_kernel_path(minios_path, kernel_version)
    if not os.path.exists(kernel_version_path):
        print(f"Kernel version {kernel_version} not found in repository.")
        return False

    try:
        # Prepare kernel file paths
        squashfs_file = os.path.join(kernel_version_path, f"01-kernel-{kernel_version}.sb")
        vmlinuz_file = os.path.join(kernel_version_path, f"vmlinuz-{kernel_version}")
        initramfs_file = os.path.join(kernel_version_path, f"initrfs-{kernel_version}.img")

        # Verify all required files exist before copying
        if not os.path.exists(squashfs_file):
            raise FileNotFoundError(f"SquashFS file not found: {squashfs_file}")
        if not os.path.exists(vmlinuz_file):
            raise FileNotFoundError(f"Kernel file not found: {vmlinuz_file}")
        if not os.path.exists(initramfs_file):
            raise FileNotFoundError(f"Initramfs file not found: {initramfs_file}")

        # Copy kernel files to active locations
        shutil.copy2(squashfs_file, os.path.join(minios_path, os.path.basename(squashfs_file)))
        shutil.copy2(vmlinuz_file, os.path.join(minios_path, "boot", os.path.basename(vmlinuz_file)))
        shutil.copy2(initramfs_file, os.path.join(minios_path, "boot", os.path.basename(initramfs_file)))
        
        # Update bootloader configurations
        _update_bootloader_configs(minios_path, kernel_version)
        
        # Create active kernel marker
        marker_file = os.path.join(minios_path, "boot", "active-kernel")
        os.makedirs(os.path.dirname(marker_file), exist_ok=True)
        with open(marker_file, 'w') as f:
            f.write(kernel_version)
        
        print(f"Successfully copied kernel files for {kernel_version}")
        return True
    except (Exception, IndexError) as e:
        print(f"Failed to activate kernel {kernel_version}: {e}")
        return False

def list_all_kernels(minios_path: str) -> List[str]:
    """Lists all unique kernel versions available (packaged, active, or running)."""
    kernels = set()
    
    # Add packaged kernels
    kernel_repo_path = get_kernel_repository_path(minios_path)
    if os.path.exists(kernel_repo_path):
        kernels.update([d for d in os.listdir(kernel_repo_path) if os.path.isdir(os.path.join(kernel_repo_path, d))])

    # Add active kernel
    active_kernel = get_active_kernel(minios_path)
    if active_kernel:
        kernels.add(active_kernel)

    # Add running kernel
    running_kernel = get_currently_running_kernel()
    if running_kernel:
        kernels.add(running_kernel)

    return sorted(list(kernels))

def delete_packaged_kernel(minios_path: str, kernel_version: str) -> bool:
    """Deletes a packaged kernel from the repository."""
    kernel_version_path = get_kernel_path(minios_path, kernel_version)
    if not os.path.exists(kernel_version_path):
        return True # Already gone
    
    try:
        shutil.rmtree(kernel_version_path)
        return True
    except Exception as e:
        print(f"Failed to delete packaged kernel {kernel_version}: {e}")
        return False

def get_kernel_info(minios_path: str, kernel_id: str) -> dict:
    """Get detailed information about a kernel."""
    active_kernel_id = get_active_kernel(minios_path)
    is_active = kernel_id == active_kernel_id
    is_running = is_kernel_currently_running(kernel_id)
    is_packaged = os.path.exists(get_kernel_path(minios_path, kernel_id))

    # Create better display name with version parsing
    display_name = kernel_id
    if '-' in kernel_id:
        parts = kernel_id.split('-')
        if len(parts) >= 2:
            version = parts[0]
            arch_flavor = '-'.join(parts[1:])
            display_name = f"{version} ({arch_flavor})"

    # Determine status with priorities
    status_parts = []
    status_color = "#666666"  # Default gray
    icon_name = "package-x-generic"  # Unified icon for all kernels
    
    if is_running:
        status_parts.append("Running")
        status_color = "#e74c3c"  # Red for running
        icon_name = "package-x-generic"  # Unified icon
    
    if is_active:
        if is_running:
            status_parts = ["Active & Running"]
        else:
            status_parts.append("Active")
            status_color = "#27ae60"  # Green for active
            icon_name = "package-x-generic"  # Unified icon
    
    if is_packaged and not is_active:
        status_parts.append("Available")
        status_color = "#3498db"  # Blue for packaged
        icon_name = "package-x-generic"  # Unified icon
    
    if not status_parts:
        return None

    # Determine kernel type and description
    kernel_type = "Standard"
    kernel_desc = ""
    
    kernel_lower = kernel_id.lower()
    if 'rt' in kernel_lower:
        kernel_type = "Real-time"
        kernel_desc = "Low-latency kernel for real-time applications"
    elif 'cloud' in kernel_lower:
        kernel_type = "Cloud"
        kernel_desc = "Optimized for virtualized environments"
    elif 'mos' in kernel_lower or 'minios' in kernel_lower:
        kernel_type = "MiniOS"
        kernel_desc = "Custom kernel for MiniOS distribution"
    elif 'generic' in kernel_lower:
        kernel_type = "Generic"
        kernel_desc = "General purpose kernel"
    elif 'lowlatency' in kernel_lower:
        kernel_type = "Low-latency"
        kernel_desc = "Reduced latency for audio/video applications"
    else:
        kernel_desc = "Linux kernel"

    # Get file sizes for additional info
    size_info = ""
    if is_active or is_packaged:
        try:
            kernel_path = get_kernel_path(minios_path, kernel_id) if is_packaged else minios_path
            if is_active and not is_packaged:
                # Active kernel files are in different locations
                sb_files = glob.glob(os.path.join(minios_path, "01-kernel-*.sb"))
                if sb_files:
                    sb_size = os.path.getsize(sb_files[0])
                    size_info = f" • {_format_size(sb_size)}"
            elif is_packaged:
                sb_files = glob.glob(os.path.join(kernel_path, "01-kernel-*.sb"))
                if sb_files:
                    sb_size = os.path.getsize(sb_files[0])
                    size_info = f" • {_format_size(sb_size)}"
        except:
            pass

    info = {
        'id': kernel_id,
        'display_name': display_name,
        'version': kernel_id,
        'status': " ".join(status_parts),
        'status_color': status_color,
        'icon_name': icon_name,
        'kernel_type': kernel_type,
        'description': f"{kernel_type} kernel{size_info}",
        'full_description': kernel_desc,
        'is_active': is_active,
        'is_running': is_running,
        'is_packaged': is_packaged
    }

    return info

def _format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def get_kernel_file_info(file_path: str) -> dict:
    """Get file information (size, date) for a kernel file"""
    file_info = {'size': 0, 'size_text': 'Unknown', 'date': 'Unknown'}
    
    try:
        if os.path.exists(file_path):
            stat = os.stat(file_path)
            file_info['size'] = stat.st_size
            
            # Format size in human readable format
            size = stat.st_size
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024.0:
                    file_info['size_text'] = f"{size:.1f} {unit}"
                    break
                size /= 1024.0
            
            # Format date
            import time
            file_info['date'] = time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
    except:
        pass
    
    return file_info

def get_currently_running_kernel() -> str:
    """Get the kernel version currently running on the system with comprehensive analysis"""
    import re
    
    # Method 1: Check mounted .sb modules to see which kernel module is active
    try:
        result = subprocess.run(['mount'], capture_output=True, text=True, check=True)
        mount_output = result.stdout
        
        # Look for mounted kernel .sb files
        for line in mount_output.split('\n'):
            if '01-kernel-' in line and '.sb' in line and 'squashfs' in line:
                match = re.search(r'01-kernel-([^/\s]+\.sb)', line)
                if match:
                    kernel_sb = match.group(1)
                    kernel_version = kernel_sb.replace('.sb', '')
                    return kernel_version
    except subprocess.CalledProcessError:
        pass
    
    # Method 2: Fallback to uname -r
    try:
        result = subprocess.run(['uname', '-r'], capture_output=True, text=True, check=True)
        kernel_version = result.stdout.strip()
        return kernel_version
    except subprocess.CalledProcessError:
        pass
    
    return ""

def is_kernel_currently_running(kernel_version: str, minios_path: str = None) -> bool:
    """Check if a specific kernel version is currently running"""
    return kernel_version == get_currently_running_kernel()

def get_system_type() -> str:
    """Get type of system (live, installed, etc.)"""
    if os.path.exists('/run/initramfs/memory'):
        if os.path.exists('/run/initramfs/memory/toram'):
            return "Live system (running from RAM)"
        else:
            return "Live system (running from media)"
    else:
        return "Installed system"

def get_union_filesystem_type() -> str:
    """Get the type of union filesystem used by MiniOS (aufs or overlayfs)"""
    try:
        # Check mount output for root filesystem
        result = subprocess.run(['mount'], capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if ' on / type ' in line:
                if 'aufs' in line:
                    return 'aufs'
                elif 'overlay' in line:
                    return 'overlayfs'
        
        # Fallback: check /proc/mounts
        with open('/proc/mounts', 'r') as f:
            for line in f:
                if line.startswith('aufs / aufs') or line.startswith('none / aufs'):
                    return 'aufs'
                elif line.startswith('overlay / overlay') or line.startswith('none / overlay'):
                    return 'overlayfs'
        
        # Default fallback
        return 'overlayfs'
        
    except (OSError, IOError, subprocess.CalledProcessError):
        # Default to overlayfs if detection fails
        return 'overlayfs'

def get_temp_dir_with_space_check(required_mb: int = 1024, prefix: str = "minios-kernel-", operation_type: str = "kernel_packaging", custom_temp_dir: str = None) -> str:
    """Get temporary directory with sufficient space.
    
    Checks available space in /tmp and falls back to alternative location if needed.
    
    Args:
        required_mb: Required space in megabytes (default: 1024MB for kernel packaging)
        prefix: Optional prefix for temporary directory name
        operation_type: Type of operation for logging purposes (e.g., "kernel_packaging")
        custom_temp_dir: Custom temporary directory path (if None, use automatic selection)
    
    Returns:
        str: Path to temporary directory with sufficient space
    
    Raises:
        RuntimeError: If insufficient space is available in all locations
    """
    REQUIRED_SPACE = int(required_mb * 1024 * 1024)  # Convert MB to bytes
    
    # Check custom temporary directory first if provided
    if custom_temp_dir:
        if not os.path.exists(custom_temp_dir):
            raise RuntimeError(_("Custom temporary directory does not exist: {}").format(custom_temp_dir))
        
        if not os.access(custom_temp_dir, os.W_OK):
            raise RuntimeError(_("Custom temporary directory is not writable: {}").format(custom_temp_dir))
        
        try:
            statvfs_custom = os.statvfs(custom_temp_dir)
            available_space_custom = statvfs_custom.f_bavail * statvfs_custom.f_frsize
            
            if available_space_custom >= REQUIRED_SPACE:
                print("I: {}".format(_('Using custom temporary directory for {operation} ({available:.1f}MB available, {needed:.1f}MB needed)')).format(
                    operation=operation_type, available=available_space_custom / (1024*1024), needed=REQUIRED_SPACE / (1024*1024)), flush=True)
                return tempfile.mkdtemp(dir=custom_temp_dir, prefix=prefix)
            else:
                raise RuntimeError(_("Insufficient space in custom temporary directory '{}' for {}: {:.1f}MB available, {:.1f}MB needed").format(
                    custom_temp_dir, operation_type, available_space_custom / (1024*1024), REQUIRED_SPACE / (1024*1024)))
        except (OSError, IOError) as e:
            raise RuntimeError(_("Cannot check space in custom temporary directory '{}': {}").format(custom_temp_dir, str(e)))
    
    # Primary choice: /tmp
    default_tmp = "/tmp"
    
    try:
        # Check available space in /tmp
        statvfs = os.statvfs(default_tmp)
        available_space = statvfs.f_bavail * statvfs.f_frsize
        
        if available_space >= REQUIRED_SPACE:
            # Sufficient space in /tmp
            print("I: {}".format(_('Using /tmp for {operation} ({available:.1f}MB available, {needed:.1f}MB needed)')).format(
                operation=operation_type, available=available_space / (1024*1024), needed=REQUIRED_SPACE / (1024*1024)), flush=True)
            return tempfile.mkdtemp(dir=default_tmp, prefix=prefix)
        else:
            print("I: {}".format(_('Insufficient space in /tmp for {operation} ({available:.1f}MB available, {needed:.1f}MB needed)')).format(
                operation=operation_type, available=available_space / (1024*1024), needed=REQUIRED_SPACE / (1024*1024)), flush=True)
            
            # Alternative directory depends on filesystem type
            fs_type = get_union_filesystem_type()
            if fs_type == 'aufs':
                alt_tmp = "/run/initramfs/memory/changes/tmp"
            else:  # overlayfs
                alt_tmp = "/run/initramfs/memory/changes/changes/tmp"
            
            print("I: {}".format(_('Detected {} filesystem, using alternative: {}')).format(
                fs_type, alt_tmp), flush=True)
            
            # Create alternative directory if it doesn't exist
            if not os.path.exists(alt_tmp):
                os.makedirs(alt_tmp, exist_ok=True)
                print("I: {}".format(_('Created alternative temporary directory: {}')).format(alt_tmp), flush=True)
            
            # Check space in alternative location
            statvfs_alt = os.statvfs(alt_tmp)
            available_space_alt = statvfs_alt.f_bavail * statvfs_alt.f_frsize
            
            if available_space_alt >= REQUIRED_SPACE:
                print("I: {}".format(_('Using alternative temporary directory: {} ({:.1f}MB available)')).format(
                    alt_tmp, available_space_alt / (1024*1024)), flush=True)
                return tempfile.mkdtemp(dir=alt_tmp, prefix=prefix)
            else:
                # Not enough space anywhere
                raise RuntimeError(_(
                    "Insufficient disk space for operation. Need {:.1f}MB, but only {:.1f}MB available in /tmp and {:.1f}MB in {}"
                ).format(
                    required_mb,
                    available_space / (1024*1024),
                    available_space_alt / (1024*1024),
                    alt_tmp
                ))
        
    except (OSError, IOError) as e:
        # Fallback to default behavior if space checking fails
        print("W: {}".format(_('Could not check disk space: {}. Using default temporary directory.')).format(str(e)), flush=True)
        return tempfile.mkdtemp(prefix=prefix)