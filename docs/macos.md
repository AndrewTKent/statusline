# macOS apps

Three companion apps that read the same data files — no extra API calls.

## Menu bar app

<img width="24" height="24" alt="green dot" src="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'><circle cx='12' cy='12' r='8' fill='%2327ae60'/></svg>"> Color-coded icon: green = ok, yellow = rate limit 70%+, red = 90%+ or context critical.

Click for a SwiftUI popover with full dashboard.

```bash
cd macos/ClaudeMenuBar
./build.sh      # Compiles with swiftc — no Xcode needed
./install.sh    # Copies to ~/Applications, auto-starts at login
```

## Raycast extension

Search "Claude Status" for a full metric list, or pin to menu bar for always-visible `$12.34 | 5hr: 45%`.

```
macos/claude-raycast/    # TypeScript — ready when Raycast is installed
```

## Widget bridge

Consolidates all status data into `~/.claude/widget-snapshot.json` with a 24-hour cost sparkline. Foundation for WidgetKit desktop/lock screen widgets.

```bash
swift macos/claude-widget/Bridge/claude-widget-bridge.swift
```

Run on a 30s launchd timer for auto-refresh. See [`macos/claude-widget/README.md`](../macos/claude-widget/README.md) for setup.

