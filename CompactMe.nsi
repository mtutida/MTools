; MTools CompactMe NSIS installer script
; Build after PyInstaller onedir output exists in dist\CompactMe\.

Unicode true

!include "MUI2.nsh"
!include "x64.nsh"
!include "StrFunc.nsh"
${Using:StrFunc} UnStrStr

!define APP_NAME "MTools CompactMe"
!define APP_VERSION "1.1.0-rc395"
!define APP_VERSION_NUMERIC "1.1.0.395"
!define APP_PUBLISHER "Marcelo Tutida"
!define APP_COPYRIGHT "Copyright © 2026 Marcelo Tutida"
!define APP_EXE "CompactMe.exe"
!define APP_ICON_FILE "CompactMe.ico"
!define APP_DIR "dist\CompactMe"
!define OUTPUT_DIR "installer_output"
!define OUTPUT_FILE "MTools_CompactMe_Setup_${APP_VERSION}.exe"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MTools CompactMe"
!define OPENWITH_APP_KEY "Software\Classes\Applications\CompactMe.exe"
!define LEGACY_APP_NAME "CompriMidia"
!define LEGACY_APP_EXE "CompriMidia.exe"
!define LEGACY_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CompriMidia"
!define LEGACY_OPENWITH_APP_KEY "Software\Classes\Applications\CompriMidia.exe"
!define LEGACY_PRODUCT_KEY "Software\CompriMidia"
!define APP_REGISTRY_KEY "Software\MTools\CompactMe"
!define APP_INSTALL_DIR "MTools\CompactMe"

Name "${APP_NAME}"
OutFile "${OUTPUT_DIR}\${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\${APP_INSTALL_DIR}"
InstallDirRegKey HKLM "${APP_REGISTRY_KEY}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "${APP_NAME}"

!macro RegisterOpenWithExtension EXT
  WriteRegStr HKLM "${OPENWITH_APP_KEY}\SupportedTypes" "${EXT}" ""
!macroend

!macro UnregisterOpenWithExtension EXT
  DeleteRegValue HKLM "${OPENWITH_APP_KEY}\SupportedTypes" "${EXT}"
!macroend

VIProductVersion "${APP_VERSION_NUMERIC}"
VIAddVersionKey /LANG=1046 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=1046 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1046 "FileVersion" "${APP_VERSION_NUMERIC}"
VIAddVersionKey /LANG=1046 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=1046 "LegalCopyright" "${APP_COPYRIGHT}"
VIAddVersionKey /LANG=1046 "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey /LANG=1046 "OriginalFilename" "${OUTPUT_FILE}"

!define MUI_ABORTWARNING
!define MUI_ICON "app\ui\icons\compactme.ico"
!define MUI_UNICON "app\ui\icons\compactme.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Abrir ${APP_NAME}"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

Function MigrateLegacyRc393
  ; The published rc393 installer uses this key and Program Files location.
  ; Execute its own uninstaller to preserve its cleanup contract and user data.
  ReadRegStr $0 HKLM "${LEGACY_UNINSTALL_KEY}" "InstallLocation"
  StrCmp $0 "" legacy_done
  IfFileExists "$0\${LEGACY_APP_EXE}" 0 legacy_stale
  IfFileExists "$0\uninstall.exe" legacy_uninstall legacy_blocked

legacy_uninstall:
  ExecWait '"$0\uninstall.exe" /S _?=$0' $1
  StrCmp $1 0 legacy_verify legacy_blocked

legacy_verify:
  IfFileExists "$0\${LEGACY_APP_EXE}" legacy_blocked
  ; The _?= option lets ExecWait observe completion but prevents NSIS from
  ; removing its own uninstaller. Remove that verified, product-owned file now.
  Delete "$0\uninstall.exe"
  IfFileExists "$0\uninstall.exe" legacy_blocked
  RMDir "$0"
  Goto legacy_done

legacy_stale:
  ; A stale ARP entry cannot be upgraded. Remove only its product-owned keys.
  DeleteRegKey HKLM "${LEGACY_UNINSTALL_KEY}"
  DeleteRegKey HKLM "${LEGACY_OPENWITH_APP_KEY}"
  DeleteRegKey HKLM "${LEGACY_PRODUCT_KEY}"
  Goto legacy_done

legacy_blocked:
  MessageBox MB_ICONSTOP|MB_OK "Não foi possível remover a instalação CompriMidia rc393 existente. Repare ou desinstale a versão anterior antes de continuar."
  Abort

legacy_done:
FunctionEnd

Function CleanupLegacyCompatibilityResidue
  ; rc393 data is intentionally not under Program Files and is never removed here.
  SetShellVarContext all
  Delete "$DESKTOP\${LEGACY_APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${LEGACY_APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${LEGACY_APP_NAME}\${LEGACY_APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${LEGACY_APP_NAME}\Desinstalar ${LEGACY_APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${LEGACY_APP_NAME}"
  DeleteRegKey HKLM "${LEGACY_OPENWITH_APP_KEY}"
  DeleteRegKey HKLM "${LEGACY_UNINSTALL_KEY}"
  DeleteRegKey HKLM "${LEGACY_PRODUCT_KEY}"
FunctionEnd
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP_NAME} requer Windows 64 bits."
    Abort
  ${EndIf}
  Call MigrateLegacyRc393
FunctionEnd

Section "${APP_NAME}" SEC_APP
  SectionIn RO
  Call CleanupLegacyCompatibilityResidue

  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\${APP_ICON_FILE}"
  Delete "$INSTDIR\config.json"

  File /r "${APP_DIR}\*"
  File /oname=${APP_ICON_FILE} "app\ui\icons\compactme.ico"

  WriteRegStr HKLM "${APP_REGISTRY_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_ICON_FILE}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair" 1

  ; Registro leve para aparecer no menu "Abrir com" do Windows Explorer.
  ; Não torna o CompactMe o app padrão de nenhum formato.
  WriteRegStr HKLM "${OPENWITH_APP_KEY}" "FriendlyAppName" "${APP_NAME}"
  WriteRegStr HKLM "${OPENWITH_APP_KEY}\DefaultIcon" "" "$INSTDIR\${APP_ICON_FILE}"
  WriteRegStr HKLM "${OPENWITH_APP_KEY}\shell\open\command" "" '"$INSTDIR\${APP_EXE}" "%1"'
  !insertmacro RegisterOpenWithExtension .mp4
  !insertmacro RegisterOpenWithExtension .mkv
  !insertmacro RegisterOpenWithExtension .webm
  !insertmacro RegisterOpenWithExtension .mov
  !insertmacro RegisterOpenWithExtension .avi
  !insertmacro RegisterOpenWithExtension .flv
  !insertmacro RegisterOpenWithExtension .ts
  !insertmacro RegisterOpenWithExtension .m4v
  !insertmacro RegisterOpenWithExtension .wmv
  !insertmacro RegisterOpenWithExtension .mts
  !insertmacro RegisterOpenWithExtension .m2ts
  !insertmacro RegisterOpenWithExtension .3gp
  !insertmacro RegisterOpenWithExtension .mpg
  !insertmacro RegisterOpenWithExtension .mpeg
  !insertmacro RegisterOpenWithExtension .vob
  !insertmacro RegisterOpenWithExtension .mp3
  !insertmacro RegisterOpenWithExtension .aac
  !insertmacro RegisterOpenWithExtension .wav
  !insertmacro RegisterOpenWithExtension .flac
  !insertmacro RegisterOpenWithExtension .ogg
  !insertmacro RegisterOpenWithExtension .m4a
  !insertmacro RegisterOpenWithExtension .wma
  !insertmacro RegisterOpenWithExtension .opus
  !insertmacro RegisterOpenWithExtension .alac
  System::Call 'shell32.dll::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'

  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Criar atalhos no Menu Iniciar de todos os usuários. Sem isso, em uma
  ; instalação elevada por UAC o atalho pode acabar no perfil do usuário
  ; elevado em vez de aparecer para o usuário que instalou/usa o app.
  SetShellVarContext all
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_ICON_FILE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_ICON_FILE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Desinstalar ${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"
SectionEnd

Section "Criar atalho na Área de Trabalho" SEC_DESKTOP
  SetShellVarContext all
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_ICON_FILE}" 0 SW_SHOWNORMAL "" "${APP_NAME}"
SectionEnd

Function un.EnsureCompactMeClosed
un.check_running:
  nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\tasklist.exe" /FI "IMAGENAME eq ${APP_EXE}" /FO CSV /NH'
  Pop $0
  Pop $1
  StrCmp $0 "error" un.process_check_failed
  StrCmp $0 "timeout" un.process_check_failed
  ${UnStrStr} $2 $1 "${APP_EXE}"
  StrCmp $2 "" un.process_closed
  IfSilent un.process_running_silent
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${APP_NAME} está em execução.$\r$\n$\r$\nFeche o aplicativo e clique em Repetir para continuar. Clique em Cancelar para sair sem desinstalar." IDRETRY un.check_running IDCANCEL un.process_cancelled

un.process_cancelled:
  SetErrors
  Return
un.process_running_silent:
  SetErrors
  Return
un.process_closed:
  ClearErrors
  Return
un.process_check_failed:
  IfSilent un.process_check_failed_silent
  MessageBox MB_OK|MB_ICONSTOP "Não foi possível confirmar se ${APP_NAME} está fechado. O uninstall foi cancelado sem remover arquivos ou registros. Tente novamente."
un.process_check_failed_silent:
  SetErrors
  Return
FunctionEnd

Section "Uninstall"
  Call un.EnsureCompactMeClosed
  IfErrors un.abort

  ; Remove the in-use-prone application payload first. Keep shortcuts and
  ; uninstall registration intact if Windows still refuses to delete it.
  ClearErrors
  Delete "$INSTDIR\${APP_EXE}"
  IfFileExists "$INSTDIR\${APP_EXE}" un.cleanup_failed
  RMDir /r "$INSTDIR\_internal"
  IfFileExists "$INSTDIR\_internal" un.cleanup_failed

  SetShellVarContext all
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Desinstalar ${APP_NAME}.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\${APP_ICON_FILE}"
  Delete "$INSTDIR\config.json"
  RMDir /r "$INSTDIR"

  !insertmacro UnregisterOpenWithExtension .mp4
  !insertmacro UnregisterOpenWithExtension .mkv
  !insertmacro UnregisterOpenWithExtension .webm
  !insertmacro UnregisterOpenWithExtension .mov
  !insertmacro UnregisterOpenWithExtension .avi
  !insertmacro UnregisterOpenWithExtension .flv
  !insertmacro UnregisterOpenWithExtension .ts
  !insertmacro UnregisterOpenWithExtension .m4v
  !insertmacro UnregisterOpenWithExtension .wmv
  !insertmacro UnregisterOpenWithExtension .mts
  !insertmacro UnregisterOpenWithExtension .m2ts
  !insertmacro UnregisterOpenWithExtension .3gp
  !insertmacro UnregisterOpenWithExtension .mpg
  !insertmacro UnregisterOpenWithExtension .mpeg
  !insertmacro UnregisterOpenWithExtension .vob
  !insertmacro UnregisterOpenWithExtension .mp3
  !insertmacro UnregisterOpenWithExtension .aac
  !insertmacro UnregisterOpenWithExtension .wav
  !insertmacro UnregisterOpenWithExtension .flac
  !insertmacro UnregisterOpenWithExtension .ogg
  !insertmacro UnregisterOpenWithExtension .m4a
  !insertmacro UnregisterOpenWithExtension .wma
  !insertmacro UnregisterOpenWithExtension .opus
  !insertmacro UnregisterOpenWithExtension .alac
  DeleteRegKey HKLM "${OPENWITH_APP_KEY}"
  System::Call 'shell32.dll::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "${APP_REGISTRY_KEY}"
  Goto un.done

un.cleanup_failed:
  IfSilent un.cleanup_failed_silent
  MessageBox MB_OK|MB_ICONSTOP "Não foi possível remover completamente os arquivos de ${APP_NAME}. A entrada de desinstalação foi mantida para permitir nova tentativa. Feche o aplicativo e tente novamente."
un.cleanup_failed_silent:
  SetErrorLevel 1
  Quit

un.abort:
  SetErrorLevel 1
  Quit

un.done:
SectionEnd

