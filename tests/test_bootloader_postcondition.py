#!/usr/bin/env python3

def test_bootloader_postcondition_requires_exact_kernel_and_initramfs(tmp_path):
    from bootloader_utils import verify_bootloader_postcondition

    grub = tmp_path / 'boot/grub'
    grub.mkdir(parents=True)
    config = grub / 'grub.cfg'
    config.write_text(
        'linux /minios/boot/vmlinuz-new quiet\n'
        'initrd /minios/boot/initrfs-new.img\n', encoding='utf-8')
    assert verify_bootloader_postcondition(str(tmp_path), 'new') is True
    config.write_text(
        'linux /minios/boot/vmlinuz-new quiet\n'
        'initrd /minios/boot/initrfs-old.img\n', encoding='utf-8')
    assert verify_bootloader_postcondition(str(tmp_path), 'new') is False


def test_update_rejects_configuration_without_concrete_boot_pair(tmp_path):
    from bootloader_utils import update_bootloader_configs

    grub = tmp_path / 'boot/grub'
    grub.mkdir(parents=True)
    (grub / 'grub.cfg').write_text(
        'set kernel=/minios/boot/${kernel_file}\n', encoding='utf-8')
    assert update_bootloader_configs(str(tmp_path), 'new') is False
