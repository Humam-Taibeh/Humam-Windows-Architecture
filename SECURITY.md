# Security Policy

## 🛡️ Scope

**Pulse** runs with administrator privileges and modifies
registry keys, services, and installed software. Security issues in this
project can therefore have real impact on end-user machines. We take reports
seriously and appreciate responsible disclosure.

## 📦 Supported Versions

| Version | Supported |
|---|---|
| 6.x (current) | ✅ |
| < 6.0 | ❌ |

Only the latest release on `master` receives security fixes.

## 🚨 Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, report privately via one of:

1. **GitHub Security Advisories** (preferred):
   [Report a vulnerability](https://github.com/Humam-Taibeh/Pulse/security/advisories/new)
2. **Email**: humamtaibehh@gmail.com — use the subject line
   `[SECURITY] Pulse`

Include as much of the following as you can:

- A description of the vulnerability and its impact
- Steps to reproduce (Windows version/build, GUI vs terminal mode)
- Any proof-of-concept code or log excerpts
- Suggested remediation, if you have one

## ⏱️ What to Expect

- **Acknowledgement** of your report within **72 hours**
- A **triage decision** (accepted / needs info / declined) within **7 days**
- Credit in the release notes once a fix ships, unless you prefer anonymity

## 🔍 Areas of Particular Interest

- Elevation-of-privilege paths through the self-elevating launcher or the
  bundled `uac_admin` executable
- Command or argument injection into the `core.ps1 -Task` / `-AppIds`
  dispatch pipeline
- Tampering with snapshot/restore data that could corrupt recovery
- Supply-chain concerns in the winget catalogs (typosquatted or hijacked
  package IDs)

Thank you for helping keep users safe. 🙏
