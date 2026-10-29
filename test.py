import sys, inspect

def show_pkg_version(pkg_name, module=None):
    mod_path = getattr(module, "__file__", None)
    print(f"[{pkg_name}] imported from:", mod_path)

    # 1) Try module attribute
    v = getattr(module, "__version__", None) if module else None
    if v:
        print(f"[{pkg_name}] version (attr):", v)
        return

    # 2) importlib.metadata (Py ≥3.8)
    try:
        from importlib.metadata import version, PackageNotFoundError
        print(f"[{pkg_name}] version (importlib.metadata):", version(pkg_name))
        return
    except Exception as e:
        print(f"[{pkg_name}] importlib.metadata failed:", repr(e))

    # 3) Backport for older Pythons
    try:
        import importlib_metadata  # pip install importlib-metadata
        print(f"[{pkg_name}] version (importlib_metadata backport):",
              importlib_metadata.version(pkg_name))
        return
    except Exception as e:
        print(f"[{pkg_name}] importlib_metadata backport failed:", repr(e))

    # 4) pkg_resources fallback
    try:
        import pkg_resources
        print(f"[{pkg_name}] version (pkg_resources):",
              pkg_resources.get_distribution(pkg_name).version)
        return
    except Exception as e:
        print(f"[{pkg_name}] pkg_resources failed:", repr(e))

    print(f"[{pkg_name}] version: <unknown>")

# Usage
import pyFAI
show_pkg_version("pyFAI", pyFAI)

# (handy to check dependencies too)
for dep in ("fabio", "numpy"):
    try:
        mod = __import__(dep)
    except Exception:
        mod = None
    show_pkg_version(dep, mod)
