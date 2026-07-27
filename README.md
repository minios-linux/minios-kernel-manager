# MiniOS Kernel Manager 1.2.0

GTK3 and command-line tools for packaging, inspecting, activating, and removing
Linux kernels in MiniOS.

## Components

- **minios-kernel-manager** - GTK3 GUI application
- **minios-kernel** - CLI backend for kernel operations

## Usage

```bash
# GUI application
minios-kernel-manager

# CLI commands
minios-kernel list
minios-kernel info [version]
minios-kernel activate <version>
minios-kernel package --repo <package> -o <output>
minios-kernel package --deb linux-image.deb linux-modules.deb -o <output>
minios-kernel delete <version>
minios-kernel status
```

All non-help CLI commands require root privileges. Add `--json` for structured
output.

## Packaging

Repository mode resolves split `linux-modules-*` dependencies and requires APT
lists newer than 24 hours unless `--force-update` is used. Local mode accepts
one or more `.deb` files; split kernels normally require matching image,
modules, and modules-extra packages.

Packaging produces the kernel image, initramfs, and SquashFS module. Compact
dpkg metadata from the source packages is retained under
`/usr/share/minios/kernel-dpkg/` inside the SquashFS. When a writable MiniOS
root is detected, the completed package is staged into
`<MiniOS-root>/kernels/<version>`.

Supported SquashFS compression methods depend on installed tools. The default
is `zstd`; optional methods include `lz4`, `lzo`, `gzip`, `lzma`, `xz`, and
`bzip2`.

## Encrypted Persistence

When the running initrd advertises encrypted persistence, replacement kernels
must provide dm-crypt support. The generated initramfs is checked for
`cryptsetup`, `dm-crypt.ko`, and `etc/minios-initramfs-crypt`; packaging fails
rather than activating a kernel that would make encrypted persistence
unbootable.

## Storage and Activation

The MiniOS root is detected from the running initramfs, mounted live medium, or
supported mounted filesystems. Paths are relative to that root, for example:

```text
<MiniOS-root>/kernels/<version>/
<MiniOS-root>/boot/vmlinuz-<version>
<MiniOS-root>/boot/initrfs-<version>.img
<MiniOS-root>/01-kernel-<version>.sb
<MiniOS-root>/boot/active-kernel
```

Repository and bootloader writes are confined and atomic. Activation requires
a complete kernel package and GRUB configuration, updates SYSLINUX when present,
and rolls back staged files if bootloader generation fails. It does not reboot
the machine.

## Build

```bash
make build    # Build translations
sudo make install
# Staged installation
make install DESTDIR=/tmp/minios-kernel-manager
```

## License

GPL-3.0+

## Author

crims0n <crims0n@minios.dev>
