# MiniOS Kernel Manager

GTK3 graphical tool for managing Linux kernels in MiniOS. Package kernels from repository or .deb files, activate different versions, and manage kernel installations.

## Components

- **minios-kernel-manager** - GTK3 GUI application
- **minios-kernel** - CLI backend for kernel operations

## Usage

```bash
# GUI application
minios-kernel-manager

# CLI commands
minios-kernel list
minios-kernel activate <version>
minios-kernel package --repo <package> -o <output>
```

## Build

```bash
make build    # Build translations
sudo make install
```

## License

GPL-3.0+

## Author

crims0n <crims0n@minios.dev>