Name:           mailroom
Version:        1.0.1
Release:        1%{?dist}
Summary:        Email toolkit for AI assistants and command-line scripting
License:        MIT
URL:            https://github.com/SmartLayer/mailroom
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  python3-wheel

Requires:       python3 >= 3.11
Requires:       python3-imapclient >= 3.0.0
Requires:       python3-typer >= 0.15.0
Requires:       python3-requests >= 2.32.0
Requires:       python3-dotenv >= 1.0.0

%description
Mailroom provides CLI commands for searching, reading, moving, flagging,
and replying to emails over IMAP. It also offers an MCP (Model Context
Protocol) server mode for integration with AI assistants.

The RPM package provides all CLI commands. The MCP server subcommand
requires the python3-mcp package; users who need MCP mode should install
via pipx or Homebrew instead.

%prep
%autosetup -n %{name}-%{version}

%build
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .

%install
# Unpack wheel directly to work around Debian sysconfig patches.
# On Fedora, replace this block with: %%pyproject_install
PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_DIR=%{buildroot}/usr/lib/python${PYTHON_VER}/site-packages
mkdir -p "${SITE_DIR}" %{buildroot}/usr/bin
python3 -c "
import zipfile, sys
with zipfile.ZipFile(sys.argv[1]) as whl:
    whl.extractall(sys.argv[2])
" dist/mailroom-*.whl "${SITE_DIR}"

# Create entry-point script
cat > %{buildroot}/usr/bin/mailroom << 'ENTRY'
#!/usr/bin/python3
from mailroom.__main__ import main
main()
ENTRY
chmod 755 %{buildroot}/usr/bin/mailroom

# Install man page
install -Dpm 644 debian/mailroom.1 %{buildroot}%{_mandir}/man1/mailroom.1

%files
%license LICENSE
%doc README.md
/usr/bin/mailroom
/usr/lib/python*/site-packages/mailroom/
/usr/lib/python*/site-packages/mailroom-*.dist-info/
%{_mandir}/man1/mailroom.1*

%changelog
* Mon Apr 06 2026 Weiwu Zhang <a@colourful.land> - 1.0.1-1
- Rename CLI commands to aerc-aligned short verbs (search, move, reply, etc.)
- Rename MCP tools to kebab-case (search, move, mark-read, etc.)
- Add read command to view email content
- Add folders command to list email folders
- Normalize all commands to use --folder/-f and --uid/-u named flags
- Rename import-email to copy (JMAP alignment)
- Rename process-email to triage, download-attachment to save, etc.

* Fri Apr 03 2026 Weiwu Zhang <a@colourful.land> - 1.0.0-1
- Initial package
