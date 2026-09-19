# Formats

Seven render modes. Set `FORMAT=` in `~/.claude/statusline.conf` or `STATUSLINE_FORMAT=` env var.

## `default`: multi-line dashboard (the sample in the README)

The full cockpit, one labeled row per fact. Auto-falls-through to `narrow` when the detected terminal width is below `NARROW_THRESHOLD` (default 60 cols).

## `compact`: context + session only

Just the `context` and `session` rows — the two numbers that actually gate you.

## `narrow`: trimmed fallback for tight panels

Same facts as `default` (model+effort, dir+branch, context, 5h, 7d+cost), trimmed hard: short labels, 5–8 char bars scaled to `COLS`, no reset timestamps or breakdowns. Auto-selected under `default` when the panel is narrow; can also be set explicitly.

## `sigil`: single dense line

```
◈ Opus 4.6 · $2.14 ($8.90/d) · ●●●○○ 60% · ⎇ feature-123✦↑1[PR✓] · 42%⏱24:12 · 71%w
```

Width-adaptive: full detail (cost, daily aggregate, context, git, 5h rate, weekly) at ≥120 cols; drops the daily aggregate and weekly at ≥80; drops git detail to a bare branch name and rate to a bare percentage below 80. Good for tmux status bars or small terminals.

## `sparkline`: default + trend history

```
  ...default output...
  trend   cost▁▂▃▅▃▂▁▄▆█  rate▁▃▅▇█▇▅▃▂▁
```

Appends inline `▁▂▃▄▅▆▇█` mini-charts (cost and 5h-rate trend, last 15 sessions) read from `~/.claude/session-history.jsonl`. See if you're burning hotter today than yesterday.

## `rprompt`: zsh right-prompt

Writes zsh-formatted status to `~/.claude/rprompt.txt`. Add to `.zshrc`:

```zsh
_claude_rprompt() {
  local f=~/.claude/rprompt.txt
  [[ -f "$f" ]] || return
  local age=$(( $(date +%s) - $(stat -f %m "$f") ))
  (( age > 300 )) && { RPROMPT=""; return }
  RPROMPT="$(cat "$f")"
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _claude_rprompt
```

Claude metrics in your shell prompt gutter. Zero vertical space. Auto-hides after 5 minutes of inactivity. Also emits `sigil` to stdout for Claude Code's own status area.

## `iterm2`: native terminal status bar

Pushes structured data to iTerm2 via `OSC 1337;SetUserVar` or sets the Kitty window title via `OSC 2`. Auto-detects your terminal; also emits `sigil` to stdout as a fallback.

**iTerm2 setup:** Preferences → Profiles → Session → Status Bar → add "Interpolated String" components:

`\(user.claude_model)` &middot; `\(user.claude_cost)` &middot; `\(user.claude_ctx)` &middot; `\(user.claude_git)` &middot; `\(user.claude_rate)` &middot; `\(user.claude_timer)`

