#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kernel utilities for MiniOS KernelPack GUI
Handles kernel detection, download, and module management
"""

import os
import glob
import subprocess
import tempfile
import shutil
from typing import List, Optional


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


def download_kernel_package(package_name: str, temp_dir: str) -> str:
    """Download and extract kernel package, return kernel version"""
    try:
        # Download package
        result = subprocess.run(['apt-get', 'download', package_name],
                              cwd=temp_dir, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download {package_name}: {result.stderr}")
        
        # Find downloaded .deb file
        deb_files = glob.glob(os.path.join(temp_dir, f"{package_name}_*.deb"))
        if not deb_files:
            raise RuntimeError(f"Downloaded package not found")
        
        deb_file = deb_files[0]
        
        # Extract package using dpkg-deb
        extract_result = subprocess.run(['dpkg-deb', '-x', deb_file, temp_dir], 
                                      capture_output=True, text=True, check=True)
        
        # Extract kernel version from package name
        kernel_version = package_name.replace('linux-image-', '')
        return kernel_version
        
    except (subprocess.CalledProcessError, RuntimeError) as e:
        raise RuntimeError(f"Failed to download kernel package: {e}")


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
            print(f"Removing existing kernel modules for {kernel_version}")
            shutil.rmtree(target_path)
    
    # Find extracted modules
    extracted_paths = [
        os.path.join(temp_dir, "lib", "modules", kernel_version),
        os.path.join(temp_dir, "usr", "lib", "modules", kernel_version)
    ]
    
    # Debug: log what we're looking for
    print(f"Searching for kernel modules for {kernel_version}")
    print(f"Temporary directory: {temp_dir}")
    
    found_paths = []
    for path in extracted_paths:
        exists = os.path.exists(path)
        print(f"Checking: {path} - {'Found' if exists else 'Not found'}")
        if exists:
            found_paths.append(path)
            try:
                contents = os.listdir(path)
                print(f"Module directory contains {len(contents)} items")
            except Exception as e:
                print(f"Error reading modules directory: {e}")
    
    source_path = None
    for path in extracted_paths:
        if os.path.exists(path):
            source_path = path
            break
    
    if not source_path:
        print(f"No kernel modules found in expected locations")
        print(f"Available paths searched:")
        for i, path in enumerate(extracted_paths, 1):
            print(f"  {i}. {path}")
        
        # Show what we actually have in temp_dir
        print(f"Contents of extraction directory:")
        try:
            for root, dirs, files in os.walk(temp_dir):
                level = root.replace(temp_dir, '').count(os.sep)
                if level <= 3:  # Limit depth for readability
                    indent = '  ' * level
                    rel_path = os.path.relpath(root, temp_dir)
                    print(f"{indent}{rel_path}/")
        except Exception as e:
            print(f"Error examining extraction directory: {e}")
        
        raise RuntimeError(f"Extracted kernel modules for {kernel_version} not found")
    
    # Copy modules to system location
    shutil.copytree(source_path, target_path)
    
    # Run depmod if modules.dep doesn't exist
    modules_dep = os.path.join(target_path, "modules.dep")
    if not os.path.exists(modules_dep):
        subprocess.run(['depmod', kernel_version], check=True)


def cleanup_temp_modules(kernel_version: str) -> None:
    """Remove temporary kernel modules"""
    try:
        target_dir = get_non_symlink_modules_dir()
        target_path = os.path.join(target_dir, kernel_version)
        
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
    except Exception:
        pass  # Ignore cleanup errors