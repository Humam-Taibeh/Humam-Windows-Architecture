# Pull Request

## Summary

<!-- What does this PR do, and why? Link related issues: Fixes #123 -->

## Type of change

- [ ] 🐛 Bug fix (non-breaking)
- [ ] ✨ New feature (non-breaking)
- [ ] 💥 Breaking change
- [ ] 📚 Documentation / meta only
- [ ] 🎨 GUI / theming / UX
- [ ] 🔧 Refactor / performance

## Architectural contracts checklist

<!-- See CONTRIBUTING.md — PRs violating these will be asked to revise -->

- [ ] System-touching logic lives **only** in `src/backend/core.ps1`
- [ ] Any new GUI task has a matching `Invoke-GuiTask` case emitting exactly one `SUCCESS|…` / `ERROR|…` line
- [ ] New tweaks are **catalog entries**, not bespoke functions, and snapshot their original values
- [ ] `apps` lists in `menu_structure.py` mirror the backend `$Apps_*` arrays exactly
- [ ] Qt widgets are touched only from the GUI thread (signals for everything else)
- [ ] New prompts/retries respect `$Script:NonInteractive`

## Testing

<!-- Describe how you verified this change -->

- **Windows version/build:**
- **Mode tested:** GUI / terminal / both
- **Snapshot/restore verified** (for system changes): applied → *Reset All Tweaks* → original state confirmed: yes / no / n-a

## Housekeeping

- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `README.md` updated if features/usage/architecture changed
- [ ] Commit messages follow Conventional Commits
