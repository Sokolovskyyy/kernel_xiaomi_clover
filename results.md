# SUSFS Diagnostics — MI PAD 4 (kernel 4.19.325)

## Статус

**Корневая причина найдена.** IOCTL dispatch в KernelSU-Next не маршрутизирует SUSFS команды (0x555xx).

---

## 1. Устройство и конфигурация

| Параметр | Значение |
|----------|----------|
| Device | MI PAD 4 (clover) |
| Kernel | 4.19.325-perf (Proton clang 13.0.0) |
| Kernel source | AlcatrazDev-Android-Devices/kernel_xiaomi_clover (lineage-23.1) |
| KernelSU-Next | next branch, legacy non-GKI driver (CONFIG_KSU_MANUAL_HOOK=y) |
| SUSFS patch | v2.2.0 (JackA1ltman/NonGKI_Kernel_Build_2nd) |
| Module | sidex15/susfs4ksu-module v1.5.2-R28 |
| Android | 16 (SDK 36), BP4A.251205.006 |

## 2. Runtime diagnostics (с планшета)

| Что проверено | Результат |
|---------------|-----------|
| CONFIG_KSU_SUSFS (zcat /proc/config.gz) | y (+ все подопции) |
| KSU active (dmesg) | **Да** — "KernelSU: ksu fd installed", "ksu selinux hide", "set root profile" |
| /dev/ksu | **Нет** (legacy non-GKI — не создаёт char device) |
| /proc/susfs* | **Нет** |
| /data/adb/ | **Есть** — модуль установлен, ksu бинарник есть |
| Модуль | Установлен: `/data/adb/modules/susfs4ksu/` |
| ksu_susfs_arm64 show version | `[-] Requires susfs v1.5.3+` |
| module.prop version | `[-] Requires susfs v1.5.3+-d30974d` |
| /proc/kallsyms | `anon_ksu_ioctl` (t), `anon_ksu_release` (t), `setup_ksu_cred` (T) |
| susfs: логи в dmesg | **Нет** — ни одного сообщения |

## 3. Корневая причина ( Root Cause)

### Схема работы

```
ksu_susfs_arm64 (userspace)
    │
    │ ioctl(fd, CMD_SUSFS_SHOW_VERSION=0x555e1, ...)
    │ fd = /dev/ksu (anon_inode_fd)
    ▼
anon_ksu_ioctl()                     ← supercall/supercall.c (KSU-Next)
    │
    ▼
ksu_supercall_handle_ioctl(cmd, arg) ← supercall/dispatch.c (KSU-Next)
    │
    │ for (i = 0; ksu_ioctl_handlers[i].handler; i++) {
    │     if (cmd == ksu_ioctl_handlers[i].cmd) { ... }
    │ }
    │ // Ни один обработчик не совпадает с 0x555e1
    ▼
pr_warn("ksu ioctl: unsupported command 0x%x\n", cmd);
return -ENOTTY;   ← БИНАРНИК ВИДИ ЭТО И ВОЗВРАЩАЕТ "Requires susfs v1.5.3+"
```

### Проблема

Файл `kernel/supercall/dispatch.c` (upstream KernelSU-Next, ветка `dev`) содержит таблицу `ksu_ioctl_handlers[]` с ~20 командами:

```
KSU_IOCTL_GRANT_ROOT, KSU_IOCTL_GET_INFO, KSU_IOCTL_REPORT_EVENT,
KSU_IOCTL_SET_SEPOLICY, KSU_IOCTL_CHECK_SAFEMODE, KSU_IOCTL_GET_ALLOW_LIST,
... и т.д.
```

**Нет ни одной SUSFS команды** (0x55550-0x60020). Таблица заканчивается sentinel'ом.

Патч SUSFS добавляет обработчики команд в `fs/susfs.c`:
- `susfs_show_version()` — CMD_SUSFS_SHOW_VERSION (0x555e1)
- `susfs_add_sus_path()` — CMD_SUSFS_ADD_SUS_PATH (0x55550)
- `susfs_set_uname()` — CMD_SUSFS_SET_UNAME (0x55590)
- и т.д. (см. `include/linux/susfs_def.h`)

Но **dispatch table в dispatch.c не знает о них** — команды не маршрутизируются.

### Почему нет /proc/susfs* и susfs: логов

`fs/susfs.c` определяет `susfs_init()`, которая инициализирует work queue для мониторинга `/sdcard/Android`. Эта функция:
1. **Нигде не вызывается** — ни в `kernelsu_init()` (ksu.c), ни где-либо ещё в дереве
2. Логи `susfs:` требуют `CONFIG_KSU_SUSFS_ENABLE_LOG` + вызов `susfs_enable_log()` из userspace
3. `/proc/susfs*` — нет инициализации, нет procfs entry

## 4. Анализ архитектуры KSU-Next

### Два пути коммуникации ядро↔userspace

| Путь | Использует | Обработчик в ядре |
|------|-----------|-------------------|
| **FD ioctl** (anon_inode_fd) | `ksu_susfs_arm64` (sidex15 module) | `anon_ksu_ioctl` → `ksu_supercall_handle_ioctl` (dispatch.c) |
| **SYS_reboot syscall** | `susfsd.rs` (KSU-Next official) | `ksu_handle_sys_reboot` (kernel/reboot.c hook) |

**Официальный KSU-Next** отправляет SUSFS команды через `SYS_reboot` с `SUSFS_MAGIC=0xFAFAFAFA`:
```rust
// userspace/ksud/src/susfsd.rs
syscall(SYS_reboot, KSU_INSTALL_MAGIC1, SUSFS_MAGIC, CMD_SUSFS_SHOW_VERSION, &mut cmd);
```

**Модуль sidex15** (`ksu_susfs_arm64` v1.5.2-R28) отправляет через FD ioctl, который попадает в `dispatch.c` — а там SUSFS команд нет.

### Ключевой файл

`kernel/supercall/dispatch.c` (upstream):
- `ksu_supercall_handle_ioctl()` — итерирует по `ksu_ioctl_handlers[]`
- Если команда не найдена → `return -ENOTTY` ("unsupported command")
- **Нет fallback для 0x555xx команд**
- **Нет вызова `susfs_*` функций из fs/susfs.c**

## 5. Что нужно сделать (варианты фикса)

### Вариант A: Добавить SUSFS dispatch в ioctl handler (рекомендуется)

В `inject_susfs_safety.py` (или отдельном скрипте) добавить в `kernel/supercall/dispatch.c` после таблицы `ksu_ioctl_handlers[]` обработчик SUSFS команд:

```c
#ifdef CONFIG_KSU_SUSFS
#include <linux/susfs_def.h>
#include <linux/susfs.h>

static long handle_susfs_ioctl(unsigned int cmd, void __user *argp)
{
    void __user *user_info = argp;
    switch (cmd) {
    case CMD_SUSFS_ADD_SUS_PATH:       susfs_add_sus_path(&user_info); return 0;
    case CMD_SUSFS_ADD_SUS_PATH_LOOP:  susfs_add_sus_path_loop(&user_info); return 0;
    case CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS:
        susfs_set_hide_sus_mnts_for_non_su_procs(&user_info); return 0;
    case CMD_SUSFS_ADD_SUS_KSTAT:      susfs_add_sus_kstat(&user_info); return 0;
    case CMD_SUSFS_UPDATE_SUS_KSTAT:   susfs_update_sus_kstat(&user_info); return 0;
    case CMD_SUSFS_SET_UNAME:          susfs_set_uname(&user_info); return 0;
    case CMD_SUSFS_ENABLE_LOG:         susfs_enable_log(&user_info); return 0;
    case CMD_SUSFS_SET_CMDLINE_OR_BOOTCONFIG:
        susfs_set_cmdline_or_bootconfig(&user_info); return 0;
    case CMD_SUSFS_ADD_OPEN_REDIRECT:  susfs_add_open_redirect(&user_info); return 0;
    case CMD_SUSFS_SHOW_VERSION:       susfs_show_version(&user_info); return 0;
    case CMD_SUSFS_SHOW_ENABLED_FEATURES:
        susfs_show_enabled_features(&user_info); return 0;
    case CMD_SUSFS_SHOW_VARIANT:       susfs_show_variant(&user_info); return 0;
    case CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING:
        susfs_enable_avc_log_spoofing(&user_info); return 0;
    case CMD_SUSFS_ADD_SUS_MAP:        susfs_add_sus_map(&user_info); return 0;
    default: return -ENOTTY;
    }
}
#endif
```

И модифицировать `ksu_supercall_handle_ioctl()`:

```c
long ksu_supercall_handle_ioctl(unsigned int cmd, void __user *argp)
{
    // ... существующий цикл ...

#ifdef CONFIG_KSU_SUSFS
    if (cmd >= 0x55550 && cmd <= 0x60020)
        return handle_susfs_ioctl(cmd, argp);
#endif

    pr_warn("ksu ioctl: unsupported command 0x%x\n", cmd);
    return -ENOTTY;
}
```

### Вариант B: Добавить SYS_reboot hook для SUSFS

Если используется SYS_reboot путь, убедиться что `ksu_handle_sys_reboot()` в `kernel/reboot.c` маршрутизирует `SUSFS_MAGIC` вызовы к `fs/susfs.c` функциям. Нужно проверить текущую реализацию `ksu_handle_sys_reboot`.

### Вариант C: Использовать официальный susfsd из KSU-Next

Заменить модуль sidex15 на официальный KSU-Next SUSFS daemon, который использует `SYS_reboot` путь вместо FD ioctl.

## 6. Дополнительные задачи

### 6a. Вызвать susfs_init()

В `drivers/kernelsu/ksu.c`, функция `kernelsu_init()` должна вызывать `susfs_init()` (определена в `fs/susfs.c`). Сейчас она нигде не вызывается. Добавить:

```c
// В kernelsu_init() или on_boot_completed()
#ifdef CONFIG_KSU_SUSFS
extern void __init susfs_init(void);
susfs_init();
#endif
```

### 6b. Подключить fs/susfs.c в сборку

Убедиться что `fs/Makefile` содержит `obj-$(CONFIG_KSU_SUSFS) += susfs.o` — патч это делает, но нужно проверить что hunk применился корректно.

## 7. Структура файлов

| Файл | Роль |
|------|------|
| `kernel/supercall/dispatch.c` (upstream KSU-Next) | **Точка фикса** — ioctl dispatch table, нет SUSFS команд |
| `kernel/supercall/supercall.c` (upstream KSU-Next) | `anon_ksu_ioctl` → вызывает dispatch |
| `fs/susfs.c` (из SUSFS patch) | SUSFS command handlers (`susfs_show_version()` и др.) |
| `include/linux/susfs_def.h` (из SUSFS patch) | Команды 0x55550-0x60020, структуры |
| `include/linux/susfs.h` (из SUSFS patch) | Forward declarations, `SUSFS_VERSION "v2.2.0"` |
| `drivers/kernelsu/ksu.c` | `kernelsu_init()` — нет `susfs_init()` |
| `.github/workflows/build.yml` | CI: setup.sh → inject → patch → build |
| `.github/scripts/inject_susfs_safety.py` | Инжект SELinux stubs, нет ioctl dispatch |

## 8. Ссылки

- SUSFS patch: `https://raw.githubusercontent.com/JackA1ltman/NonGKI_Kernel_Build_2nd/mainline/Patches/Patch/susfs_patch_to_4.19.patch`
- KSU-Next dispatch.c: `https://github.com/KernelSU-Next/KernelSU-Next/blob/dev/kernel/supercall/dispatch.c`
- KSU-Next supercall.c: `https://github.com/KernelSU-Next/KernelSU-Next/blob/dev/kernel/supercall/supercall.c`
- KSU-Next susfsd.rs: `https://github.com/KernelSU-Next/KernelSU-Next/blob/dev/userspace/ksud/src/susfsd.rs`
- Module: `https://github.com/nicehash/kernel-ksud` (sidex15 fork)
- Device diagnostic: `susfs_diag.txt` (25KB, полный вывод)
