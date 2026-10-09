Unicode true
!include "MUI2.nsh"
!define MODE "FULL"
!define BUILD_TAG "0.1.1"
!ifdef APPONLY
  !undef MODE
  !define MODE "APPONLY"
!endif
!ifdef RUNTIME
  !undef MODE
  !define MODE "RUNTIME"
!endif
Name "MTools CompactMe Shared Media Candidate ${MODE}"
OutFile "..\candidate_output\MTools_CompactMe_${MODE}_Candidate_${BUILD_TAG}.exe"
InstallDir "$PROGRAMFILES64\MTools\CompactMe-Candidate-${MODE}"
!ifdef RUNTIME
  InstallDir "$PROGRAMFILES64\MTools\Shared\media"
!endif
RequestExecutionLevel admin
SetCompress off
Page directory
Page instfiles
Section
  SetOutPath "$INSTDIR"
  !if "${MODE}" == "FULL"
    File /r "..\candidate_output\installer_staging\full\CompactMe\*"
  !else if "${MODE}" == "APPONLY"
    File /r "..\candidate_output\installer_staging\apponly\CompactMe\*"
    ExpandEnvStrings $0 "%ProgramData%"
    CreateDirectory "$0\MTools\Shared\media\dependents"
    IfErrors 0 +2
    Abort
    GetTempFileName $7 "$0\MTools\Shared\media\dependents"
    IfErrors 0 +2
    Abort
    Rename "$7" "$7.dependent"
    IfErrors 0 +2
    Abort
    FileOpen $9 "$7.dependent" w
    IfErrors 0 +2
    Abort
    FileWrite $9 "$INSTDIR"
    IfErrors 0 +2
    Abort
    FileClose $9
    FileOpen $8 "$INSTDIR\dependency.marker.name" w
    FileWrite $8 "$7.dependent"
    FileClose $8
  !else
    SetOutPath "$INSTDIR"
    File /r "..\candidate_output\installer_staging\runtime\media\*"
  !endif
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd
Section Uninstall
  !ifdef APPONLY
    ExpandEnvStrings $0 "%ProgramData%"
    FindFirst $R0 $R1 "$0\MTools\Shared\media\dependents\*.dependent"
    StrCmp $R1 "" apponly_dependents_done
  apponly_dependent_next:
    StrCpy $R2 "$0\MTools\Shared\media\dependents\$R1"
    FileOpen $R3 $R2 r
    IfErrors apponly_dependent_continue
    FileRead $R3 $R4
    FileClose $R3
    StrCmp $R4 "$INSTDIR" 0 apponly_dependent_continue
    Delete $R2
  apponly_dependent_continue:
    FindNext $R0 $R1
    IfErrors apponly_dependents_done
    Goto apponly_dependent_next
  apponly_dependents_done:
    FindClose $R0
  !endif
  RMDir /r "$INSTDIR"
SectionEnd

Function un.onInit
  ExpandEnvStrings $0 "%ProgramData%"
  CreateDirectory "$0\MTools\Shared\media\locks"
  StrCpy $1 "$0\MTools\Shared\media\locks\media-0.1.0-win-x64-candidate.lock"
  System::Call 'kernel32::CreateFile(t r1, i 0x40000000, i 0, i 0, i 1, i 0x80, i 0) i.r6'
  IntCmp $6 -1 lock_failed check_inuse check_inuse
lock_failed:
  MessageBox MB_ICONSTOP|MB_OK "O lock do runtime está ocupado; a remoção foi cancelada."
  Abort
check_inuse:
  System::Call 'kernel32::GetCurrentProcessId() i.R0'
  System::Call 'kernel32::GetCurrentProcess() i.R1'
  System::Call 'kernel32::GetProcessTimes(i R1, *l .R2, *l .R3, *l .R4, *l .R5) i.R6'
  IntCmp $R6 0 process_time_failed process_time_ready process_time_ready
process_time_failed:
  Delete "$0\MTools\Shared\media\locks\media-0.1.0-win-x64-candidate.lock"
  MessageBox MB_ICONSTOP|MB_OK "Não foi possível registrar a identidade do processo proprietário."
  Abort
process_time_ready:
  StrCpy $2 '{"ownerType":"nsis-removal","pid":$R0,"creationTimeLow":$R2,"creationTimeHigh":$R3}'
  StrLen $3 $2
  IntOp $3 $3 * 2
  System::Call 'kernel32::WriteFile(i r6, t r2, i r3, *i .R7, i 0) i.R8'
  IntCmp $R8 0 write_failed check_inuse_done check_inuse_done
write_failed:
  System::Call 'kernel32::CloseHandle(i r6)'
  Delete "$0\MTools\Shared\media\locks\media-0.1.0-win-x64-candidate.lock"
  MessageBox MB_ICONSTOP|MB_OK "Não foi possível registrar o proprietário do lock."
  Abort
check_inuse_done:
  IfFileExists "$0\MTools\Shared\media\inuse\media-0.1.0-win-x64-candidate" 0 checkdeps
  MessageBox MB_ICONSTOP|MB_OK "O runtime está marcado como em uso; encerre os aplicativos dependentes antes de remover."
  System::Call 'kernel32::CloseHandle(i r6)'
  Delete "$0\MTools\Shared\media\locks\media-0.1.0-win-x64-candidate.lock"
  Abort
checkdeps:
  !ifdef RUNTIME
  IfFileExists "$0\MTools\Shared\media\dependents\*.dependent" 0 dependents_done
  MessageBox MB_ICONSTOP|MB_OK "Há aplicativos AppOnly instalados que dependem deste runtime."
  System::Call 'kernel32::CloseHandle(i r6)'
  Delete "$0\MTools\Shared\media\locks\media-0.1.0-win-x64-candidate.lock"
  Abort
  !endif
dependents_done:
FunctionEnd

Function un.onUninstSuccess
  System::Call 'kernel32::CloseHandle(i r6)'
  Delete "$0\MTools\Shared\media\locks\media-0.1.0-win-x64-candidate.lock"
FunctionEnd
