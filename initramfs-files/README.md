# Файлы initramfs для MiniOS Kernel Manager

Эти файлы содержат изменения, необходимые для поддержки новой архитектуры управления ядрами в MiniOS.

## Изменения

### `init`
- Добавлен вызов `setup_active_kernel "$DATA"` на строке 55
- Обеспечивает активацию выбранного ядра при загрузке

### `lib/livekitlib`
- Добавлена функция `setup_active_kernel()` (строки 1542-1589)
- Функция копирует активное ядро из `/kernels/` директории в `01-kernel-active.sb`
- Использует конфигурацию из `config.conf.d/kernel-manager.conf`

## Установка

Скопируйте эти файлы в соответствующие места в initramfs:
- `init` → `/run/initramfs/init`
- `lib/livekitlib` → `/run/initramfs/lib/livekitlib`

## Новая архитектура

1. **Хранение ядер**: `/minios/kernels/01-kernel-*.sb`
2. **Активное ядро**: `/minios/01-kernel-active.sb` (создается при загрузке)
3. **Конфигурация**: `/minios/config.conf.d/kernel-manager.conf`
4. **Параметр**: `ACTIVE_KERNEL="version"`

Только активное ядро загружается в overlay filesystem, что решает проблему загрузки всех ядер одновременно.