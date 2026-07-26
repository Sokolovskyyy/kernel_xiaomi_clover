#!/usr/bin/env python3
"""
Inject minimum SUSFS safety implementations into KernelSU-Next source tree.

Architecture:
  - susfs_is_current_ksu_domain() is a NON-static function in selinux/selinux.c
    because callers in fs/namespace.c and fs/susfs.c include <linux/susfs_def.h>
    (which has extern declaration) but NOT drivers/kernelsu/selinux/selinux.h.
    A static inline in selinux.h would be invisible to those callers → linker error.
  - susfs_ksu_sid / susfs_priv_app_sid are variable definitions in selinux.c,
    declared extern in selinux/selinux.h (for KSU driver code) and implicitly
    via susfs_def.h extern for other callers.
  - susfs_set_ksu_sid_from_ctx / susfs_set_priv_app_sid_from_ctx are only called
    from rules.c (inside KSU driver), so they can be static inline in selinux.h.
  - ksu_handle_devpts() is called by the original AlcatrazDev pty.c hook but has
    no implementation — provide a no-op stub.

Usage:
  KSU_DIR=drivers/kernelsu python3 inject_susfs_safety.py
"""
import os

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


# ── 1. selinux/selinux.h — static inline setters + extern vars ──────────
path = os.path.join(KSU, "selinux", "selinux.h")
src = read_file(path)

if "susfs_is_current_ksu_domain" in src:
    print(f"{path}: SUSFS declarations already present - skipping")
else:
    block = """
#ifdef CONFIG_KSU_SUSFS

extern u32 susfs_ksu_sid;
extern u32 susfs_priv_app_sid;
bool susfs_is_current_ksu_domain(void);

static inline void susfs_set_ksu_sid_from_ctx(const char *secctx_name)
{
\tint err;
\tif (!secctx_name) { pr_err("susfs: secctx_name is NULL\\n"); return; }
\terr = security_secctx_to_secid(secctx_name, strlen(secctx_name),
\t\t\t\t       &susfs_ksu_sid);
\tif (err) pr_err("susfs: failed to resolve sid for '%s', err: %d\\n",
\t\t       secctx_name, err);
\telse pr_info("susfs: ksu_sid '%u' set for '%s'\\n", susfs_ksu_sid, secctx_name);
}

static inline void susfs_set_priv_app_sid_from_ctx(const char *secctx_name)
{
\tint err;
\tif (!secctx_name) { pr_err("susfs: secctx_name is NULL\\n"); return; }
\terr = security_secctx_to_secid(secctx_name, strlen(secctx_name),
\t\t\t\t       &susfs_priv_app_sid);
\tif (err) pr_err("susfs: failed to resolve sid for '%s', err: %d\\n",
\t\t       secctx_name, err);
\telse pr_info("susfs: priv_app_sid '%u' set for '%s'\\n",
\t\t     susfs_priv_app_sid, secctx_name);
}
#endif /* CONFIG_KSU_SUSFS */
"""
    idx = src.rfind("#endif")
    if idx >= 0:
        new_src = src[:idx] + block + "\n" + src[idx:]
        write_file(path, new_src)
        print(f"{path}: added SUSFS declarations + static inline setters")
    else:
        append_file(path, block)
        print(f"{path}: appended SUSFS declarations (fallback)")

# ── 2. selinux/selinux.c — variable definitions + non-inline function ──
path = os.path.join(KSU, "selinux", "selinux.c")
src = read_file(path)

if "susfs_ksu_sid" in src:
    print(f"{path}: SUSFS symbols already present - skipping")
else:
    code = """
#ifdef CONFIG_KSU_SUSFS
#include <linux/security.h>

u32 susfs_ksu_sid __read_mostly = 0;
u32 susfs_priv_app_sid __read_mostly = 0;

bool susfs_is_current_ksu_domain(void)
{
\treturn unlikely(current_sid() == susfs_ksu_sid);
}

/*
 * Stub for ksu_handle_devpts() — called from drivers/tty/pty.c
 * (pts_unix98_lookup) in AlcatrazDev kernel source but was never
 * implemented. Provide no-op to satisfy the linker.
 */
int ksu_handle_devpts(struct inode *inode)
{
\treturn 0;
}
#endif /* CONFIG_KSU_SUSFS */
"""
    append_file(path, code)
    print(f"{path}: appended SUSFS variables + susfs_is_current_ksu_domain()")

# ── 3. selinux/rules.c — add SID setup calls ──────────────────────────
path = os.path.join(KSU, "selinux", "rules.c")
src = read_file(path)

if "susfs_set_ksu_sid" in src or "susfs_set_ksu_sid_from_ctx" in src:
    print(f"{path}: SUSFS SID setup already present - skipping")
else:
    marker = "\treset_avc_cache();"
    idx = src.find(marker)
    if idx >= 0:
        eol = src.index("\n", idx) + 1
        sid_setup = """
#ifdef CONFIG_KSU_SUSFS
\tsusfs_set_priv_app_sid_from_ctx("u:r:priv_app:s0:c512,c768");
\tsusfs_set_ksu_sid_from_ctx(KERNEL_SU_CONTEXT);
#endif
"""
        new_src = src[:eol] + sid_setup + src[eol:]
        write_file(path, new_src)
        print(f"{path}: added SUSFS SID setup after reset_avc_cache()")
    else:
        print(f"WARNING: marker not found in {path} - SID setup skipped")

print("\n=== SUSFS safety injection complete ===")
