# GoYou bundled sing-box client

The release build downloads the platform-specific sing-box binary into this
directory before Tauri packages the application. The binary is intentionally
ignored by Git because it is generated per target platform.

Supported release targets currently include:

- macOS Intel (`darwin-amd64`)
- macOS Apple Silicon (`darwin-arm64`)
- Windows x64 (`windows-amd64`)
- Linux x64 (`linux-amd64`)

Do not place credentials or generated sing-box configuration files here.
