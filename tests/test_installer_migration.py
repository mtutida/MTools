from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class InstallerMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nsi = (ROOT / "CompactMe.nsi").read_text(encoding="utf-8-sig")
        cls.build = (ROOT / "build_installer_nsis.bat").read_text(encoding="utf-8")

    def test_new_installer_identity_targets_compactme(self):
        self.assertIn('!define APP_NAME "MTools CompactMe"', self.nsi)
        self.assertIn('!define APP_EXE "CompactMe.exe"', self.nsi)
        self.assertIn('!define APP_DIR "dist\\CompactMe"', self.nsi)
        self.assertIn('!define APP_INSTALL_DIR "MTools\\CompactMe"', self.nsi)
        self.assertIn('InstallDir "$PROGRAMFILES64\\${APP_INSTALL_DIR}"', self.nsi)
        self.assertIn('!define OUTPUT_FILE "MTools_CompactMe_Setup_${APP_VERSION}.exe"', self.nsi)

    def test_legacy_rc393_identifier_is_preserved_and_called_for_upgrade(self):
        self.assertIn('!define LEGACY_UNINSTALL_KEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\CompriMidia"', self.nsi)
        self.assertIn('ExecWait', self.nsi)
        self.assertIn('Call MigrateLegacyRc393', self.nsi)
        self.assertIn('Call CleanupLegacyCompatibilityResidue', self.nsi)

    def test_legacy_uninstaller_left_by_nsis_execwait_is_removed(self):
        migration = self.nsi.split('Function MigrateLegacyRc393', 1)[1].split('FunctionEnd', 1)[0]
        self.assertIn('ExecWait', migration)
        self.assertIn('Delete "$0\\uninstall.exe"', migration)
        self.assertIn('IfFileExists "$0\\uninstall.exe" legacy_blocked', migration)
        self.assertIn('RMDir "$0"', migration)

    def test_arp_and_open_with_use_new_executable(self):
        self.assertIn('!define UNINSTALL_KEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MTools CompactMe"', self.nsi)
        self.assertIn('!define OPENWITH_APP_KEY "Software\\Classes\\Applications\\CompactMe.exe"', self.nsi)
        self.assertIn('"$INSTDIR\\${APP_EXE}" "%1"', self.nsi)

    def test_uninstall_preserves_user_data_by_not_referencing_localappdata(self):
        uninstall = self.nsi.split('Section "Uninstall"', 1)[1]
        self.assertNotIn('LOCALAPPDATA', uninstall.upper())
        self.assertNotIn('APPDATA', uninstall.upper())

    def test_uninstall_detects_running_app_and_allows_retry_or_cancel(self):
        self.assertIn('Function un.EnsureCompactMeClosed', self.nsi)
        self.assertIn('tasklist.exe', self.nsi)
        self.assertIn('nsExec::ExecToStack', self.nsi)
        self.assertIn('${UnStrStr}', self.nsi)
        self.assertIn('MB_RETRYCANCEL', self.nsi)
        self.assertIn('IDRETRY un.check_running', self.nsi)
        self.assertIn('IDCANCEL un.process_cancelled', self.nsi)
        uninstall = self.nsi.split('Section "Uninstall"', 1)[1].split('SectionEnd', 1)[0]
        self.assertLess(uninstall.index('Call un.EnsureCompactMeClosed'), uninstall.index('Delete "$INSTDIR\\${APP_EXE}"'))

    def test_uninstall_keeps_registration_if_payload_cannot_be_removed(self):
        uninstall = self.nsi.split('Section "Uninstall"', 1)[1].split('SectionEnd', 1)[0]
        self.assertIn('IfFileExists "$INSTDIR\\${APP_EXE}" un.cleanup_failed', uninstall)
        self.assertIn('IfFileExists "$INSTDIR\\_internal" un.cleanup_failed', uninstall)
        registry_removal = uninstall.index('DeleteRegKey HKLM "${UNINSTALL_KEY}"')
        self.assertLess(uninstall.index('IfFileExists "$INSTDIR\\${APP_EXE}" un.cleanup_failed'), registry_removal)
        self.assertLess(uninstall.index('IfFileExists "$INSTDIR\\_internal" un.cleanup_failed'), registry_removal)
        self.assertIn('SetErrorLevel 1', uninstall)
        self.assertIn('Quit', uninstall)

    def test_build_script_requires_compactme_artifact_and_spec(self):
        self.assertIn('dist\\CompactMe\\CompactMe.exe', self.build)
        self.assertIn('CompactMe.nsi', self.build)
        self.assertIn('/INPUTCHARSET UTF8', self.build)

if __name__ == "__main__":
    unittest.main()
