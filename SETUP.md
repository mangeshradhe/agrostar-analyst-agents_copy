# Agrostar Analyst Agents — Team Setup Guide

There are two types of people:
- **Users** — use the analysts to answer business questions (everyone)
- **Editors** — update analyst context files (2–3 people only)

---

## Step 1 — Admin Only (Do Once)

> **Who does this:** Darpan (or whoever manages the Drive)

1. Open [Google Drive](https://drive.google.com)
2. Create a new **Shared Drive** (not "My Drive") called: `Agrostar Analyst Agents`
3. Inside it, create this folder structure:
   ```
   Agrostar Analyst Agents/
   ├── CLAUDE.md
   └── commands/
       └── dvs-analyst.md
   ```
4. Upload `CLAUDE.md` and `dvs-analyst.md` from this repo into the correct folders
5. Set permissions on the Shared Drive:
   - **Editors** (people allowed to update files): `Content manager` role
   - **Everyone else** (users): `Viewer` role
6. Share the Drive link with the whole team

---

## Step 2 — Every Team Member (Do Once on Your Machine)

### 2a. Install Google Drive for Desktop
Download and install: https://www.google.com/drive/download

Sign in with your Agrostar Google account. This will sync the shared Drive to your computer automatically.

### 2b. Find your local Drive path

After installing, open **Finder** (Mac) or **File Explorer** (Windows) and locate the synced folder. It will look like one of these:

**Mac:**
```
/Users/YOUR_NAME/Library/CloudStorage/GoogleDrive-YOUR_EMAIL/Shared drives/Agrostar Analyst Agents
```

**Windows:**
```
G:\Shared drives\Agrostar Analyst Agents
```

Copy this path — you'll need it in the next step.

### 2c. Set up Claude Code to use the shared analysts

Open **Terminal** (Mac) or **Command Prompt** (Windows) and run:

**Mac:**
```bash
mkdir -p ~/.claude/commands
ln -sf "/Users/YOUR_NAME/Library/CloudStorage/GoogleDrive-YOUR_EMAIL/Shared drives/Agrostar Analyst Agents/commands/dvs-analyst.md" ~/.claude/commands/dvs-analyst.md
ln -sf "/Users/YOUR_NAME/Library/CloudStorage/GoogleDrive-YOUR_EMAIL/Shared drives/Agrostar Analyst Agents/CLAUDE.md" ~/.claude/CLAUDE.md
```

> Replace `YOUR_NAME` and `YOUR_EMAIL` with your actual values.

**Windows (run as Administrator):**
```cmd
mklink "C:\Users\YOUR_NAME\.claude\commands\dvs-analyst.md" "G:\Shared drives\Agrostar Analyst Agents\commands\dvs-analyst.md"
mklink "C:\Users\YOUR_NAME\.claude\CLAUDE.md" "G:\Shared drives\Agrostar Analyst Agents\CLAUDE.md"
```

**What this does:** Creates a live link — whenever an Editor updates the file in Google Drive, your local Claude automatically gets the latest version. No manual downloads ever.

### 2d. Verify it worked

Open Terminal and run:
```bash
ls ~/.claude/commands/
```
You should see `dvs-analyst.md` listed.

---

## Step 3 — Using the Analyst (Everyone)

1. Open **Terminal**
2. Type `claude` and press Enter
3. Type `/dvs-analyst` to activate the DVS analyst
4. Ask your question in plain English

**Examples:**
```
/dvs-analyst
> What is the health of the DVS program for May 2026?
> Which stores have breached the 1-hour first action SLA this week?
> Show me state-wise B2C fulfillment breakdown for this month
```

---

## Step 4 — Updating Analyst Context (Editors Only)

When you want to add new business context, fix a wrong table join, or add a new metric:

1. Open the file directly in **Google Drive** (web browser)
2. Edit it — Google Docs won't work, use the `.md` file directly or download → edit → re-upload
3. Or if you have Google Drive for Desktop, edit the file in your synced local folder using any text editor (Notepad, TextEdit, VS Code)
4. Save — the change syncs to everyone's machine within 30–60 seconds

> **Rule:** Always test your change locally before saving to Drive. Ask a question through Claude that exercises the context you changed and verify the query and answer are correct.

---

## Adding a New Analyst

When a new analyst is ready (e.g., `sales-analyst.md`):

1. Editor adds the new `.md` file to the `commands/` folder in Drive
2. Every team member runs ONE new command in Terminal:

**Mac:**
```bash
ln -sf "/Users/YOUR_NAME/Library/CloudStorage/GoogleDrive-YOUR_EMAIL/Shared drives/Agrostar Analyst Agents/commands/sales-analyst.md" ~/.claude/commands/sales-analyst.md
```

After that, the new analyst auto-updates just like the others.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `/dvs-analyst` command not found | Run `ls ~/.claude/commands/` — if empty, redo Step 2c |
| Getting old answers after an update | Wait 60 seconds for Drive to sync, then restart Claude |
| Drive path not found | Open Finder → look for "Google Drive" in the left sidebar → right-click the file → "Get Info" to see the full path |
| Symlink command fails on Mac | Make sure `~/.claude/commands/` folder exists: run `mkdir -p ~/.claude/commands` first |
