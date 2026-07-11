# Sync this vault to your Mac

The vault lives on the NAS. Three ways to read/edit it from your Mac, best first.

## Option 1: Syncthing (recommended)

Two-way, real-time, no cloud hop. Best if you edit notes on both sides (e.g. the Inbox).

1. On the NAS: install Syncthing — via Docker (a small sidecar container) or your NAS app center if it ships one.
2. On the Mac: `brew install syncthing` then `brew services start syncthing`.
3. Open each Syncthing web UI (NAS: its container port; Mac: `localhost:8384`) and add the other as a remote device using its device ID.
4. On the NAS side, share the vault's root folder (the one containing `RoboCo/` and `.obsidian/`).
5. Accept the share on the Mac and pick a local folder to receive it into. Open that folder as an Obsidian vault.
6. Set the share type to **Send & Receive** on both sides so edits on the Mac (e.g. dropping a note in the Inbox) sync back to the NAS.
7. Add a `.stignore` entry on both devices for `.obsidian/workspace.json`:
   ```
   .obsidian/workspace.json
   ```
Without this, the NAS and the Mac fight over which panes/tabs are open every time either one writes it — annoying, harmless, but noisy. Everything else in `.obsidian/` (themes, plugin settings) is fine to sync.

Conflicts: Syncthing keeps a `.sync-conflict-*` copy instead of overwriting — the vault's alias-based wikilinks mean a rename or an occasional conflict copy never breaks a link.

## Option 2: SMB mount (simpler, read-mostly)

Good if you mostly just want to read the vault and rarely edit.

1. Share the vault's parent folder over SMB from the NAS (most NAS OSes have this built in).
2. On the Mac: Finder → Go → Connect to Server (`smb://<nas-address>/<share>`).
3. Open the mounted folder as an Obsidian vault.

Gotchas:
- Do not also let iCloud Drive sync the same folder — iCloud and an SMB mount both trying to own file versions corrupts an Obsidian vault's index. Keep the vault off iCloud Drive entirely if it's SMB-mounted.
- A vault on a network share is slower for Obsidian's search/graph indexing than local or Syncthing-synced disk, and a network hiccup mid-write can corrupt `.obsidian/workspace.json` (harmless — delete it, Obsidian regenerates it) or, rarely, a note being saved.
- No offline access — if the NAS is down or the Mac is off-network, the vault is unreachable.

## Option 3: Obsidian Sync (paid)

Obsidian's own end-to-end-encrypted sync service. Simplest to set up (no NAS-side install), costs a monthly subscription, and syncs through Obsidian's cloud rather than directly NAS-to-Mac. Worth it if you want the vault on more devices (phone, iPad) with zero extra infrastructure. Otherwise Syncthing is the same result for free, one extra install.
