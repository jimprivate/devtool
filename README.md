# jimprivate/devtool

Personal tool collection with cross-platform bootstrap.

## Repo structure

```
devtool/                 <- GitHub repo (source of truth)
├── install.py            <- One-time setup (cloned to ~/devtool/)
├── deploy-win.ps1       <- Windows installer
├── deploy-mac.sh        <- macOS installer
├── deploy-linux.sh      <- Linux installer
└── apps/                <- Your actual tools (installed to ~/devtool/apps/)
    ├── go-claude.py
    ├── go-github.py      <- GitHub: push, pull, new, init
    └── go-rclone.py      <- Google Drive: upload, download, sync, list, browse
```

## Workflow

**One-time setup:**
```powershell
# Windows
irm https://raw.githubusercontent.com/jimprivate/devtool/master/deploy-win.ps1 | iex
```

**Daily use:**
```powershell
# Edit tools in ~/devtool/apps/

# GitHub operations (from any directory)
go-github            <- Interactive menu
go-github push "msg" <- Commit + push current repo
go-github pull       <- Pull latest
go-github new myrepo <- Create GitHub repo (private)
go-github init myrepo <- Create repo + git init + first push

# Pull latest on another machine
.\deploy-win.ps1
```

**Google Drive (rclone):**
```powershell
go-rclone                        # Interactive menu
go-rclone setup                  # One-time: install rclone + authorize Google
go-rclone upload  .\photos gdrive:backup/photos   # copy local -> Drive
go-rclone download gdrive:docs .  # copy Drive -> local
go-rclone sync    .\notes gdrive:notes            # mirror (destructive)
go-rclone list    gdrive:backup                   # list Drive contents
go-rclone browse                                  # pick a path interactively
```

## Files explained

| File | Role | Changes? |
|------|------|----------|
| `apps/*.py` | Your actual tools | Yes — edit here, sync |
| `install.py` | One-time setup via git | Rarely |
| `deploy-*.ps1/.sh` | Downloads & runs install | Rarely |
| `gh.py` | GitHub CLI | As needed |

## Environment

- `GITHUB_TOKEN` — required for `gh new` and `gh init`. Generate at:
  https://github.com/settings/tokens (scopes: `repo`, `delete_repo`)
