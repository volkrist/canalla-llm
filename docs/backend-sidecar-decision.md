# Backend sidecar packaging decision (0.9.3)

## Options

| | PyInstaller onedir | Nuitka | Embedded CPython | Tauri `externalBin` onefile |
|---|---|---|---|---|
| Reproducibility | Spec file + venv pins | Compiler flags, slower, more fragile native hooks | Closest to a venv copy | Same as PyInstaller onefile |
| Startup | Fast after AV first scan | Often fast | Fast | Slow extract-to-temp |
| Size | Dominated by ONNX Runtime, not GGUF | Similar native set | Similar | Similar + worse AV |
| FastAPI/Uvicorn | Proven | Possible | Proven | Proven |
| Alembic data files | `datas=` | Extra work | Copy tree | `datas=` |
| FastEmbed/ONNX DLLs | `collect_dynamic_libs` | Known pain | Copy site-packages | Same as onedir but packed |
| Defender | Better without UPX/onefile | Mixed | Looks like Python | onefile often quarantined |
| Tauri | Folder as `bundle.resources` | Same | Same | Native `externalBin` wants one exe |
| Debug | Dist folder is inspectable | Harder | Inspectable | Opaque |
| Updates | Replace folder | Replace exe | Replace runtime | Replace exe |
| CI | One script | Long compile | Copy + path rewrite | One script |
| Playwright | Exclude browsers | Same | Same | Same |
| Dev workflow | `tauri dev` still uses `.venv` | Same | Same | Same |

## Choice for this slice

**PyInstaller onedir → `alex-backend.exe` + `_internal/`**, copied into the Tauri resource tree.

Reasons:

1. This repo already recommended sidecar-from-`runtime_entry` after Desktop ownership (`docs/runtime-foundation.md`).
2. Alembic, Uvicorn, and ONNX DLLs are folder-shaped. onedir avoids onefile extract-to-temp and reduces SmartScreen friction versus a 200MB+ packed exe.
3. Existing Desktop supervisor (PID + Job Object, no kill-by-name) stays. Tauri `externalBin` would only help *bundle* a single file; it would not replace ownership. Resources + our supervisor is the equivalent that actually fits onedir.
4. Nuitka would fight ONNX/tokenizers on Windows for little product gain in an installer-*foundation* slice.
5. Embedded CPython is the better long-term updater story; it is a bigger CI/layout change than this slice.

UPX is off. Chromium is not collected. Embedding weights are not collected.

Production packaged Desktop never searches `python.exe`. `tauri dev` / debug builds may still spawn `apps/backend/.venv/Scripts/python.exe`.
