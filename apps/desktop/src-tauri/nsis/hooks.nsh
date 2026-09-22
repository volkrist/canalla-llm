; Keep binaries out of the user data root (%LOCALAPPDATA%\Alex LLM).
;
; 1.0 note: the product is now Canalla LLM, so the programme installs into
; Programs\Canalla LLM. A previous Alex LLM programme folder must not stay behind as a
; second, competing installation: its binaries, shortcuts, autostart entry and uninstall
; entry are removed here. The user data root (%LOCALAPPDATA%\Alex LLM) and every Windows
; Credential Manager entry are never touched — they hold the database, documents, session,
; provider credential and Alex Cloud enrollment, and the product keeps using them as-is.

!macro NSIS_HOOK_PREINSTALL
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${PRODUCTNAME}"
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
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
!macroend
