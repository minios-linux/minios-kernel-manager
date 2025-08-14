#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kernel utilities for MiniOS Kernel Manager
Handles kernel detection, download, and module management
"""

import os
import glob
import subprocess
import tempfile
import shutil
import gettext
from typing import List, Optional, Tuple

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext


def get_available_kernels() -> List[str]:
    """Get list of installed kernels"""
    kernels = []
    
    # Use simple kernel detection approach
    try:
        if os.path.exists("/lib/modules"):
            kernels = [k for k in os.listdir("/lib/modules") 
                      if os.path.isdir(os.path.join("/lib/modules", k))]
    except (OSError, PermissionError):
        pass
    
    return sorted(kernels)


def get_manual_packages() -> List[str]:
    """Return empty list - manual packages are now selected via file picker"""
    # Manual packages are now selected through file picker dialog
    # This function is kept for compatibility but returns empty list
    return []


def get_repository_kernels() -> List[dict]:
    """Get list of available repository kernels with detailed information"""
    packages = []
    try:
        # Search for kernel packages
        result = subprocess.run(['apt-cache', 'search', '^linux-image-[0-9]'], 
                              capture_output=True, text=True, check=True)
        
        for line in result.stdout.strip().split('\n'):
            if line and ' - ' in line:
                parts = line.split(' - ', 1)
                pkg = parts[0]
                description = parts[1] if len(parts) > 1 else ""
                
                # Skip debug packages only
                if 'dbg' not in pkg:
                    try:
                        show_result = subprocess.run(['apt-cache', 'show', pkg], 
                                                   capture_output=True, text=True, check=True)
                        
                        pkg_info = _parse_package_info(show_result.stdout, pkg, description)
                        if pkg_info and pkg_info['size'] > 1 * 1024 * 1024:  # 1MB threshold
                            packages.append(pkg_info)
                            
                    except (subprocess.CalledProcessError, ValueError, IndexError):
                        continue
        
    except subprocess.CalledProcessError:
        pass
    
    # Sort by version (newer first)
    packages.sort(key=lambda x: x['version'], reverse=True)
    return packages

def _parse_package_info(apt_show_output: str, package_name: str, description: str) -> Optional[dict]:
    """Parse apt-cache show output to extract package information"""
    info = {
        'package': package_name,
        'version': '',
        'size': 0,
        'size_text': '',
        'description': description,
        'architecture': '',
        'installed_size': 0,
        'depends': []
    }
    
    for line in apt_show_output.split('\n'):
        line = line.strip()
        if line.startswith('Version: '):
            info['version'] = line.split(':', 1)[1].strip()
        elif line.startswith('Size: '):
            try:
                info['size'] = int(line.split(':', 1)[1].strip())
                info['size_text'] = _format_size(info['size'])
            except ValueError:
                pass
        elif line.startswith('Architecture: '):
            info['architecture'] = line.split(':', 1)[1].strip()
        elif line.startswith('Installed-Size: '):
            try:
                # Installed-Size is in KB
                info['installed_size'] = int(line.split(':', 1)[1].strip()) * 1024
            except ValueError:
                pass
        elif line.startswith('Depends: '):
            depends_str = line.split(':', 1)[1].strip()
            # Parse basic dependencies (ignore version constraints for now)
            deps = []
            for dep in depends_str.split(','):
                dep = dep.strip().split()[0]  # Get just package name
                if dep and not dep.startswith('${'):  # Skip variable substitutions
                    deps.append(dep)
            info['depends'] = deps[:5]  # Limit to first 5 dependencies
    
    return info if info['size'] > 0 else None

def _format_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def check_package_cache(force_update: bool = False) -> Tuple[bool, str]:
    """
    Check if package cache is outdated and handle accordingly.
    
    Args:
        force_update: If True, automatically update outdated cache
        
    Returns:
        (success, message): True if can proceed, False if should stop
    """
    import time
    
    cache_file = '/var/cache/apt/pkgcache.bin'
    lists_dir = '/var/lib/apt/lists'
    
    # Check if lists directory is empty or doesn't exist
    lists_empty = True
    if os.path.exists(lists_dir):
        try:
            # Check if directory has any files (excluding lock files)
            files = [f for f in os.listdir(lists_dir) if not f.startswith('lock')]
            lists_empty = len(files) == 0
        except (OSError, PermissionError):
            lists_empty = True
    
    # Check cache file age
    cache_outdated = True
    if os.path.exists(cache_file) and not lists_empty:
        try:
            file_age = time.time() - os.path.getmtime(cache_file)
            cache_outdated = file_age >= 24 * 60 * 60  # Older than 24 hours
        except (OSError, PermissionError):
            cache_outdated = True
    
    # If lists are empty or cache is outdated
    if lists_empty or cache_outdated:
        if force_update:
            try:
                print("I: {}".format(_('Updating package lists...')), flush=True)
                result = subprocess.run(['apt', 'update'], check=True, capture_output=True, text=True)
                print("I: {}".format(_('Package lists updated')), flush=True)
                return True, ""
            except subprocess.CalledProcessError as e:
                error_msg = _("Failed to update package lists: {}").format(str(e))
                return False, error_msg
            except Exception as e:
                error_msg = _("Error updating package lists: {}").format(str(e))
                return False, error_msg
        else:
            # Show warning and stop
            print("W: {}".format(_('Package database is outdated')), flush=True)
            print("E: {}".format(_('Run \'apt update\' or use --force-update')), flush=True)
            return False, "Package database is outdated"
    
    return True, ""


def process_manual_package(package_path: str, temp_dir: str) -> str:
    """Process manually selected .deb package, return kernel version"""
    try:
        import re
        
        # Extract package using dpkg-deb
        subprocess.run(['dpkg-deb', '-x', package_path, temp_dir], check=True)
        
        # Extract kernel version from filename
        filename = os.path.basename(package_path)
        
        # Try to extract version from filename like linux-image-6.1.0-13-amd64_6.1.55-1_amd64.deb
        match = re.search(r'linux-image-(.+?)_', filename)
        if match:
            return match.group(1)
        
        # Fallback: look for kernel in extracted files
        boot_dir = os.path.join(temp_dir, "boot")
        if os.path.exists(boot_dir):
            vmlinuz_files = glob.glob(os.path.join(boot_dir, "vmlinuz-*"))
            if vmlinuz_files:
                vmlinuz_file = vmlinuz_files[0]
                basename = os.path.basename(vmlinuz_file)
                if basename.startswith("vmlinuz-"):
                    return basename[8:]  # Remove "vmlinuz-"
        
        # Last resort: try to parse package control info
        subprocess.run(['dpkg-deb', '-e', package_path, os.path.join(temp_dir, 'DEBIAN')], check=True)
        control_file = os.path.join(temp_dir, 'DEBIAN', 'control')
        if os.path.exists(control_file):
            with open(control_file, 'r') as f:
                control_content = f.read()
                # Look for Package: linux-image-VERSION
                match = re.search(r'Package:\s*linux-image-(.+)', control_content)
                if match:
                    return match.group(1)
        
        raise RuntimeError("Could not determine kernel version from package")
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to process package: {e}")
    except Exception as e:
        raise RuntimeError(f"Error processing manual package: {e}")


def download_kernel_package(package_name: str, temp_dir: str, force_update: bool = False) -> str:
    """Download and extract kernel package, return kernel version"""
    import time
    deb_file = None
    
    # Check package cache before attempting download
    cache_ok, cache_message = check_package_cache(force_update)
    if not cache_ok:
        raise RuntimeError(cache_message)
    
    try:
        # Step 1: Download package
        print(f"I: {_('Downloading {package_name} from repository...').format(package_name=package_name)}", flush=True)
        result = subprocess.run(['apt-get', 'download', package_name],
                              cwd=temp_dir, check=True)
        print(f"I: {_('Download completed successfully')}", flush=True)
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to download package '{package_name}' from repository: {e}")
    
    try:
        # Step 2: Find downloaded .deb file
        deb_files = glob.glob(os.path.join(temp_dir, f"{package_name}_*.deb"))
        if not deb_files:
            raise RuntimeError(f"Downloaded .deb file for '{package_name}' not found in {temp_dir}")
        
        deb_file = deb_files[0]
        print(f"I: {_('Found package file: {filename}').format(filename=os.path.basename(deb_file))}", flush=True)
        
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Error locating downloaded package: {e}")
    
    try:
        # Step 3: Extract package contents
        print(f"I: {_('Extracting package contents...')}", flush=True)
        extract_result = subprocess.run(['dpkg-deb', '-x', deb_file, temp_dir], 
                                      check=True, capture_output=True, text=True)
        print(f"I: {_('Package extracted successfully')}", flush=True)
        
        # Determine actual kernel version from extracted package contents
        # Priority: vmlinuz filename > modules directory name > package name
        actual_kernel_version = None
        
        # Look for vmlinuz file to get the real kernel version
        boot_paths = [
            os.path.join(temp_dir, "boot"),
            os.path.join(temp_dir, "usr", "boot")
        ]
        
        for boot_path in boot_paths:
            if os.path.exists(boot_path):
                for item in os.listdir(boot_path):
                    if item.startswith("vmlinuz-"):
                        actual_kernel_version = item.replace("vmlinuz-", "")
                        break
                if actual_kernel_version:
                    break
        
        # Fallback: check modules directory
        if not actual_kernel_version:
            modules_base_paths = [
                os.path.join(temp_dir, "lib", "modules"),
                os.path.join(temp_dir, "usr", "lib", "modules")
            ]
            
            for modules_base in modules_base_paths:
                if os.path.exists(modules_base):
                    version_dirs = [d for d in os.listdir(modules_base) 
                                  if os.path.isdir(os.path.join(modules_base, d))]
                    if version_dirs:
                        actual_kernel_version = version_dirs[0]
                        break
        
        # Final fallback: use package name
        if not actual_kernel_version:
            actual_kernel_version = package_name.replace('linux-image-', '')
        
        # For output files, use package name (preserves -unsigned suffix if present)
        display_kernel_version = package_name.replace('linux-image-', '')
        
        # Store both versions for later use
        if not hasattr(download_kernel_package, '_versions'):
            download_kernel_package._versions = {}
        download_kernel_package._versions = {
            'display_version': display_kernel_version,
            'actual_version': actual_kernel_version
        }
        
        return display_kernel_version
        
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to extract package '{os.path.basename(deb_file)}'"
        if e.stderr:
            error_msg += f": {e.stderr.strip()}"
        else:
            error_msg += f": dpkg-deb returned exit code {e.returncode}"
        raise RuntimeError(error_msg)
    except Exception as e:
        raise RuntimeError(f"Error extracting package: {e}")


def get_non_symlink_modules_dir() -> str:
    """Get modules directory that is not a symlink"""
    if os.path.exists("/lib") and not os.path.islink("/lib"):
        return "/lib/modules"
    elif os.path.exists("/usr/lib") and not os.path.islink("/usr/lib"):
        return "/usr/lib/modules"
    else:
        raise RuntimeError("No valid modules directory found")


def locate_kernel_modules(kernel_version: str) -> str:
    """Locate kernel modules directory for installed kernel"""
    modules_dir = get_non_symlink_modules_dir()
    kernel_path = os.path.join(modules_dir, kernel_version)
    
    if not os.path.exists(kernel_path):
        raise RuntimeError(f"Kernel modules for {kernel_version} not found")
    
    return modules_dir


def prepare_temp_modules(kernel_version: str, temp_dir: str, force_reinstall: bool = False) -> None:
    """Prepare temporary kernel modules for repository kernel"""
    import shutil
    
    target_dir = get_non_symlink_modules_dir()
    target_path = os.path.join(target_dir, kernel_version)
    
    # Check if kernel is already installed
    if os.path.exists(target_path):
        if not force_reinstall:
            raise RuntimeError(f"KERNEL_EXISTS:{kernel_version}")
        else:
            # Remove existing installation for reinstall
            print(f"I: {_('Removing existing kernel modules for {kernel_version}').format(kernel_version=kernel_version)}")
            shutil.rmtree(target_path)
    
    # Find extracted modules
    extracted_paths = [
        os.path.join(temp_dir, "lib", "modules", kernel_version),
        os.path.join(temp_dir, "usr", "lib", "modules", kernel_version)
    ]
    
    # Find and verify modules directory
    found_paths = []
    for path in extracted_paths:
        if os.path.exists(path):
            found_paths.append(path)
            # Verify directory is readable
            try:
                os.listdir(path)
            except Exception as e:
                print(f"E: {_('Error reading modules directory: {error}').format(error=e)}")
            break  # Use first found path
    
    source_path = None
    for path in extracted_paths:
        if os.path.exists(path):
            source_path = path
            break
    
    if not source_path:
        raise RuntimeError(_("Kernel modules for {kernel_version} not found in package").format(kernel_version=kernel_version))
    
    # Copy modules to system location
    print(f"I: {_('Installing kernel modules to {target_path}').format(target_path=target_path)}", flush=True)
    shutil.copytree(source_path, target_path)
    
    # Run depmod if modules.dep doesn't exist
    modules_dep = os.path.join(target_path, "modules.dep")
    if not os.path.exists(modules_dep):
        print(f"I: {_('Building module dependencies')}", flush=True)
        subprocess.run(['depmod', kernel_version], check=True, capture_output=True)


def cleanup_temp_modules(kernel_version: str) -> None:
    """Remove temporary kernel modules"""
    try:
        target_dir = get_non_symlink_modules_dir()
        target_path = os.path.join(target_dir, kernel_version)
        
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
    except Exception:
        pass  # Ignore cleanup errors