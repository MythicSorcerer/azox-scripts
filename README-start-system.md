# Azox Minecraft Start System

## Files
- `start.sh`: Entry point (runs `mc_start.py`)
- `start.env`: Runtime configuration
- `mc_start.py`: Main launcher/update/check logic
- `boot_proxy.py`: Temporary Minecraft status proxy for boot MOTD
- `modrinth_plugins.json`: Managed Modrinth plugins
- `fetch-list.txt`: Modrinth plugin names/slugs to resolve with Search API (comma/newline separated)
- `plugin_allowlist.txt`: Keep these plugin jar patterns even if unmanaged
- `essentialsx_artifacts.txt`: EssentialsX artifacts to track from snapshots

## Run
```bash
cd /opt/minecraft/mc
./start.sh
```

Dry checks only:
```bash
./start.sh --no-run
```

Use a custom config:
```bash
./start.sh --config /opt/minecraft/mc/start.env
```

Config toggles:
- `UPDATE_PURPUR=true|false`
- `UPDATE_ESSENTIALSX=true|false`
- `UPDATE_MODRINTH=true|false`
- `REMOVE_LOCKS=true|false`
- `ENABLE_EULA=true|false`
- `PURGE_OLD_FILES=true|false`
- `NO_RUN=true|false` (same effect as `--no-run`)
- `JAR_FILE`

## Deployment Layout
Expected:
- `/opt/minecraft/mc` contains this script system.
- `/opt/minecraft/sv` is the server directory (`SERVER_DIR`).

## Modrinth Manifest
`modrinth_plugins.json` supports:
```json
{
  "plugins": [
    {
      "slug": "luckperms",
      "cleanup_prefix": "LuckPerms-",
      "filename": "OptionalFixedName.jar"
    }
  ]
}
```

Notes:
- `slug` (or `id`) is required.
- `cleanup_prefix` removes old matching jars after update.
- `filename` is optional; by default it uses the primary file from Modrinth.
- Entries in `fetch-list.txt` are also used. Each token is resolved through Modrinth Search API to a plugin slug.
- `fetch-list.txt` accepts comma-separated and/or newline-separated values.

## Unlisted Plugin Cleanup
- Script moves unmanaged jars from `plugins/` to `plugins/removed-plugins/`.
- It purges jars in `removed-plugins` older than `DELETED_MOD_RETENTION_DAYS`.
- `plugin_allowlist.txt` supports comma/newline-separated values.
- Allowlist values can be full globs (`Geyser-*.jar`) or base names (`azox-chat-watch`), which match versioned jars like `azox-chat-watch-1.0.0.jar`.
- Cleanup keeps plugins that match: allowlist, resolved Modrinth fetch list/manifest, or EssentialsX artifacts.
