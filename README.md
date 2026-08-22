# MiniOS Kernel Manager 1.2.2

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

Repository mode uses the running system's APT sources, trust configuration, and
package lists. It accepts a real versioned package such as
`linux-image-6.12.94+deb13-amd64`, never a tracking metapackage such as
`linux-image-amd64`. Versioned split-kernel dependencies are downloaded when
required. `--force-update` explicitly runs `apt update`; otherwise existing
lists are used without an age gate.

Downloaded `.deb` files live only in the private packaging workspace and are
removed after success or handled failure. Local input `.deb` files remain at
their caller-owned paths.

Packaging produces the kernel image, initramfs, and SquashFS module. Compact
dpkg metadata from the source packages is retained under
`/usr/share/minios/kernel-dpkg/` inside the SquashFS. When a writable MiniOS
root is detected, the completed package is staged into
`<MiniOS-root>/kernels/<version>`.

Manager-produced bundles use canonical format 1 with `update_policy: frozen`:
the image package is manual and held, subordinate kernel packages are automatic
and unheld, and no repository configuration is embedded. Activation also
accepts canonical tracked bundles from `01-kernel`. Legacy 1.2.1 metadata is
accepted only for live activation, not as Installer-native format 1.

`/lib/modules/<version>` is the canonical runtime interface. On usrmerged
systems it resolves to `/usr/lib/modules/<version>`; on older layouts it remains
under `/lib`. The bundle records the physical path selected by the running base
system. Packaging refuses an already-present same-version module tree rather
than risk mixing old modules with a newly downloaded package revision.

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
