"""Tests for the auth_setup module.

The setup_gmail_oauth2 function requires a real browser-based OAuth flow
and cannot be meaningfully unit-tested without mocking the entire Google
auth stack. The credential-file parsing it delegates to is tested in
test_browser_auth.py::TestLoadClientCredentials.
"""
