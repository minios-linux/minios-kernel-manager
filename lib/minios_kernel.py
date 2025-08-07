#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniOS Kernel CLI Tool

Command-line interface for packaging kernels in MiniOS.
This script handles kernel packaging operations from the command line.
"""

import argparse
import os
import sys
import tempfile
import shutil
import gettext
import locale
import threading
import time

# Set up localization
locale.setlocale(locale.LC_ALL, '')
gettext.bindtextdomain('minios-kernel-manager', '/usr/share/locale')
gettext.textdomain('minios-kernel-manager')
_ = gettext.gettext

# This check is to ensure the script can be run from the source tree
script_dir = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(script_dir) == 'lib':
    # Running from source tree
    sys.path.insert(0, os.path.dirname(script_dir))
    from lib.kernel_utils import (
        download_kernel_package, process_manual_package, prepare_temp_modules, cleanup_temp_modules
    )
    from lib.build_utils import create_squashfs_image, generate_initramfs, copy_vmlinuz
else:
    # Running from installed location
    from kernel_utils import (
        download_kernel_package, process_manual_package, prepare_temp_modules, cleanup_temp_modules
    )
    from build_utils import create_squashfs_image, generate_initramfs, copy_vmlinuz

def activity_indicator(stop_event, message):
    """Show activity indicator during long operations"""
    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧']
    i = 0
    while not stop_event.is_set():
        print(f"\r{spinner[i % len(spinner)]} {message}", end="", flush=True)
        time.sleep(0.1)
        i += 1
    print("\r", end="", flush=True)  # Clear line

def main():
    """Main entry point for the CLI utility."""
    # Ensure unbuffered output for real-time GUI updates
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    
    parser = argparse.ArgumentParser(description=_("MiniOS Kernel CLI Tool"))
    
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-repo", help=_("Name of the kernel package in the repository"))
    source_group.add_argument("--source-deb", help=_("Path to the kernel .deb package"))

    parser.add_argument("--output-dir", required=True, help=_("Directory to save the packaged kernel files"))
    parser.add_argument("--squashfs-comp", default="zstd", help=_("Compression method for SquashFS"))
    parser.add_argument("--initrd-comp", default="zstd", help=_("Compression method for initramfs"))
    parser.add_argument("--progress", action="store_true", help=_("Enable progress output for GUI"))

    args = parser.parse_args()

    def progress_print(percent, message=None):
        """Print progress in a format that GUI can parse."""
        if args.progress:
            if message:
                print(f"PROGRESS:{percent}:{message}", flush=True)
            else:
                print(f"PROGRESS:{percent}", flush=True)
        elif message:
            print(message, flush=True)

    temp_dir = None
    kernel_version = None

    try:
        temp_dir = tempfile.mkdtemp()
        
        # Create temporary directory message
        print("I: Created temporary directory: {}".format(temp_dir), flush=True)
        progress_print(5, _("Starting download"))

        if args.source_repo:
            print("I: Downloading kernel package {}...".format(args.source_repo), flush=True)
            progress_print(10, _("Downloading kernel package"))
            kernel_version = download_kernel_package(args.source_repo, temp_dir)
        else: # args.source_deb
            print("I: Processing manual package {}...".format(args.source_deb), flush=True)
            progress_print(10, _("Processing manual package"))
            kernel_version = process_manual_package(args.source_deb, temp_dir)

        progress_print(30, _("Download completed"))
        progress_print(35, _("Extracting package"))

        progress_print(40, _("Preparing kernel modules"))
        # Skip system installation for packaging - modules will be used directly from temp_dir

        progress_print(50, _("Copying kernel files"))
        copy_vmlinuz(kernel_version, temp_dir, args.output_dir)

        progress_print(60, _("Creating SquashFS image"))
        create_squashfs_image(kernel_version, args.squashfs_comp, args.output_dir, logger=None, temp_dir=temp_dir)

        progress_print(80, _("Generating initramfs"))
        # This will require running as root if it calls a privileged helper
        generate_initramfs(kernel_version, args.initrd_comp, args.output_dir, logger=None)

        progress_print(95, _("Finalizing installation"))

    except Exception as e:
        print("E: {}".format(e), file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        # Cleanup
        if temp_dir and os.path.exists(temp_dir):
            progress_print(100, _("Cleanup completed"))
            print("I: Removing temporary directory: {}".format(temp_dir), flush=True)
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    main()
