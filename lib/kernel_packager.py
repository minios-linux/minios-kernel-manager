#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniOS Kernel Packager CLI

This script handles the command-line operations for packaging kernels.
"""

import argparse
import os
import sys
import tempfile
import shutil

# This check is to ensure the script can be run from the source tree
if not os.path.exists('/usr/lib/minios-kernel-manager'):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(script_dir))

from kernel_utils import (
    download_kernel_package, process_manual_package, prepare_temp_modules, cleanup_temp_modules
)
from build_utils import create_squashfs_image, generate_initramfs, copy_vmlinuz

def main():
    """Main entry point for the CLI utility."""
    parser = argparse.ArgumentParser(description="MiniOS Kernel Packager")
    
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-repo", help="Name of the kernel package in the repository")
    source_group.add_argument("--source-deb", help="Path to the kernel .deb package")

    parser.add_argument("--output-dir", required=True, help="Directory to save the packaged kernel files")
    parser.add_argument("--squashfs-comp", default="zstd", help="Compression method for SquashFS")
    parser.add_argument("--initrd-comp", default="zstd", help="Compression method for initramfs")

    args = parser.parse_args()

    temp_dir = None
    temp_modules_installed = False
    kernel_version = None

    try:
        temp_dir = tempfile.mkdtemp()
        print(f"--- Created temporary directory: {temp_dir}")

        if args.source_repo:
            print(f"--- Downloading kernel package {args.source_repo}...")
            kernel_version = download_kernel_package(args.source_repo, temp_dir)
        else: # args.source_deb
            print(f"--- Processing manual package {args.source_deb}...")
            kernel_version = process_manual_package(args.source_deb, temp_dir)

        print(f"--- Extracted kernel version: {kernel_version}")

        print("--- Preparing kernel modules...")
        # The helper is not available in this context, so we assume root or necessary permissions
        prepare_temp_modules(kernel_version, temp_dir)
        temp_modules_installed = True

        print("--- Copying vmlinuz...")
        copy_vmlinuz(kernel_version, temp_dir, args.output_dir)

        print("--- Creating SquashFS image...")
        create_squashfs_image(kernel_version, args.squashfs_comp, args.output_dir, logger=print)

        print("--- Generating initramfs...")
        # This will require running as root if it calls a privileged helper
        generate_initramfs(kernel_version, args.initrd_comp, args.output_dir, logger=print)

        print(f"\n--- Kernel packaging complete. Files are in: {args.output_dir}")

    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Cleanup
        if temp_modules_installed and kernel_version:
            print(f"--- Cleaning up temporary modules for {kernel_version}...")
            cleanup_temp_modules(kernel_version)
        if temp_dir and os.path.exists(temp_dir):
            print(f"--- Removing temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    main()
