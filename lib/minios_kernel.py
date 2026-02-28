#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniOS Kernel CLI Tool

Command-line interface for packaging and managing kernels in MiniOS.
This script handles kernel packaging operations from the command line.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import shutil
import gettext
import locale
import threading
import time
import signal
import atexit

# Set up localization
locale.setlocale(locale.LC_ALL, '')
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext

# Global variable to track temporary directory for cleanup
_temp_dir = None

def cleanup_temp_dir():
    """Clean up temporary directory"""
    global _temp_dir
    if _temp_dir and os.path.exists(_temp_dir):
        try:
            print("I: {}".format(_('Cleaning up on interruption: {}')).format(_temp_dir), flush=True)
            shutil.rmtree(_temp_dir)
            print("I: {}".format(_('Temporary directory cleaned up')), flush=True)
        except Exception as e:
            print("W: {}".format(_('Failed to clean temporary directory: {}')).format(str(e)), flush=True)
        _temp_dir = None

def signal_handler(signum, frame):
    """Handle interruption signals"""
    signal_names = {2: 'SIGINT', 15: 'SIGTERM', 9: 'SIGKILL'}
    signal_name = signal_names.get(signum, f'Signal {signum}')

    if not getattr(signal_handler, '_already_handling', False):
        signal_handler._already_handling = True
        print("I: {}".format(_('Received {} - cleaning up...')).format(signal_name), flush=True)
        cleanup_temp_dir()

        # Exit with appropriate code
        sys.exit(128 + signum)

# Register signal handlers and cleanup function
signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # Termination request
atexit.register(cleanup_temp_dir)  # Normal exit cleanup

# Use only system installed modules
try:
    # Try relative imports first (when imported as module)
    from .kernel_utils import (
        download_kernel_package, process_manual_packages, prepare_temp_modules,
        cleanup_temp_modules, get_last_kernel_versions
    )
    from .build_utils import create_squashfs_image, generate_initramfs, copy_vmlinuz
    from .minios_utils import (
        find_minios_directory, activate_kernel, list_all_kernels, get_active_kernel,
        get_temp_dir_with_space_check, is_kernel_currently_running
    )
except ImportError:
    # Fall back to absolute imports (when run as main script)
    from kernel_utils import (
        download_kernel_package, process_manual_packages, prepare_temp_modules,
        cleanup_temp_modules, get_last_kernel_versions
    )
    from build_utils import create_squashfs_image, generate_initramfs, copy_vmlinuz
    from minios_utils import (
        find_minios_directory, activate_kernel, list_all_kernels, get_active_kernel,
        get_temp_dir_with_space_check, is_kernel_currently_running
    )

def activity_indicator(stop_event, message):
    """Show activity indicator during long operations"""
    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧']
    i = 0
    while not stop_event.is_set():
        print(f"\r{spinner[i % len(spinner)]} {message}", end="", flush=True)
        time.sleep(0.1)
        i += 1
    print("\r", end="", flush=True)  # Clear line


def package_kernel(args):
    """Package a kernel from repository or deb file."""
    def progress_print(percent, message=None):
        """Print progress information - only for JSON mode GUI progress bar."""
        if args.json:
            progress_data = {
                "type": "progress",
                "percent": percent,
                "message": message if message else ""
            }
            print(json.dumps(progress_data), flush=True)

    temp_dir = None
    kernel_version = None
    global _temp_dir

    try:
        # Ensure output directory exists and is writable
        if not os.path.exists(args.output):
            try:
                os.makedirs(args.output, exist_ok=True)
                print("I: {}".format(_('Created output directory: {}')).format(args.output), flush=True)
            except Exception as e:
                raise RuntimeError(_("Failed to create output directory '{}': {}").format(args.output, str(e)))

        # Check if output directory is writable
        if not os.access(args.output, os.W_OK):
            raise RuntimeError(_("Output directory '{}' is not writable").format(args.output))

        # Check available space and choose appropriate temporary directory
        # Use 1024MB estimate for full kernel packaging
        temp_dir = get_temp_dir_with_space_check(1024, "minios-kernel-", "kernel_packaging", args.temp_dir)
        _temp_dir = temp_dir  # Set global for signal handler

        # Create temporary directory message (always log for cleanup purposes)
        print("I: {}".format(_('Created temporary directory: {}')).format(temp_dir), flush=True)

        if args.repo:
            progress_print(10, _("Downloading kernel package {}").format(args.repo))
            kernel_version = download_kernel_package(args.repo, temp_dir, args.force_update)
        else: # args.deb
            progress_print(10, _("Processing manual package(s)"))
            kernel_version = process_manual_packages(args.deb, temp_dir)

        progress_print(30, _("Download completed"))
        progress_print(35, _("Extracting package"))

        progress_print(40, _("Preparing kernel modules"))
        # Skip system installation for packaging - modules will be used directly from temp_dir

        progress_print(50, _("Copying kernel files"))
        copy_vmlinuz(kernel_version, temp_dir, args.output)

        progress_print(60, _("Creating SquashFS image"))
        create_squashfs_image(kernel_version, args.sqfs_comp, args.output, logger=None, temp_dir=temp_dir)

        progress_print(80, _("Generating initramfs"))
        # This will require running as root if it calls a privileged helper
        # Pass the same custom temp dir that was used for the main packaging
        custom_initramfs_temp = args.temp_dir if args.temp_dir else None

        # Get the actual kernel version for system operations (modules, initramfs)
        # This was determined during the download/extraction process
        original_kernel_version = get_last_kernel_versions().get('actual_version')

        if not original_kernel_version and temp_dir:
            # Fallback: find the actual kernel version from vmlinuz or modules directory
            boot_paths = [
                os.path.join(temp_dir, "boot"),
                os.path.join(temp_dir, "usr", "boot")
            ]

            # First try to get version from vmlinuz filename
            for boot_path in boot_paths:
                if os.path.exists(boot_path):
                    for item in os.listdir(boot_path):
                        if item.startswith("vmlinuz-"):
                            original_kernel_version = item.replace("vmlinuz-", "")
                            break
                    if original_kernel_version:
                        break

            # Fallback to modules directory
            if not original_kernel_version:
                modules_dir = os.path.join(temp_dir, "lib", "modules")
                if not os.path.exists(modules_dir):
                    modules_dir = os.path.join(temp_dir, "usr", "lib", "modules")
                if os.path.exists(modules_dir):
                    version_dirs = [d for d in os.listdir(modules_dir) if os.path.isdir(os.path.join(modules_dir, d))]
                    if version_dirs:
                        original_kernel_version = version_dirs[0]

        generate_initramfs(kernel_version, args.output, logger=None, temp_dir=temp_dir,
                         custom_temp_dir=custom_initramfs_temp, original_kernel_version=original_kernel_version)

        # Move packaged kernel to MiniOS kernels directory if not already there
        minios_path = find_minios_directory()
        if minios_path and args.output != os.path.join(minios_path, "kernels", kernel_version):
            progress_print(95, _("Installing to kernel repository"))
            from minios_utils import package_kernel_to_repository

            # Get the packaged files
            vmlinuz_file = os.path.join(args.output, f"vmlinuz-{kernel_version}")
            initramfs_file = os.path.join(args.output, f"initrfs-{kernel_version}.img")
            squashfs_file = os.path.join(args.output, f"01-kernel-{kernel_version}.sb")

            # Install to repository
            if all(os.path.exists(f) for f in [vmlinuz_file, initramfs_file, squashfs_file]):
                package_kernel_to_repository(minios_path, kernel_version,
                                           squashfs_file, vmlinuz_file, initramfs_file)
        else:
            progress_print(95, _("Finalizing installation"))

        progress_print(100, _("Kernel packaging completed successfully!"))

        # Clean up temporary directory on successful completion
        cleanup_temp_dir()

        # Final success message for JSON output
        if args.json:
            success_data = {
                "success": True,
                "message": "Kernel packaging completed successfully",
                "type": "success"
            }
            print(json.dumps(success_data), flush=True)

    except Exception as e:
        if args.json:
            error_data = {
                "success": False,
                "error": str(e),
                "type": "error"
            }
            print(json.dumps(error_data), file=sys.stderr, flush=True)
        else:
            print("E: {}".format(e), file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        # Clear global reference (but don't cleanup - only signal handlers should cleanup)
        _temp_dir = None

def list_kernels_cmd(args):
    """List available kernels."""
    # Find MiniOS directory
    minios_path = find_minios_directory()
    if not minios_path:
        if args.json:
            print(json.dumps({"error": "MiniOS directory not found", "kernels": []}))
        else:
            print("E: {}".format(_('MiniOS directory not found')), file=sys.stderr, flush=True)
        sys.exit(1)

    available_kernels = list_all_kernels(minios_path)
    current_kernel = get_active_kernel(minios_path)

    if args.json:
        kernels_json = []
        for kernel in available_kernels:
            is_active = kernel == current_kernel
            kernels_json.append({
                "version": kernel,
                "is_active": is_active,
                "is_running": is_kernel_currently_running(kernel),
                "status": "active" if is_active else "available"
            })

        result = {
            "kernels": kernels_json,
            "active_kernel": current_kernel,
            "minios_path": minios_path
        }
        print(json.dumps(result, indent=2))
    else:
        print("{}:".format(_("Available kernels")))
        for kernel in available_kernels:
            status = " ({})".format(_("active")) if kernel == current_kernel else ""
            print("  - {}{}".format(kernel, status))

        if current_kernel:
            print("\n{}: {}".format(_("Currently active kernel"), current_kernel))
        else:
            print("\n{}".format(_("No currently active kernel found")))

def activate_kernel_cmd(args):
    """Activate a kernel from the repository."""
    # Find MiniOS directory
    minios_path = find_minios_directory()
    if not minios_path:
        if args.json:
            print(json.dumps({"success": False, "error": "MiniOS directory not found"}))
        else:
            print("E: {}".format(_('MiniOS directory not found')), file=sys.stderr, flush=True)
        sys.exit(1)

    if not args.json:
        print("I: {}".format(_('Found MiniOS directory: {}')).format(minios_path), flush=True)

    # List available kernels
    available_kernels = list_all_kernels(minios_path)

    # Check current active kernel
    current_kernel = get_active_kernel(minios_path)
    if current_kernel and not args.json:
        print("I: {}".format(_('Currently active kernel: {}')).format(current_kernel), flush=True)

    # Check if requested kernel is available
    if args.kernel_version not in available_kernels:
        error_msg = f"Kernel {args.kernel_version} not found in repository"
        if args.json:
            print(json.dumps({
                "success": False,
                "error": error_msg,
                "available_kernels": available_kernels
            }))
        else:
            print("E: {}".format(error_msg), file=sys.stderr, flush=True)
            print("{}: {}".format(_('Available kernels'), ', '.join(available_kernels)), file=sys.stderr, flush=True)
        sys.exit(1)

    # Check if kernel is already active
    if args.kernel_version == current_kernel:
        if args.json:
            print(json.dumps({
                "success": True,
                "message": f"Kernel {args.kernel_version} is already active",
                "kernel_version": args.kernel_version,
                "already_active": True
            }))
        else:
            print("I: {}".format(_('Kernel {} is already active')).format(args.kernel_version), flush=True)
        return

    # Activate the kernel
    if not args.json:
        print("I: {}".format(_('Activating kernel {}...')).format(args.kernel_version), flush=True)

    success = activate_kernel(minios_path, args.kernel_version)

    if args.json:
        print(json.dumps({
            "success": success,
            "kernel_version": args.kernel_version,
            "previous_kernel": current_kernel,
            "message": f"Kernel {args.kernel_version} activated successfully" if success else f"Failed to activate kernel {args.kernel_version}"
        }))
    else:
        if success:
            print("I: {}".format(_('Kernel {} activated successfully!')).format(args.kernel_version), flush=True)
        else:
            print("E: {}".format(_('Failed to activate kernel {}')).format(args.kernel_version), file=sys.stderr, flush=True)
            sys.exit(1)

def info_kernel_cmd(args):
    """Show kernel information."""
    # Find MiniOS directory
    minios_path = find_minios_directory()
    if not minios_path:
        if args.json:
            print(json.dumps({"error": "MiniOS directory not found"}))
        else:
            print("E: {}".format(_('MiniOS directory not found')), file=sys.stderr, flush=True)
        sys.exit(1)

    current_kernel = get_active_kernel(minios_path)
    available_kernels = list_all_kernels(minios_path)

    if args.kernel_version:
        target_kernel = args.kernel_version
        if target_kernel not in available_kernels:
            error_msg = _("Kernel {} not found").format(target_kernel)
            if args.json:
                print(json.dumps({"error": error_msg, "available_kernels": available_kernels}))
            else:
                print("E: {}".format(error_msg), file=sys.stderr, flush=True)
            sys.exit(1)
    else:
        target_kernel = current_kernel
        if not target_kernel:
            error_msg = _("No active kernel found")
            if args.json:
                print(json.dumps({"error": error_msg, "available_kernels": available_kernels}))
            else:
                print("E: {}".format(error_msg), file=sys.stderr, flush=True)
            sys.exit(1)

    if args.json:
        info = {
            "kernel_version": target_kernel,
            "is_active": target_kernel == current_kernel,
            "is_running": is_kernel_currently_running(target_kernel),
            "minios_path": minios_path,
            "available_kernels": available_kernels,
            "active_kernel": current_kernel
        }
        print(json.dumps(info, indent=2))
    else:
        print("{}: {}".format(_("Kernel"), target_kernel))
        status_text = _("Active") if target_kernel == current_kernel else _("Available")
        print("{}: {}".format(_("Status"), status_text))
        print("{}: {}".format(_("MiniOS path"), minios_path))
        if current_kernel:
            print("{}: {}".format(_("Current active kernel"), current_kernel))
        print("{}: {}".format(_("Total available kernels"), len(available_kernels)))

def status_cmd(args):
    """Check MiniOS directory status and write permissions."""
    # Find MiniOS directory
    minios_path = find_minios_directory()
    if not minios_path:
        error_msg = _("MiniOS directory not found")
        if args.json:
            print(json.dumps({"success": False, "error": error_msg, "found": False, "writable": False}))
        else:
            print("E: {}".format(error_msg), file=sys.stderr, flush=True)
        sys.exit(1)

    # Check if directory is writable
    writable = False
    fs_type = "unknown"
    error_msg = None

    try:
        # Get filesystem type
        import subprocess
        try:
            result = subprocess.run(['stat', '-f', '-c', '%T', minios_path],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
            fs_type = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback method using /proc/mounts
            try:
                with open('/proc/mounts', 'r') as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            mount_point, fs_type_mount = parts[1], parts[2]
                            if minios_path.startswith(mount_point):
                                fs_type = fs_type_mount
                                break
            except Exception:
                pass

        # SquashFS is always read-only
        if fs_type == 'squashfs':
            writable = False
            error_msg = _("Directory is on a SquashFS filesystem (read-only)")
        else:
            # Try to create a temporary file to test write access
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(dir=minios_path, delete=True):
                    pass
                writable = True
            except (OSError, PermissionError) as e:
                writable = False
                error_msg = _("Permission denied: {}").format(str(e))

    except Exception as e:
        writable = False
        error_msg = _("Error checking directory: {}").format(str(e))

    if args.json:
        result = {
            "success": True,
            "minios_path": minios_path,
            "found": True,
            "writable": writable,
            "filesystem_type": fs_type
        }
        if error_msg:
            result["error"] = error_msg
        print(json.dumps(result))
    else:
        print("{}: {}".format(_("MiniOS path"), minios_path))
        print("{}: {}".format(_("Filesystem type"), fs_type))
        if writable:
            print("{}: {}".format(_("Status"), _("Writable")))
        else:
            print("{}: {}".format(_("Status"), _("Read-only")))
            if error_msg:
                print("{}: {}".format(_("Reason"), error_msg))

def delete_kernel_cmd(args):
    """Delete a packaged kernel"""
    from minios_utils import find_minios_directory, delete_packaged_kernel

    minios_path = find_minios_directory()
    if not minios_path:
        error_msg = _("MiniOS directory not found")
        if args.json:
            print(json.dumps({"success": False, "error": error_msg}))
        else:
            print(error_msg, file=sys.stderr)
        sys.exit(1)

    kernel_version = args.kernel_version
    success = delete_packaged_kernel(minios_path, kernel_version)

    if args.json:
        result = {"success": success}
        if success:
            result["message"] = _("Kernel {} deleted successfully").format(kernel_version)
        else:
            result["error"] = _("Failed to delete kernel {}").format(kernel_version)
        print(json.dumps(result))
    else:
        if success:
            print(_("Kernel {} deleted successfully").format(kernel_version))
        else:
            print(_("Failed to delete kernel {}").format(kernel_version), file=sys.stderr)
            sys.exit(1)

def main():
    """Main entry point for the CLI utility."""
    # Pre-check for --json flag before parsing
    json_output = '--json' in sys.argv

    # Check for root privileges
    if os.geteuid() != 0:
        error_msg = _("This tool requires root privileges. Please run with sudo or through pkexec.")
        if json_output:
            print(json.dumps({"success": False, "error": error_msg}, ensure_ascii=False), file=sys.stderr)
        else:
            print(error_msg, file=sys.stderr)
        sys.exit(1)

    # Ensure unbuffered output for real-time GUI updates (Python 3.6 compatible)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, line_buffering=True)

    parser = argparse.ArgumentParser(description=_("MiniOS Kernel Manager CLI"))
    parser.add_argument('--json', action='store_true', help=_('Output in JSON format'))

    # Global options
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument('--json', action='store_true', help=_('Output in JSON format'))

    subparsers = parser.add_subparsers(dest='command', help=_('Available commands'))

    # Package command
    package_parser = subparsers.add_parser('package', help=_('Package a kernel'), parents=[parent_parser])
    source_group = package_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--repo", help=_("Name of the kernel package in the repository"))
    source_group.add_argument("--deb", nargs='+', help=_("Path(s) to kernel .deb package(s)"))
    package_parser.add_argument("-o", "--output", required=True, help=_("Directory to save the packaged kernel files"))
    package_parser.add_argument("--sqfs-comp", default="zstd", help=_("Compression method for SquashFS"))
    package_parser.add_argument("--temp-dir", help=_("Custom temporary directory (must have at least 1024MB free space)"))
    package_parser.add_argument("--force-update", action="store_true", help=_("Force package lists update if outdated"))

    # List command
    list_parser = subparsers.add_parser('list', help=_('List available kernels'), parents=[parent_parser])

    # Activate command
    activate_parser = subparsers.add_parser('activate', help=_('Activate a kernel'), parents=[parent_parser])
    activate_parser.add_argument("kernel_version", help=_("Kernel version to activate"))

    # Info command
    info_parser = subparsers.add_parser('info', help=_('Show kernel information'), parents=[parent_parser])
    info_parser.add_argument("kernel_version", nargs='?', help=_("Kernel version to get info about (current if not specified)"))

    # Status command
    status_parser = subparsers.add_parser('status', help=_('Check MiniOS directory status'), parents=[parent_parser])

    # Delete command
    delete_parser = subparsers.add_parser('delete', help=_('Delete a packaged kernel'), parents=[parent_parser])
    delete_parser.add_argument("kernel_version", help=_("Kernel version to delete"))

    # Parse arguments - handle global flags that can appear anywhere
    # Extract global flags from any position
    global_json = '--json' in sys.argv

    args = parser.parse_args()

    # Apply global flags
    if global_json:
        args.json = True

    # Handle missing subcommand
    if not args.command:
        parser.print_help()
        sys.exit(1)
    elif args.command == 'package':
        package_kernel(args)
    elif args.command == 'list':
        list_kernels_cmd(args)
    elif args.command == 'activate':
        activate_kernel_cmd(args)
    elif args.command == 'info':
        info_kernel_cmd(args)
    elif args.command == 'status':
        status_cmd(args)
    elif args.command == 'delete':
        delete_kernel_cmd(args)

if __name__ == "__main__":
    main()
