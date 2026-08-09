# GitHub Setup and Push Guide

## TL;DR

```bash
cd /workspaces/codespaces-blank
env -u GITHUB_TOKEN gh auth login --hostname github.com --web
gh auth setup-git --hostname github.com
git remote set-url origin https://github.com/valdronus/tradeskillArbitrage.git
git add Documentation/AuctioneerInfo.md Documentation/AuctioneerStatisticMethods.md Documentation/NewStatModulePlan.md Documentation/GitHubSetup.md
git commit -m 'Add Auctioneer documentation and setup guide'
git push origin main
```

If the push fails with a 403, try again with:

```bash
env -u GITHUB_TOKEN git push origin main
```

This works 90% of the time in a fresh session because it avoids using an invalid `GITHUB_TOKEN` environment override.

This guide explains the exact commands used to authenticate, configure git, and push local changes to `https://github.com/valdronus/tradeskillArbitrage`.

> Do not paste any secret values into shared files. Use tokens only when prompted.

## 1. Initialize the repo and add files

If your workspace is not already a git repository:

```bash
cd /workspaces/codespaces-blank
git init
```

Move your markdown file into the Documentation folder:

```bash
mv AuctioneerInfo.md Documentation/AuctioneerInfo.md
```

Add files and commit them:

```bash
git add Documentation/AuctioneerInfo.md auctioneer_rope_to_sqlite.py
git commit -m 'Add Auctioneer parser and reference documentation'
```

## 2. Configure the repository remote

If the remote is not configured yet, add it:

```bash
git remote add origin https://github.com/valdronus/tradeskillArbitrage.git
```

If the remote already exists but points to the wrong URL, update it:

```bash
git remote set-url origin https://github.com/valdronus/tradeskillArbitrage.git
```

## 3. Authenticate with GitHub CLI

If `GITHUB_TOKEN` is set in your environment, remove it for the login flow:

```bash
cd /workspaces/codespaces-blank
env -u GITHUB_TOKEN gh auth login --hostname github.com --web
```

During `gh auth login` you will be prompted for:

1. protocol: `HTTPS`
2. authenticate git with GitHub credentials: `Yes`
3. web authentication flow: follow the device code instructions

After login, verify auth:

```bash
gh auth status --hostname github.com
```

Then configure git to use gh credentials:

```bash
gh auth setup-git --hostname github.com
```

If you prefer personal access token (PAT) auth instead of web auth, use:

```bash
env -u GITHUB_TOKEN gh auth login --hostname github.com
```

and choose:

- protocol: `HTTPS`
- authenticate GitHub CLI: `Paste an authentication token`

The PAT must include at least:

- `repo`
- `read:org`

## 4. Check git credential and remote setup

```bash
cd /workspaces/codespaces-blank
git remote -v
git status --short --branch
```

If git still tries to use stale credentials, clear them and retry:

```bash
git credential reject <<'EOF'
protocol=https
host=github.com
username=x-access-token
password=
EOF
```

## 5. Fetch remote state and merge if needed

If the remote already has history, fetch before pushing:

```bash
git fetch origin
```

If your local branch diverges from `origin/main`, merge:

```bash
git merge origin/main --allow-unrelated-histories -m 'Merge remote main into local branch'
```

## 6. Push your branch

If auth is configured correctly, push normally:

```bash
git push -u origin main
```

If you need to push explicitly with the current `gh` token:

```bash
cd /workspaces/codespaces-blank
TOKEN=$(gh auth token --hostname github.com)
git push https://x-access-token:$TOKEN@github.com/valdronus/tradeskillArbitrage.git main
```

## 7. Verify success

After the push completes, confirm the repo is updated:

```bash
git log --oneline --decorate --graph --all | head
```

## Notes

- `gh auth login` can be sensitive to stale environment variables. Use `env -u GITHUB_TOKEN` to clear `GITHUB_TOKEN` during login.
- Use only one authentication source at a time: either `gh` stored credentials or a PAT in your shell.
- If `git push` fails with `403`, retry with `env -u GITHUB_TOKEN git push origin main` because an invalid `GITHUB_TOKEN` environment variable can override the working `gh` credential helper.
- The `Documentation/GitHubSetup.md` file itself should not contain any token or secret values.
