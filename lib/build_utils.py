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

# Use only system installed modules
try:
    # Try relative imports first (when imported as module)
    from .compression_utils import get_compression_params
    from .kernel_utils import get_non_symlink_modules_dir
    from .minios_utils import get_temp_dir_with_space_check
except ImportError:
    # Fall back to absolute imports (when run as main script)
    from compression_utils import get_compression_params
    from kernel_utils import get_non_symlink_modules_dir
    from minios_utils import get_temp_dir_with_space_check

# Initialize gettext
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext


def detect_initramfs_builder() -> str:
    """Detect which initramfs builder is available: 'dracut' or 'livekit'"""
    # Check for dracut first
    if os.path.exists('/run/initramfs/dracut-mos/mkdracut'):
        return 'dracut'

    # Check for livekit
    if os.path.exists('/run/initramfs/mkinitrfs'):
        return 'livekit'

    # Neither found
    raise RuntimeError(_("No initramfs builder found. Please ensure mkdracut or mkinitrfs is available."))


def get_system_modules_base() -> str:
    """Determine the modules base path used by the current system"""
    # Check where modules are actually located in the current system
    # This ensures SquashFS uses the same structure as the target system

    # Follow symlinks to find the real location
    if os.path.exists("/lib/modules"):
        real_path = os.path.realpath("/lib/modules")
        if "/usr/lib/modules" in real_path:
            return "usr/lib/modules"
        else:
            return "lib/modules"
    elif os.path.exists("/usr/lib/modules"):
        return "usr/lib/modules"
    else:
        # Fallback to traditional location
        return "lib/modules"


def copy_vmlinuz(kernel_version: str, temp_dir: str, output_dir: str, kernel_source: str = "local") -> str:
    """Copy vmlinuz file for the selected kernel"""
    output_path = os.path.join(output_dir, f"vmlinuz-{kernel_version}")

    # For extracted packages, look for any vmlinuz file first
    if temp_dir and os.path.exists(temp_dir):
        boot_dir = os.path.join(temp_dir, "boot")
        if os.path.exists(boot_dir):
            vmlinuz_files = glob.glob(os.path.join(boot_dir, "vmlinuz-*"))
            if vmlinuz_files:
                # Use the first (and usually only) vmlinuz file found
                shutil.copy2(vmlinuz_files[0], output_path)
                return output_path

    # Search paths for vmlinuz
    search_paths = [
        os.path.join(temp_dir, "boot", f"vmlinuz-{kernel_version}"),
        f"/boot/vmlinuz-{kernel_version}",
        f"/run/initramfs/memory/data/minios/boot/vmlinuz-{kernel_version}",
        f"/run/initramfs/memory/data/minios/boot/vmlinuz",
        f"/lib/live/mount/medium/minios/boot/vmlinuz-{kernel_version}",
        f"/lib/live/mount/medium/minios/boot/vmlinuz"
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
        temp_dir: Temporary directory with extracted deb contents (required)
    """
    if not temp_dir or not os.path.exists(temp_dir):
        raise RuntimeError(_("temp_dir is required and must exist for SquashFS creation"))

    output_image = os.path.join(output_dir, f"01-kernel-{kernel_version}.sb")

    # Remove existing image
    if os.path.exists(output_image):
        os.remove(output_image)

    # Find modules directory in extracted deb contents - look for any kernel version
    modules_base_paths = [
        os.path.join(temp_dir, "usr", "lib", "modules"),
        os.path.join(temp_dir, "lib", "modules")
    ]

    modules_path = None
    original_kernel_version = None
    for base_path in modules_base_paths:
        if os.path.exists(base_path):
            # Find the first (and usually only) kernel version directory
            version_dirs = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]
            if version_dirs:
                original_kernel_version = version_dirs[0]  # Store original version from package
                modules_path = os.path.join(base_path, original_kernel_version)
                break

    if not modules_path:
        raise RuntimeError(_("Kernel modules not found in extracted deb package for kernel {kernel_version}").format(kernel_version=kernel_version))

    # Use system modules base path instead of package path
    system_modules_base = get_system_modules_base()
    print(f"I: {_('Using system modules base: {base}').format(base=system_modules_base)}")

    # Create temporary structure with proper paths for SquashFS
    # Use parent directory of temp_dir as base to keep all temp files in one location
    base_temp_dir = os.path.dirname(temp_dir) if temp_dir else None
    temp_squashfs_dir = tempfile.mkdtemp(prefix=f"minios-kernel-{kernel_version}-squashfs-", dir=base_temp_dir)

    # Fix permissions to 755 to ensure proper access to SquashFS contents
    # tempfile.mkdtemp() creates directories with 700 by default, but SquashFS needs 755
    os.chmod(temp_squashfs_dir, 0o755)
    target_modules_dir = os.path.join(temp_squashfs_dir, system_modules_base)
    os.makedirs(target_modules_dir, exist_ok=True)

    # Copy modules to proper structure using ORIGINAL kernel version (so kernel can find them)
    shutil.copytree(modules_path, os.path.join(target_modules_dir, original_kernel_version))

    # Generate modules.dep and other module dependency files for SquashFS
    try:
        print(f"I: {_('Generating module dependencies for SquashFS')}")

        if system_modules_base == "usr/lib/modules":
            # For usr/lib/modules structure, point depmod to usr subdirectory
            depmod_basedir = os.path.join(temp_squashfs_dir, "usr")
            depmod_result = subprocess.run(['depmod', '-b', depmod_basedir, original_kernel_version],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        else:
            # For lib/modules structure (traditional)
            depmod_result = subprocess.run(['depmod', '-b', temp_squashfs_dir, original_kernel_version],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        if depmod_result.returncode != 0:
            error_msg = depmod_result.stderr.strip()
            # Stop build on any ERROR, continue on WARNING
            if "ERROR:" in error_msg:
                raise RuntimeError(_('Critical depmod error: {}').format(error_msg))
            print(f"W: {_('depmod failed: {}').format(error_msg)}")
        else:
            print(f"I: {_('Module dependencies generated successfully')}")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
        print(f"W: {_('Failed to generate module dependencies: {}').format(str(e))}")

    # Use the modules directory structure directly as source for SquashFS
    source_path = system_modules_base
    print(f"I: {_('Using extracted deb modules with structure: {path}').format(path=f'{temp_squashfs_dir}/{system_modules_base}/{original_kernel_version}')}")

    # Get compression parameters
    comp_params = get_compression_params(compression, 'squashfs')

    # Check mksquashfs version for -no-strip support and availability
    try:
        result = subprocess.run(['mksquashfs', '-version'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
        # Version info can be in stdout or stderr
        version_output = result.stdout if result.stdout else result.stderr
        version_lines = version_output.split('\n')
        version_line = next((line for line in version_lines if 'version' in line.lower()), '')

        if version_line:
            # Extract version number - handle different formats
            import re
            version_match = re.search(r'version\s+(\d+)\.(\d+)', version_line.lower())
            if version_match:
                major, minor = int(version_match.group(1)), int(version_match.group(2))
                use_no_strip = (major > 4) or (major == 4 and minor >= 5)
                print(f"DEBUG: {_('mksquashfs version detected: {major}.{minor}, no-strip support: {use_no_strip}').format(major=major, minor=minor, use_no_strip=use_no_strip)}", flush=True)
            else:
                print(f"DEBUG: {_('Could not parse version from: {version_line}').format(version_line=version_line)}", flush=True)
                use_no_strip = False
        else:
            print(f"DEBUG: {_('No version line found in output')}", flush=True)
            use_no_strip = False
    except (subprocess.CalledProcessError, ValueError, IndexError) as e:
        print(f"DEBUG: {_('mksquashfs version check failed: {error}').format(error=str(e))}", flush=True)
        use_no_strip = False
    except FileNotFoundError:
        raise RuntimeError(_("mksquashfs command not found. Please install squashfs-tools package."))

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
        '-noappend'
    ])

    if use_no_strip:
        cmd.append('-no-strip')

    # Validate command arguments
    for i, arg in enumerate(cmd):
        if not arg or not isinstance(arg, str):
            raise RuntimeError(f"Invalid command argument at position {i}: {repr(arg)}")

    # Execute mksquashfs
    print(f"I: {_('Starting SquashFS compression with {compression}...').format(compression=compression)}", flush=True)

    # Debug: print the exact command being executed
    print(f"DEBUG: {_('mksquashfs command: {command}').format(command=' '.join(cmd))}", flush=True)

    # Validate paths before execution (check relative to temp_squashfs_dir)
    full_source_path = os.path.join(temp_squashfs_dir, source_path)
    if not os.path.exists(full_source_path):
        raise RuntimeError(f"Source path does not exist: {full_source_path}")
    if not os.path.isdir(full_source_path):
        raise RuntimeError(f"Source path is not a directory: {full_source_path}")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_image), exist_ok=True)

    # Change working directory to temp_squashfs_dir to use relative paths
    old_cwd = os.getcwd()
    try:
        os.chdir(temp_squashfs_dir)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    finally:
        # Always restore working directory after starting process
        os.chdir(old_cwd)

    stdout_lines = []
    stderr_lines = []

    # Capture both stdout and stderr
    try:
        stdout, stderr = process.communicate()
        stdout_lines = stdout.splitlines() if stdout else []
        stderr_lines = stderr.splitlines() if stderr else []

        if logger:
            for line in stdout_lines:
                if line.strip():
                    logger(line.strip())
        else:
            # Show progress for standalone execution
            for line in stdout_lines:
                if line.strip() and ('[' in line and ']' in line):
                    # This is a progress line from mksquashfs
                    print(f"\r{line.strip()}", end="", flush=True)
    except Exception as e:
        process.kill()
        if os.path.exists(temp_squashfs_dir):
            shutil.rmtree(temp_squashfs_dir)
        raise RuntimeError(f"Failed to execute mksquashfs: {str(e)}")

    if process.returncode != 0:
        # Cleanup temporary directory
        if os.path.exists(temp_squashfs_dir):
            shutil.rmtree(temp_squashfs_dir)

        # Combine error output for better debugging
        error_msg = _("Failed to create SquashFS image")
        if stderr_lines:
            error_msg += f". Error: {' '.join(stderr_lines)}"
        if stdout_lines:
            error_msg += f". Output: {' '.join(stdout_lines[-5:])}"  # Last 5 lines

        raise RuntimeError(error_msg)

    print(f"\nI: {_('SquashFS image completed: {path}').format(path=output_image)}", flush=True)

    # Cleanup temporary directory
    if os.path.exists(temp_squashfs_dir):
        shutil.rmtree(temp_squashfs_dir)

    return output_image


def generate_initramfs(kernel_version: str, output_dir: str, logger: Optional[Callable] = None, temp_dir: str = None, custom_temp_dir: str = None, original_kernel_version: str = None) -> str:
    """Generate initramfs image using either livekit or dracut"""
    # Use original kernel version if provided, otherwise use kernel_version
    build_version = original_kernel_version if original_kernel_version else kernel_version
    output_image = os.path.join(output_dir, f"initrfs-{kernel_version}.img")

    # Detect which initramfs builder to use
    try:
        builder = detect_initramfs_builder()
    except RuntimeError as e:
        raise RuntimeError(_("Failed to detect initramfs builder: {}").format(str(e)))

    print(f"I: {_('Using initramfs builder: {}').format(builder)}", flush=True)

    # Get modules directory
    modules_dir = get_non_symlink_modules_dir()

    if builder == 'dracut':
        return _generate_initramfs_dracut(kernel_version, build_version, output_image, modules_dir, temp_dir, custom_temp_dir, logger)
    else:  # livekit
        return _generate_initramfs_livekit(kernel_version, build_version, output_image, modules_dir, temp_dir, custom_temp_dir, logger)


def _generate_initramfs_dracut(kernel_version: str, build_version: str, output_image: str,
                                modules_dir: str, temp_dir: str = None, custom_temp_dir: str = None, logger: Optional[Callable] = None) -> str:
    """Generate initramfs using dracut/mkdracut"""
    mkdracut_path = "/run/initramfs/dracut-mos/mkdracut"
    if not os.path.exists(mkdracut_path):
        raise RuntimeError(_("mkdracut not found - this tool requires MiniOS live environment"))

    # Handle module path for extracted deb packages
    system_modules_path = os.path.join(modules_dir, build_version)
    temp_symlink_created = False

    if temp_dir and os.path.exists(temp_dir):
        # Find modules directory in extracted deb contents
        possible_modules_paths = [
            os.path.join(temp_dir, "usr", "lib", "modules", build_version),
            os.path.join(temp_dir, "lib", "modules", build_version)
        ]

        extracted_modules_path = None
        for path in possible_modules_paths:
            if os.path.exists(path):
                extracted_modules_path = path
                break

        if extracted_modules_path and not os.path.exists(system_modules_path):
            # Create temporary symlink for dracut
            try:
                os.makedirs(modules_dir, exist_ok=True)
                os.symlink(extracted_modules_path, system_modules_path)
                temp_symlink_created = True
                print(f"I: {_('Created temporary symlink: {} -> {}').format(system_modules_path, extracted_modules_path)}", flush=True)

                # Generate modules.dep
                try:
                    print(f"I: {_('Generating modules.dep for {}').format(build_version)}", flush=True)
                    depmod_result = subprocess.run(['depmod', '-b', temp_dir, build_version],
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
                    if depmod_result.returncode == 0:
                        print(f"I: {_('Successfully generated modules.dep')}", flush=True)
                    else:
                        print(f"W: {_('depmod warning: {}').format(depmod_result.stderr)}", flush=True)
                except Exception as e:
                    print(f"W: {_('Failed to run depmod: {}').format(e)}", flush=True)
            except OSError as e:
                print(f"W: {_('Failed to create symlink {}: {}').format(system_modules_path, e)}", flush=True)

    # Store symlink info for cleanup
    cleanup_symlink = system_modules_path if temp_symlink_created else None

    # Execute mkdracut with parameters: -k KERNEL -n -c
    cmd = [mkdracut_path, "-k", build_version, "-n", "-c", "-o", output_image]

    # Prepare environment with custom temp directory if provided
    env = os.environ.copy()
    if custom_temp_dir:
        env['TMPDIR'] = custom_temp_dir
        print(f"I: {_('Using custom temporary directory for initramfs: {}').format(custom_temp_dir)}", flush=True)

    # Run mkdracut with real-time output
    print(f"I: {_('Starting dracut initramfs generation...')}", flush=True)
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 universal_newlines=True, bufsize=1, universal_newlines=True, env=env)

        output_lines = []
        while True:
            line = process.stdout.readline()
            if not line:
                break
            output_lines.append(line)

            # Show output from mkdracut
            line_stripped = line.strip()
            if line_stripped:
                if not (line_stripped.startswith('+') or line_stripped.startswith('++')):
                    if any(line_stripped.startswith(prefix) for prefix in ['I: ', 'E: ', 'W: ', 'D: ']):
                        print(line_stripped, flush=True)
                    else:
                        print(f"I: {line_stripped}", flush=True)

        process.wait()
        if process.returncode != 0:
            raise RuntimeError(_("mkdracut failed with return code {code}").format(code=process.returncode))

    except Exception as e:
        if cleanup_symlink and os.path.islink(cleanup_symlink):
            os.unlink(cleanup_symlink)
        raise RuntimeError(_("Failed to generate initramfs with dracut: {}").format(str(e)))
    finally:
        # Cleanup temporary symlink
        if cleanup_symlink and os.path.islink(cleanup_symlink):
            try:
                os.unlink(cleanup_symlink)
                print(f"I: {_('Removed temporary symlink: {}').format(cleanup_symlink)}", flush=True)
            except OSError:
                pass

    if not os.path.exists(output_image):
        raise RuntimeError(_("Dracut succeeded but output file not found: {}").format(output_image))

    print(f"I: {_('Successfully generated initramfs: {}').format(output_image)}", flush=True)
    return output_image


def _generate_initramfs_livekit(kernel_version: str, build_version: str, output_image: str,
                                 modules_dir: str, temp_dir: str = None, custom_temp_dir: str = None, logger: Optional[Callable] = None) -> str:
    """Generate initramfs using livekit/mkinitrfs"""
    # Check if mkinitrfs exists
    mkinitrfs_path = "/run/initramfs/mkinitrfs"
    if not os.path.exists(mkinitrfs_path):
        raise RuntimeError(_("mkinitrfs not found - this tool requires MiniOS live environment"))

    # Handle module path for extracted deb packages
    system_modules_path = os.path.join(modules_dir, build_version)
    temp_symlink_created = False

    if temp_dir and os.path.exists(temp_dir):
        # Find modules directory in extracted deb contents
        possible_modules_paths = [
            os.path.join(temp_dir, "usr", "lib", "modules", build_version),
            os.path.join(temp_dir, "lib", "modules", build_version)
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

                    # Generate modules.dep for the symlinked modules before calling mkinitrfs
                    try:
                        print(f"I: {_('Generating module dependencies for initramfs')}")
                        depmod_result = subprocess.run(['depmod', build_version],
                                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
                        if depmod_result.returncode != 0:
                            error_msg = depmod_result.stderr.strip()
                            # Stop build on any ERROR, continue on WARNING
                            if "ERROR:" in error_msg:
                                raise RuntimeError(_('Critical depmod error: {}').format(error_msg))
                            print(f"W: {_('depmod failed: {}').format(error_msg)}")
                        else:
                            print(f"I: {_('Module dependencies generated successfully')}")
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
                        print(f"W: {_('Failed to generate module dependencies: {}').format(str(e))}")
            except OSError as e:
                print(f"Warning: Failed to create symlink {system_modules_path}: {e}")

    # Store symlink info for cleanup
    cleanup_symlink = system_modules_path if temp_symlink_created else None

    # Execute mkinitrfs with default parameters: -k KERNEL -n -c -dm
    cmd = [mkinitrfs_path, "-k", build_version, "-n", "-c", "-dm"]

    # Add config file path if available
    if temp_dir:
        config_path = os.path.join(temp_dir, "boot", f"config-{build_version}")
        if os.path.exists(config_path):
            cmd.extend(["--config-file", config_path])

    # Prepare environment with custom temp directory if provided
    env = os.environ.copy()
    if custom_temp_dir:
        env['TMPDIR'] = custom_temp_dir
        print(f"I: {_('Using custom temporary directory for initramfs: {}').format(custom_temp_dir)}", flush=True)

    # Run mkinitrfs with real-time output
    print(f"I: {_('Starting initramfs generation...')}", flush=True)
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 universal_newlines=True, bufsize=1, universal_newlines=True, env=env)

        output_lines = []
        while True:
            line = process.stdout.readline()
            if not line:
                break
            output_lines.append(line)

            # Show ALL output from mkinitrfs (includes stderr since it's redirected)
            line_stripped = line.strip()
            if line_stripped:
                # Print all mkinitrfs output to stdout - this ensures complete visibility
                # Skip only shell debug lines that start with + or ++
                if not (line_stripped.startswith('+') or line_stripped.startswith('++')):
                    # Add prefix only if line doesn't already have one
                    if any(line_stripped.startswith(prefix) for prefix in ['I: ', 'E: ', 'W: ', 'D: ']):
                        # Line already has a prefix from mkinitrfs - show as is
                        print(line_stripped, flush=True)
                    else:
                        # Add our own prefix for unprefixed mkinitrfs output
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
    temp_dir_parent = os.path.dirname(os.path.dirname(temp_initramfs_path)) if temp_initramfs_path else None
    if temp_dir_parent:
        log_source = os.path.join(temp_dir_parent, "livekit", "initramfs.log")
        if os.path.exists(log_source):
            log_dest = os.path.join(os.path.dirname(output_image), f"initramfs-{kernel_version}.log")
            shutil.copy2(log_source, log_dest)

    return output_image