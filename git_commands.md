# Git Mastery Guide: Essential Commands & Workflows

This guide provides a comprehensive overview of the most common Git commands, their roles in the development lifecycle, and practical examples of how to use them.

---

## 1. The Core Lifecycle
Git works across three "zones": the **Working Directory**, the **Staging Area** (Index), and the **Repository**.

### `git status`
*   **Role:** Shows the state of the working directory and the staging area.
*   **When to use:** Use this constantly to see which files are modified, staged, or untracked.
*   **Command:** `git status`

### `git add`
*   **Role:** Moves changes from the working directory to the staging area.
*   **How to use:**
    *   `git add <file>`: Stage a specific file.
    *   `git add .`: Stage all changes in the current directory and subdirectories.
    *   `git add -p`: Stage changes interactively (hunk by hunk).

### `git commit`
*   **Role:** Records the staged changes as a permanent snapshot in the repository's history.
*   **How to use:**
    *   `git commit -m "Your descriptive message"`: Standard commit with a message.
    *   `git commit --amend`: Modify the last commit (useful for fixing typos).

---

## 2. Remote Collaboration
Syncing your local work with a server (like GitHub).

### `git push`
*   **Role:** Uploads local repository content to a remote repository.
*   **How to use:**
    *   `git push origin main`: Push your `main` branch to the `origin` remote.
    *   `git push -u origin <branch>`: Push and set the upstream tracking (only needed the first time).

### `git pull`
*   **Role:** Fetches and simplifies merging from a remote branch into your current branch.
*   **When to use:** Run this before starting work to ensure you have the latest updates from your team.
*   **Command:** `git pull origin main`

### `git fetch`
*   **Role:** Downloads objects and refs from another repository without merging them into your local work.
*   **When to use:** When you want to see what others have done without affecting your current code.

---

## 3. Branching & Feature Development
Branches allow you to work on different features or bug fixes in isolation.

### `git branch`
*   **Role:** Lists, creates, or deletes branches.
*   **How to use:**
    *   `git branch`: List local branches.
    *   `git branch <name>`: Create a new branch.
    *   `git branch -d <name>`: Delete a branch.

### `git checkout` (or `git switch`)
*   **Role:** Switches between branches or restores working tree files.
*   **How to use:**
    *   `git checkout <branch-name>`: Switch to an existing branch.
    *   `git checkout -b <new-branch>`: Create and switch to a new branch simultaneously.

### `git merge`
*   **Role:** Joins two or more development histories together.
*   **Typical Workflow:**
    1.  Switch to the target branch: `git checkout main`
    2.  Merge the feature branch: `git merge feature-login`

---

## 4. Inspection & Debugging
Understanding what happened in the past.

### `git log`
*   **Role:** Shows the commit history.
*   **Pro-tip:** `git log --oneline --graph --decorate` provides a beautiful, condensed view of the branch history.

### `git diff`
*   **Role:** Shows changes between commits, commit and working tree, etc.
*   **How to use:**
    *   `git diff`: See unstaged changes.
    *   `git diff --staged`: See staged changes.

---

## 5. The "Panic" Commands (Undoing Changes)

### `git reset`
*   **Role:** Undoes changes.
    *   `git reset --soft HEAD~1`: Undo the last commit but keep the changes staged.
    *   `git reset --hard HEAD~1`: **CAUTION:** Completely wipes the last commit and all changes.

### `git stash`
*   **Role:** Temporarily shelves (stashes) changes you've made to your working copy so you can work on something else, and then come back and re-apply them later.
*   **How to use:**
    *   `git stash`: Save current changes.
    *   `git stash pop`: Restore the last stashed changes.

---

## 6. Configuration

### `git remote`
*   **Role:** Manage the set of tracked repositories.
*   **How to use:**
    *   `git remote -v`: List remotes.
    *   `git remote add origin <url>`: Connect your local repo to a URL.
    *   `git remote set-url origin <url>`: Update the URL of an existing remote.

---

## Summary Cheat Sheet

| Command | Action |
| :--- | :--- |
| `git status` | Check status of files |
| `git add .` | Stage all changes |
| `git commit -m ""` | Save changes with a message |
| `git push` | Send changes to GitHub |
| `git pull` | Get latest changes from GitHub |
| `git checkout -b` | Create and move to new branch |
| `git log` | View history |

> [!TIP]
> Always run `git status` before and after every major command to ensure you are where you think you are!
