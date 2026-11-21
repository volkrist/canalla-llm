; Keep binaries out of the user data root (%LOCALAPPDATA%\Alex LLM).
;
; 1.0 note: the product is now Canalla LLM, so the programme installs into
; Programs\Canalla LLM. A previous Alex LLM programme folder must not stay behind as a
; second, competing installation: its binaries, shortcuts, autostart entry and uninstall
; entry are removed here. The user data root (%LOCALAPPDATA%\Alex LLM) and every Windows
; Credential Manager entry are never touched — they hold the database, documents, session,
; provider credential and Alex Cloud enrollment, and the product keeps using them as-is.

; An upgrade is a replacement, not a merge. Tauri installs the new tree over the old one, so a
; file the previous build left behind survives the upgrade and can break the new build. A 1.2.0
; tree carried a half-collected _internal\cryptography (only the _rust.pyd, no __init__.py);
; installed over it, 1.2.1 made `cryptography` a namespace package and the sidecar died on
; `from cryptography import x509` before it could answer /health. The two trees below are wholly
; product-owned runtime payloads, and every mutable file lives in %LOCALAPPDATA%\Alex LLM, so
; removing them here loses no user data, no database, no document and no credential.
!macro NSIS_HOOK_PREINSTALL
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${PRODUCTNAME}"
  RMDir /r "$INSTDIR\sidecar"
  RMDir /r "$INSTDIR\runtime"
  SetOutPath $INSTDIR
!macroend

!macro NSIS_HOOK_POSTINSTALL
  StrCpy $0 "$LOCALAPPDATA\Programs\Alex LLM"
  ; Only a real legacy Alex LLM programme folder is removed. The check on its own main
  ; binary keeps this block away from any other folder that merely shares the name.
  IfFileExists "$0\alex-llm.exe" 0 legacy_program_done
  Delete "$SMPROGRAMS\Alex LLM.lnk"
  Delete "$SMPROGRAMS\$AppStartMenuFolder\Alex LLM.lnk"
  Delete "$DESKTOP\Alex LLM.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Alex LLM"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Alex LLM"
  RMDir /r "$0"
legacy_program_done:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Tor и sidecar запускаются самим продуктом (always-ready) и держат файлы в $INSTDIR. Без этого
  ; шага деинсталлятор не может удалить runtime\tor\tor.exe и оставляет каталог программы
  ; вместе с работающим демоном (найдено приёмкой 1.2.7). Останавливаются только процессы,
  ; чей исполняемый файл лежит внутри $INSTDIR: чужой Tor, Tor Browser и любой другой процесс
  ; с тем же именем не затрагиваются.
  System::Call 'kernel32::SetEnvironmentVariable(t "CANALLA_UNINSTALL_DIR", t "$EXEDIR")'
  FileOpen $9 "$TEMP\canalla-uninstall.log" w
  FileWrite $9 "exedir=$EXEDIR instdir=$INSTDIR$\r$\n"
  FileClose $9
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$$d=$$env:CANALLA_UNINSTALL_DIR; Get-Process -Name alex-llm,alex-backend,alex-host-loop,tor -ErrorAction SilentlyContinue | Where-Object { $$_.Path -and $$_.Path.StartsWith($$d, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue"'
  Pop $0
  FileOpen $9 "$TEMP\canalla-uninstall.log" a
  FileWrite $9 "nsExec=$0$\r$\n"
  FileClose $9
  Sleep 2000
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; The «start with Windows» entry belongs to the programme, not to the data: it is removed with
  ; the programme. The user data root and every Credential Manager entry are never touched here -
  ; they hold the database, documents, session and enrollment, and only an explicit choice in the
  ; product removes them.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Canalla LLM"
!macroend
