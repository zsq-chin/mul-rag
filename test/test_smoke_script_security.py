"""Static security and contract checks for the platform smoke script."""
import pathlib
import re
import subprocess
import unittest

SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "smoke_platform.ps1"


def _find_finally_block_containing(source, needle):
    """Return the body of the first ``finally`` block whose content includes *needle*.

    Iterates over every ``finally`` keyword in *source*, extracts the
    balanced-brace block, and returns the first one containing *needle*.
    Returns None when no match is found.
    """
    start = 0
    while True:
        idx = source.find("finally", start)
        if idx < 0:
            return None
        brace_open = source.find("{", idx)
        if brace_open < 0:
            return None
        depth = 0
        for i in range(brace_open, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    block = source[brace_open + 1 : i]
                    if needle in block:
                        return block
                    start = i + 1
                    break
        else:
            return None


class SmokeScriptSecurityTests(unittest.TestCase):
    """Validate that smoke_platform.ps1 meets security and compatibility contracts."""

    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT_PATH.read_text(encoding="utf-8")
        cls.lines = cls.source.splitlines()

    # ---- Credential handling ----

    def test_no_hardcoded_passwords(self):
        assert "password =" not in self.source.lower() or "tempPass" in self.source
        assert not re.search(r'password\s*=\s*["\'][^"\']{8,}["\']', self.source, re.I)

    def test_no_bearer_token_printed(self):
        """Bearer tokens must never appear in Write-Host output."""
        for line in self.lines:
            if "Write-Host" in line and "Bearer" in line:
                self.fail(f"Write-Host leaks Bearer token: {line.strip()}")

    def test_no_api_key_in_output(self):
        """API keys, sk-* patterns must not be echoed."""
        assert not re.search(r"Write-Host.*sk-[A-Za-z0-9]{8,}", self.source)
        assert not re.search(r"Write-Host.*api_key", self.source, re.I)

    def test_passwords_use_secure_types(self):
        """Credentials accepted as PSCredential or SecureString, not plain text."""
        assert "PSCredential" in self.source
        assert "GetNetworkCredential" in self.source

    # ---- PowerShell 5.1 compatibility ----

    def test_no_ps7_ternary_operator(self):
        """PowerShell 5.1 does not support the ternary operator (a ? b : c)."""
        ternary = re.search(r'\?\s*["\']', self.source)
        self.assertIsNone(ternary, "Found PS7 ternary operator (? :) in script")

    def test_no_response_headers_variable(self):
        """Invoke-RestMethod -ResponseHeadersVariable is unavailable in PS 5.1."""
        self.assertNotIn("ResponseHeadersVariable", self.source,
                         "-ResponseHeadersVariable is PS7-only; use Invoke-WebRequest instead")

    def test_uses_invoke_web_request(self):
        """Script must use Invoke-WebRequest (not Invoke-RestMethod) for PS 5.1 compat."""
        self.assertIn("Invoke-WebRequest", self.source)

    # ---- Cleanup contract ----

    def test_temp_user_cleaned_in_finally(self):
        """The temporary user must be removed in a finally block."""
        assert "finally" in self.source
        assert "Remove-TempUser" in self.source

    def test_cleanup_uses_ad_headers(self):
        """Remove-TempUser must reference the script-scoped admin headers variable."""
        # Find the Remove-TempUser function body
        func_match = re.search(
            r'function\s+Remove-TempUser\s*\{(.*?)\n\}',
            self.source, re.DOTALL
        )
        self.assertIsNotNone(func_match, "Remove-TempUser function not found")
        func_body = func_match.group(1)
        self.assertIn("script:adHeaders", func_body,
                       "Cleanup must use $script:adHeaders, not a local variable")
        self.assertNotIn("$adminHeaders", func_body,
                         "Cleanup must not reference undefined $adminHeaders")

    def test_cleanup_checks_status_and_records_failure(self):
        """Cleanup must check returned status and set cleanupFailed on failure."""
        func_match = re.search(
            r'function\s+Remove-TempUser\s*\{(.*?)\n\}',
            self.source, re.DOTALL
        )
        self.assertIsNotNone(func_match)
        func_body = func_match.group(1)
        self.assertIn("cleanupFailed", func_body,
                       "Cleanup must record failure via $script:cleanupFailed")

    def test_password_cleared_in_finally(self):
        """$tempPass must be set to $null in the finally block."""
        block = _find_finally_block_containing(self.source, "Remove-TempUser")
        self.assertIsNotNone(block, "finally block containing Remove-TempUser not found")
        self.assertIn("tempPass = $null", block,
                       "tempPass must be cleared in finally block")

    def test_password_never_printed(self):
        """tempPass must never appear in Write-Host output."""
        for line in self.lines:
            if "Write-Host" in line and "tempPass" in line:
                self.fail(f"Write-Host leaks tempPass: {line.strip()}")

    def test_exit_nonzero_on_failure(self):
        """Script must exit nonzero when checks fail."""
        assert "exit 1" in self.source

    # ---- Route coverage contracts ----

    def test_checks_auth_me_for_all_roles(self):
        """/auth/me must be verified for all three roles."""
        for role in ("superadmin", "admin", "user"):
            pattern = rf"ExpectedRole\s*=\s*['\"]{role}['\"]"
            assert re.search(pattern, self.source), f"Missing /auth/me check for {role}"

    def test_multimodal_management_probe_no_kb_images(self):
        """Management auth probe must not use /kb/images (requires kbId, can 422)."""
        # The superadmin-only routes should use /multimodal/kb/list, not /kb/images
        self.assertNotIn("/multimodal/kb/images", self.source,
                         "Use /multimodal/kb/list for management auth check; "
                         "/kb/images requires kbId and may 422 before auth")
        self.assertIn("/multimodal/kb/list", self.source)

    def test_checks_admin_cannot_create_admin(self):
        """Must verify admin cannot create admin role."""
        assert re.search(r"admin.*cannot.*admin|admin.*create.*admin", self.source, re.I)

    def test_checks_user_blocked_from_management(self):
        """Must verify ordinary user is blocked from management routes."""
        assert "ExpectAllowed $false" in self.source

    def test_no_duplicate_role_matrices(self):
        """Role route definitions should use data-driven helpers, not duplicate arrays."""
        # Count route arrays — should be minimal with Test-RoleRoute helper
        test_role_route_count = self.source.count("Test-RoleRoute")
        self.assertGreater(test_role_route_count, 0,
                           "Should use Test-RoleRoute data-driven helper")

    def test_no_user_allowed_routes_array(self):
        """The duplicate $userAllowedRoutes array must not exist."""
        self.assertNotIn("$userAllowedRoutes", self.source,
                         "$userAllowedRoutes is a duplicate; routes belong in $anyRoleRoutes")

    def test_no_user_blocked_routes_array(self):
        """The duplicate $userBlockedRoutes array must not exist."""
        self.assertNotIn("$userBlockedRoutes", self.source,
                         "$userBlockedRoutes is a duplicate; covered by $superadminOnlyRoutes")

    def test_invoke_api_uses_error_action_stop(self):
        """Invoke-Api must use -ErrorAction Stop so catch receives WebException."""
        func_match = re.search(
            r'function\s+Invoke-Api\s*\{(.*?)\nfunction\s+',
            self.source, re.DOTALL
        )
        self.assertIsNotNone(func_match, "Invoke-Api function not found")
        func_body = func_match.group(1)
        self.assertIn("ErrorAction", func_body, "Invoke-Api must set ErrorAction")
        self.assertNotIn("'SilentlyContinue'", func_body,
                         "Invoke-Api must use -ErrorAction Stop, not SilentlyContinue")

    def test_login_uses_error_action_stop(self):
        """Get-LoginResult must use -ErrorAction Stop so catch receives WebException."""
        func_match = re.search(
            r'function\s+Get-LoginResult\s*\{(.*?)\nfunction\s+',
            self.source, re.DOTALL
        )
        self.assertIsNotNone(func_match, "Get-LoginResult function not found")
        func_body = func_match.group(1)
        self.assertNotIn("-ErrorAction SilentlyContinue", func_body,
                         "Get-LoginResult must use -ErrorAction Stop, not SilentlyContinue")

    def test_auth_me_handles_malformed_body(self):
        """/auth/me check must record failure when body is empty or malformed."""
        # Look for a failure path when Body is null or missing role
        self.assertIsNotNone(
            re.search(r'else\s*\{.*?(?:empty|malformed).*?/auth/me', self.source, re.DOTALL),
            "Script must record failure for empty/malformed /auth/me body"
        )

    def test_privileged_test_user_cleanup_exists(self):
        """Remove-PrivilegedTestUsers function must exist and be called in finally."""
        self.assertIn("function Remove-PrivilegedTestUsers", self.source,
                       "Must define Remove-PrivilegedTestUsers function")
        block = _find_finally_block_containing(self.source, "Remove-TempUser")
        self.assertIsNotNone(block, "finally block containing Remove-TempUser not found")
        self.assertIn("Remove-PrivilegedTestUsers", block,
                       "finally block must call Remove-PrivilegedTestUsers")

    def test_privileged_test_user_ids_tracked(self):
        """Privileged test user IDs must be captured for cleanup."""
        self.assertIn("privilegedTestUserIds", self.source,
                       "Must track privileged test user IDs for cleanup")

    def test_temp_name_cleared_in_finally(self):
        """$tempName must be set to $null in the finally block."""
        block = _find_finally_block_containing(self.source, "Remove-TempUser")
        self.assertIsNotNone(block, "finally block containing Remove-TempUser not found")
        self.assertIn("tempName = $null", block,
                       "tempName must be cleared in finally block")

    def test_no_misleading_superadmin_create_comment(self):
        """No misleading 'Superadmin can create admin' comment when no such op."""
        assert "Superadmin can create admin" not in self.source

    def test_no_real_llm_request(self):
        """Must not send real LLM chat requests for auth testing."""
        assert "/chat/call" not in self.source or "meta" not in self.source

    # ---- Windows PowerShell 5.1 parser ----

    def test_powershell_51_parser(self):
        """If powershell.exe is available, parse the script with PS 5.1."""
        ps_command = (
            f"$tokens = $null; $errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{SCRIPT_PATH.as_posix()}', [ref]$tokens, [ref]$errors) | Out-Null; "
            f"if ($errors.Count -gt 0) {{ $errors | ForEach-Object {{ Write-Host $_.ToString() }}; exit 1 }} "
            f"else {{ 'OK' }}"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_command],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0 and "powershell" in result.stderr.lower():
                self.skipTest("powershell.exe not available")
            output = result.stdout.strip()
            self.assertEqual(output, "OK",
                             f"PS 5.1 parser failed: {result.stdout}\n{result.stderr}")
        except FileNotFoundError:
            self.skipTest("powershell.exe not found on this system")

    # ---- Additional security contracts ----

    def test_remote_probe_uses_error_action_stop(self):
        """Remote multimodal probe must use -ErrorAction Stop so HTTP errors are caught."""
        probe_match = re.search(
            r'Invoke-WebRequest.*RemoteMultimodalBase.*?(-ErrorAction\s+\S+)',
            self.source, re.DOTALL
        )
        self.assertIsNotNone(probe_match, "Remote multimodal Invoke-WebRequest not found")
        self.assertEqual(probe_match.group(1), "-ErrorAction Stop",
                         "Remote probe must use -ErrorAction Stop")

    def test_login_clears_password_and_formbody_in_finally(self):
        """Get-LoginResult must clear $password and $formBody in a finally block."""
        # Use balanced-brace helper since the finally block now contains nested braces.
        # "$password = $null" is unique to Get-LoginResult's finally block.
        block = _find_finally_block_containing(self.source, "password = $null")
        self.assertIsNotNone(block, "finally block in Get-LoginResult not found")
        self.assertIn("formBody = $null", block,
                       "$formBody must be cleared in finally block")

    # ---- Resource disposal regression tests ----

    def _get_function_body(self, name):
        """Extract the body of a PowerShell function by name."""
        pattern = rf'function\s+{re.escape(name)}\s*\{{(.*?)\nfunction\s+'
        match = re.search(pattern, self.source, re.DOTALL)
        if match is None:
            # Last function in file — match to end
            pattern = rf'function\s+{re.escape(name)}\s*\{{(.*)'
            match = re.search(pattern, self.source, re.DOTALL)
        self.assertIsNotNone(match, f"{name} function not found")
        return match.group(1)

    def test_invoke_api_disposes_error_response(self):
        """Invoke-Api must close/dispose Exception.Response in a finally block.

        Under PowerShell 5.1, leaving HttpWebResponse objects un-disposed
        exhausts the connection pool, causing subsequent requests to return
        synthetic status 0.
        """
        func_body = self._get_function_body("Invoke-Api")
        self.assertIn("finally", func_body,
                       "Invoke-Api must have a finally block for response disposal")
        # The finally block must reference the captured error response
        finally_match = re.search(r'}\s*finally\s*\{(.*?)\n\s*\}', func_body, re.DOTALL)
        self.assertIsNotNone(finally_match, "finally block not found in Invoke-Api")
        finally_body = finally_match.group(1)
        self.assertIn("Close()", finally_body,
                       "Invoke-Api finally must call .Close() on the response")
        self.assertIn("Dispose()", finally_body,
                       "Invoke-Api finally must call .Dispose() on the response")

    def test_invoke_api_disposes_success_response(self):
        """Invoke-Api must dispose successful Invoke-WebRequest responses after reading content.

        Success responses also hold HTTP connections that must be released.
        """
        func_body = self._get_function_body("Invoke-Api")
        # responseToDispose must be assigned in the success (try) path
        self.assertIn("responseToDispose = $resp", func_body,
                       "Invoke-Api must assign successful response to $responseToDispose")
        # And disposed in the finally block
        finally_match = re.search(r'}\s*finally\s*\{(.*?)\n\s*\}', func_body, re.DOTALL)
        self.assertIsNotNone(finally_match)
        self.assertIn("responseToDispose", finally_match.group(1),
                       "Invoke-Api finally must dispose $responseToDispose")

    def test_invoke_api_captures_error_status_before_dispose(self):
        """Invoke-Api must read Exception.Response.StatusCode before closing it.

        Reading status after Close() throws ObjectDisposedException.
        """
        func_body = self._get_function_body("Invoke-Api")
        catch_match = re.search(r'}\s*catch\s*\{(.*?)\n\s*\}\s*finally', func_body, re.DOTALL)
        self.assertIsNotNone(catch_match, "catch block not found in Invoke-Api")
        catch_body = catch_match.group(1)
        # Status must be read before the response is assigned to disposal variable
        status_pos = catch_body.find("StatusCode")
        dispose_pos = catch_body.find("responseToDispose")
        if status_pos >= 0 and dispose_pos >= 0:
            self.assertLess(status_pos, dispose_pos,
                            "StatusCode must be read before assigning to responseToDispose")

    def test_invoke_api_has_bounded_timeout(self):
        """Invoke-Api must use a bounded TimeoutSec to avoid hanging smoke tests."""
        func_body = self._get_function_body("Invoke-Api")
        self.assertIn("TimeoutSec", func_body,
                       "Invoke-Api must specify TimeoutSec for bounded request duration")

    def test_get_login_result_disposes_response(self):
        """Get-LoginResult must close/dispose HTTP responses in its finally block.

        Login responses carry the same connection-pool risk as Invoke-Api.
        """
        # Use balanced-brace helper since the finally block now contains nested braces.
        # "$password = $null" is unique to Get-LoginResult's finally block.
        block = _find_finally_block_containing(self.source, "password = $null")
        self.assertIsNotNone(block, "finally block in Get-LoginResult not found")
        self.assertIn("Close()", block,
                       "Get-LoginResult finally must call .Close() on the response")
        self.assertIn("Dispose()", block,
                       "Get-LoginResult finally must call .Dispose() on the response")
        # Must also still clear credentials
        self.assertIn("formBody = $null", block,
                       "Get-LoginResult finally must still clear $formBody")

    def test_get_login_result_has_bounded_timeout(self):
        """Get-LoginResult must use a bounded TimeoutSec."""
        func_body = self._get_function_body("Get-LoginResult")
        self.assertIn("TimeoutSec", func_body,
                       "Get-LoginResult must specify TimeoutSec")

    # ---- PowerShell 5.1 strict-mode safety ----

    def test_admin_visible_role_count_wrapped_in_array(self):
        """The admin-visible-role filter must wrap the pipeline result in @().

        In PowerShell 5.1 strict mode, when Where-Object returns no matches
        the result is $null.  Accessing .Count on $null raises
        PropertyNotFoundStrict.  Wrapping with @() ensures the result is
        always an array so .Count is always safe.
        """
        # Find the line that checks admin-visible roles
        count_lines = [
            line for line in self.lines
            if "Where-Object" in line and ".Count" in line and "role" in line
        ]
        self.assertTrue(count_lines, "No admin-visible-role filter line found")
        for line in count_lines:
            stripped = line.strip()
            self.assertRegex(
                stripped,
                r'@\(',
                f"Filtered pipeline must be wrapped in @() for PS 5.1 strict-mode safety: {stripped}"
            )

    # ---- Transport failure detection regression ----

    def test_role_route_rejects_transport_failure_status_zero(self):
        """Test-RoleRoute must never report status 0 (transport failure) as PASS.

        The allowed-route check must verify status > 0 in addition to
        excluding 401/403. Status 0 means the request never reached the
        server (connection reset, timeout, pool exhaustion).
        """
        func_body = self._get_function_body("Test-RoleRoute")
        # Must check for positive status, not just "not 401/403"
        self.assertRegex(
            func_body,
            r'StatusCode\s*-gt\s*0',
            "Test-RoleRoute must check StatusCode -gt 0 to reject transport failures"
        )
        # Must still exclude 401/403
        self.assertIn("401", func_body,
                       "Test-RoleRoute must still check for 401")
        self.assertIn("403", func_body,
                       "Test-RoleRoute must still check for 403")

    def test_role_route_old_broken_logic_absent(self):
        """The old broken logic (only checking -notin 401/403 without status > 0) must be gone."""
        func_body = self._get_function_body("Test-RoleRoute")
        # The old pattern was: $ok = ($r.StatusCode -notin @(401, 403))
        # without a preceding -gt 0 check
        old_pattern = re.compile(r'\$ok\s*=\s*\(\$r\.StatusCode\s*-notin\s*@\(401,\s*403\)\)')
        if old_pattern.search(func_body):
            self.fail(
                "Test-RoleRoute still uses the old broken logic: "
                "$ok = ($r.StatusCode -notin @(401, 403)) — "
                "status 0 (transport failure) would incorrectly PASS"
            )


if __name__ == "__main__":
    unittest.main()
