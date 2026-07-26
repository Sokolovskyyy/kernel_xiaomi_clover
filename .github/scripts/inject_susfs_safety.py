#!/usr/bin/env python3
"""
Inject minimum SUSFS safety implementations into KernelSU-Next source tree.

The SUSFS kernel patch (susfs_patch_to_4.19.patch) adds hooks to kernel source
files (namespace.c, avc.c, pty.c) that call SUSFS functions. These functions
are normally provided by the '70_ksu_safety' patch from Enginex0/Super-Builders
which modifies the KernelSU-Next SOURCE TREE.

Without this step, the kernel compiles all .c files but fails at link with
undefined references to:
  susfs_is_current_ksu_domain  (namespace.c, fs/susfs.c)
  susfs_ksu_sid                (security/selinux/avc.c)
  susfs_priv_app_sid           (security/selinux/avc.c)

Usage:
  KSU_DIR=drivers/kernelsu python3 inject_susfs_safety.py
"""
import os
import sys

KSU = os.environ.get("KSU_DIR", "drivers/kernelsu")


def read_file(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write_file(p, c):
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(c)


def append_file(p, c):
    with open(p, "a", encoding="utf-8", errors="surrogateescape") as f:
        f.write(c)


# ── 1. selinux/selinux.c — append SUSFS SID functions ─────────────────
path = os.path.join(KSU, "selinux", "selinux.c")
src = read_file(path)

if "susfs_is_current_ksu_domain" in src:
    print(f"{path}: SUSFS functions already present - skipping")
else:
    susfs_code = """
#ifdef CONFIG_KSU_SUSFS

#define KERNEL_PRIV_APP_DOMAIN "u:r:priv_app:s0:c512,c768"

u32 susfs_ksu_sid __read_mostly = 0;
u32 susfs_priv_app_sid __read_mostly = 0;

static inline void susfs_set_sid(const char *secctx_name, u32 *out_sid)
{
\tint err;

\tif (!secctx_name || !out_sid) {
\t\tpr_err("secctx_name || out_sid is NULL\\n");
\t\treturn;
\t}
\terr = security_secctx_to_secid(secctx_name, strlen(secctx_name),
\t\t\t\t       out_sid);
\tif (err) {
\t\tpr_err("failed setting sid for '%s', err: %d\\n",
\t\t       secctx_name, err);
\t\treturn;
\t}
\tpr_info("susfs: sid '%u' set for '%s'\\n", *out_sid, secctx_name);
}

void susfs_set_ksu_sid(void)
{
\tsusfs_set_sid(KERNEL_SU_CONTEXT, &susfs_ksu_sid);
}

void susfs_set_priv_app_sid(void)
{
\tsusfs_set_sid(KERNEL_PRIV_APP_DOMAIN, &susfs_priv_app_sid);
}

bool susfs_is_current_ksu_domain(void)
{
\treturn unlikely(current_sid() == susfs_ksu_sid);
}

#endif /* CONFIG_KSU_SUSFS */
"""
    append_file(path, susfs_code)
    print(f"{path}: appended SUSFS SID functions")

# ── 2. selinux/selinux.h — add declarations ────────────────────────────
path = os.path.join(KSU, "selinux", "selinux.h")
src = read_file(path)

if "susfs_is_current_ksu_domain" in src:
    print(f"{path}: SUSFS declarations already present - skipping")
else:
    decl_block = """
#ifdef CONFIG_KSU_SUSFS
extern u32 susfs_ksu_sid;
extern u32 susfs_priv_app_sid;
void susfs_set_ksu_sid(void);
void susfs_set_priv_app_sid(void);
bool susfs_is_current_ksu_domain(void);
#endif /* CONFIG_KSU_SUSFS */
"""
    # Insert before the final #endif
    idx = src.rfind("#endif")
    if idx >= 0:
        new_src = src[:idx] + decl_block + "\n" + src[idx:]
        write_file(path, new_src)
        print(f"{path}: added SUSFS declarations before final #endif")
    else:
        append_file(path, decl_block)
        print(f"{path}: appended SUSFS declarations (fallback)")

# ── 3. selinux/rules.c — add SID setup calls ──────────────────────────
path = os.path.join(KSU, "selinux", "rules.c")
src = read_file(path)

if "susfs_set_ksu_sid" in src:
    print(f"{path}: SUSFS SID setup already present - skipping")
else:
    marker = "\treset_avc_cache();"
    idx = src.find(marker)
    if idx >= 0:
        eol = src.index("\n", idx) + 1
        sid_setup = """\n#ifdef CONFIG_KSU_SUSFS
\tsusfs_set_priv_app_sid();
\tsusfs_set_ksu_sid();
#endif
"""
        new_src = src[:eol] + sid_setup + src[eol:]
        write_file(path, new_src)
        print(f"{path}: added SUSFS SID setup after reset_avc_cache()")
    else:
        print(f"WARNING: marker not found in {path} - SID setup skipped")

print("\n=== SUSFS safety injection complete ===")
