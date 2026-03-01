# Azox Minecraft Start System

## Files
- `start.sh`: Entry point (runs `mc_start.py`)
- `start.env`: Runtime configuration
- `mc_start.py`: Main launcher/update/check logic
- `boot_proxy.py`: Temporary Minecraft status proxy for boot MOTD
- `modrinth_list.txt`: Modrinth plugin names/slugs to resolve via Search API (comma/newline separated)
- `essentialsx_list.txt`: EssentialsX Maven artifact names (newline separated)
- `exempt_list.txt`: Plugin names/patterns exempt from removal
- `removed_list.txt`: Append-only log of moved/purged plugin cleanup actions

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
- `OFFLINE_MODE=true|false` (forces `online-mode=false` and `enforce-secure-profile=false` in `server.properties`)
- `CRASH_REPAIR=true|false` (enable startup crash classification + auto-repair loop)
- `CRASH_REPAIR_MAX=6` (max repair attempts before give-up)
- `JAVA_BIN` (java executable for launch, auto-switched on Java version mismatch)
- `EXTRA_JVM_FLAGS` (optional extra JVM args, auto-reset on invalid-flag crash)
- `NO_RUN=true|false` (same effect as `--no-run`)
- `JAR_FILE`

## Deployment Layout
Expected:
- `/opt/minecraft/mc` contains this script system.
- `/opt/minecraft/sv` is the server directory (`SERVER_DIR`).

## Modrinth List
- `modrinth_list.txt` accepts comma-separated and/or newline-separated values.
- Each token is resolved through Modrinth Search API to a server-compatible project slug.

## Unlisted Plugin Cleanup
- Script moves unmanaged jars from `plugins/` to `plugins/removed-plugins/`.
- It purges jars in `removed-plugins` older than `DELETED_MOD_RETENTION_DAYS`.
- `exempt_list.txt` supports comma/newline-separated values.
- Exempt values can be full globs (`Geyser-*.jar`) or base names (`azox-chat-watch`), which match versioned jars like `azox-chat-watch-1.0.0.jar`.
- Cleanup keeps plugins that match: exempt list, resolved Modrinth list, or EssentialsX list.
- `removed_list.txt` is appended when files are moved or purged.

## Crash Auto-Repair
- Startup crashes are classified from console output using weighted rules and signature matching.
- Signature = SHA256 of first 25 log lines; repeated signatures trigger escalation.
- The system covers: EULA, Java mismatch, bad JVM flags, missing dependency, plugin incompatibility/conflicts, config parse errors, OOM, disk full, port conflict, permission errors, cache corruption, world corruption, datapack failure, modloader mismatch, native library errors, and unknown fallback phases.
- Unknown fallback phases: cache purge -> disable half plugins -> binary isolation step -> disable all plugins -> redownload Purpur jar -> give up.
