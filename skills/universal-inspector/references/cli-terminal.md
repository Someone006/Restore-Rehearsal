# CLI and TUI inspection

Record the command as an argument array, working-directory label, terminal rows/columns, color mode,
exit code, stdout/stderr boundaries, and any fixture used. Redact prompts, environment values,
absolute paths, and shell history. Test `--help`, invalid input, non-interactive behavior, narrow
terminal layout, interruption, and failure cleanup when relevant.

For a TUI, capture both screen output and semantic keystroke sequence. A pretty terminal screenshot
does not replace assertions on exit status and output.
