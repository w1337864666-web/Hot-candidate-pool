import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StartupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ps1 = (ROOT / "scripts" / "start_agent.ps1").read_text(encoding="utf-8")
        self.bat = (ROOT / "start_agent.bat").read_text(encoding="utf-8")

    def test_launcher_delegates_to_powershell_script(self) -> None:
        self.assertIn("scripts\\start_agent.ps1", self.bat)
        self.assertIn("-ExecutionPolicy Bypass", self.bat)

    def test_script_checks_environment_and_supports_public_parameters(self) -> None:
        self.assertIn(".venv\\Scripts\\python.exe", self.ps1)
        self.assertIn(".env.example", self.ps1)
        self.assertIn("[switch]$NoBrowser", self.ps1)
        self.assertIn("[switch]$CheckOnly", self.ps1)
        self.assertIn("[int]$Port = 8000", self.ps1)
        self.assertIn("/health", self.ps1)
        self.assertIn("Start-Process $candidatesUrl", self.ps1)
        self.assertIn("finally", self.ps1)
        self.assertIn("add_CancelKeyPress", self.ps1)
        self.assertIn("Stop-Process -Id $serverProcess.Id", self.ps1)

    def test_script_only_reports_firewall_failures(self) -> None:
        self.assertIn("WinError 10013", self.ps1)
        self.assertNotIn("New-NetFirewallRule", self.ps1)
        self.assertNotIn("Set-NetFirewallProfile", self.ps1)
        self.assertNotIn("Disable-NetFirewallProfile", self.ps1)

    def test_proxy_values_are_not_printed_or_hardcoded(self) -> None:
        self.assertIn("HTTP_PROXY", self.ps1)
        self.assertIn("HTTPS_PROXY", self.ps1)
        self.assertIn("Import-ProxySettings", self.ps1)
        self.assertIn("value hidden", self.ps1)
        self.assertNotIn("127.0.0.1:7890", self.ps1)
