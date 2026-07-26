#!/usr/bin/env python3
"""
Inject SUSFS ioctl dispatch + susfs_init() call into KernelSU-Next source tree.

Three changes (all idempotent):
  1. dispatch.c  — add SUSFS command handler + range check in ksu_supercall_handle_ioctl()
  2. init.c      — add susfs_init() call in kernelsu_init()
  3. fs/Makefile — verify obj-$(CONFIG_KSU_SUSFS) += susfs.o exists

Run AFTER:
  - KernelSU-Next cloned (setup.sh)
  - SUSFS patch applied (fs/susfs.c, include/linux/susfs.h exist)
"""
import os, re, sys, glob

KSU = os.environ.get("KSU_DIR", "drivers/kernelsu")


def read_file(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write_file(p, c):
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(c)


def find_file(pattern):
    """Find first file matching glob under KSU dir."""
    matches = glob.glob(os.path.join(KSU, pattern))
    return matches[0] if matches else None


# ═══════════════════════════════════════════════════════════════════
# 1. dispatch.c — SUSFS ioctl dispatch
# ═══════════════════════════════════════════════════════════════════
dispatch_path = find_file("supercall/dispatch.c")
if not dispatch_path:
    print("FATAL: supercall/dispatch.c not found under", KSU)
    sys.exit(1)

src = read_file(dispatch_path)

if "SUSFS_DISPATCH_BEGIN" in src:
    print(f"{dispatch_path}: SUSFS dispatch already present — skipping")
else:
    # ── 1a. Insert handle_susfs_ioctl() function BEFORE ksu_supercall_handle_ioctl ──
    susfs_handler = r"""
#ifdef CONFIG_KSU_SUSFS
#include <linux/susfs.h>

static int handle_susfs_ioctl(unsigned int cmd, void __user *argp)
{
	void __user *user_info = argp;
	/* SUSFS_DISPATCH_BEGIN */
	switch (cmd) {
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	case CMD_SUSFS_ADD_SUS_PATH:
		susfs_add_sus_path(&user_info);
		return 0;
	case CMD_SUSFS_ADD_SUS_PATH_LOOP:
		susfs_add_sus_path_loop(&user_info);
		return 0;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	case CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS:
		susfs_set_hide_sus_mnts_for_non_su_procs(&user_info);
		return 0;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
	case CMD_SUSFS_ADD_SUS_KSTAT:
		susfs_add_sus_kstat(&user_info);
		return 0;
	case CMD_SUSFS_UPDATE_SUS_KSTAT:
		susfs_update_sus_kstat(&user_info);
		return 0;
	case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY:
		susfs_add_sus_kstat(&user_info);
		return 0;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
	case CMD_SUSFS_SET_UNAME:
		susfs_set_uname(&user_info);
		return 0;
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
	case CMD_SUSFS_ENABLE_LOG:
		susfs_enable_log(&user_info);
		return 0;
#endif
	case CMD_SUSFS_SET_CMDLINE_OR_BOOTCONFIG:
		susfs_set_cmdline_or_bootconfig(&user_info);
		return 0;
	case CMD_SUSFS_ADD_OPEN_REDIRECT:
		susfs_add_open_redirect(&user_info);
		return 0;
	case CMD_SUSFS_SHOW_VERSION:
		susfs_show_version(&user_info);
		return 0;
	case CMD_SUSFS_SHOW_ENABLED_FEATURES:
		susfs_show_enabled_features(&user_info);
		return 0;
	case CMD_SUSFS_SHOW_VARIANT:
		susfs_show_variant(&user_info);
		return 0;
	case CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING:
		susfs_enable_avc_log_spoofing(&user_info);
		return 0;
	case CMD_SUSFS_ADD_SUS_MAP:
		susfs_add_sus_map(&user_info);
		return 0;
	default:
		return -ENOTTY;
	}
	/* SUSFS_DISPATCH_END */
}
#endif /* CONFIG_KSU_SUSFS */
"""

    anchor_func = "long ksu_supercall_handle_ioctl(unsigned int cmd, void __user *argp)"
    idx = src.find(anchor_func)
    if idx < 0:
        print(f"FATAL: '{anchor_func}' not found in {dispatch_path}")
        sys.exit(1)

    new_src = src[:idx] + susfs_handler + "\n" + src[idx:]

    # ── 1b. Add range check in ksu_supercall_handle_ioctl() ──
    # After the for loop, before the "pr_warn("ksu ioctl: unsupported"
    range_check = """
#ifdef CONFIG_KSU_SUSFS
	/* Forward SUSFS commands (0x55550..0x60020) to handler */
	if (cmd >= 0x55550 && cmd <= 0x60020)
		return handle_susfs_ioctl(cmd, argp);
#endif
"""
    marker = 'pr_warn("ksu ioctl: unsupported command 0x%x\\n", cmd);'
    if marker not in new_src:
        print(f"WARNING: fallback marker not found in {dispatch_path}")
        print("  The range check will NOT be inserted.")
    elif "#if CONFIG_KSU_SUSFS" in new_src and "cmd >= 0x55550" in new_src:
        print(f"{dispatch_path}: range check already present — skipping")
    else:
        new_src = new_src.replace(marker, range_check + "\n\t" + marker, 1)

    write_file(dispatch_path, new_src)
    print(f"{dispatch_path}: added SUSFS ioctl dispatch ✓")

    # Verify insertion
    verify = read_file(dispatch_path)
    if "handle_susfs_ioctl" not in verify:
        print(f"  ERROR: handle_susfs_ioctl not found after write!")
        sys.exit(1)
    if "cmd >= 0x55550" not in verify:
        print(f"  WARNING: range check not found after write!")

# ═══════════════════════════════════════════════════════════════════
# 2. init.c — call susfs_init()
# ═══════════════════════════════════════════════════════════════════
init_path = find_file("core/init.c") or find_file("ksu.c")
if not init_path:
    print("FATAL: core/init.c or ksu.c not found under", KSU)
    sys.exit(1)

src = read_file(init_path)

if "susfs_init" in src:
    print(f"{init_path}: susfs_init call already present — skipping")
else:
    # Find kernelsu_init() and add susfs_init() call early in it
    # Strategy: insert after "ksu_cred = prepare_creds();" block (or at start of function body)
    init_call = (
        "\n#ifdef CONFIG_KSU_SUSFS\n"
        "\t{\n"
        "\t\textern void susfs_init(void);\n"
        "\t\tsusfs_init();\n"
        "\t}\n"
        "#endif\n"
    )

    # Try anchor: after ksu_cred block
    cred_anchor = "if (!ksu_cred) {"
    idx = src.find(cred_anchor)
    if idx >= 0:
        # Find the closing brace of that if-block
        brace_count = 0
        insert_pos = None
        for i in range(idx, len(src)):
            if src[i] == '{':
                brace_count += 1
            elif src[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    insert_pos = i + 1
                    break
        if insert_pos is not None:
            # Find end of line
            eol = src.find('\n', insert_pos)
            if eol >= 0:
                insert_pos = eol + 1
            new_src = src[:insert_pos] + init_call + src[insert_pos:]
            write_file(init_path, new_src)
            print(f"{init_path}: added susfs_init() call after ksu_cred init ✓")
        else:
            print(f"WARNING: could not find end of ksu_cred if-block in {init_path}")
    else:
        # Fallback: insert right after function opening brace of kernelsu_init
        func_start = src.find("int __init kernelsu_init(void)")
        if func_start < 0:
            func_start = src.find("kernelsu_init")
        if func_start >= 0:
            brace = src.find('{', func_start)
            if brace >= 0:
                insert_pos = brace + 1
                new_src = src[:insert_pos] + init_call + "\n" + src[insert_pos:]
                write_file(init_path, new_src)
                print(f"{init_path}: added susfs_init() call (fallback) ✓")
            else:
                print(f"FATAL: opening brace of kernelsu_init not found in {init_path}")
        else:
            print(f"FATAL: kernelsu_init not found in {init_path}")

# ═══════════════════════════════════════════════════════════════════
# 3. fs/Makefile — verify susfs.o
# ═══════════════════════════════════════════════════════════════════
makefile = "fs/Makefile"
if os.path.exists(makefile):
    src = read_file(makefile)
    if "CONFIG_KSU_SUSFS" in src or "susfs.o" in src:
        print(f"{makefile}: susfs.o present ✓")
    else:
        print(f"WARNING: {makefile} missing susfs.o — SUSFS patch may not have applied")
        print("  Expected: obj-$(CONFIG_KSU_SUSFS) += susfs.o")
else:
    print(f"WARNING: {makefile} not found (not yet patched?)")

print("\n=== SUSFS dispatch injection complete ===")
