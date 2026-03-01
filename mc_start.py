#!/usr/bin/env python3
import argparse
import datetime as dt
import fnmatch
import json
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "start.env"
MODRINTH_MANIFEST = SCRIPT_DIR / "modrinth_plugins.json"
FETCH_LIST_FILE = SCRIPT_DIR / "fetch-list.txt"
ALLOWLIST_FILE = SCRIPT_DIR / "plugin_allowlist.txt"
ESSENTIALS_ARTIFACTS_FILE = SCRIPT_DIR / "essentialsx_artifacts.txt"


class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def divider():
    print(f"{Ansi.GRAY}{'─' * 57}{Ansi.RESET}")


def icon_ok():
    return f"{Ansi.GREEN}[*]{Ansi.RESET}"


def icon_warn():
    return f"{Ansi.RED}[!]{Ansi.RESET}"


def icon_err():
    return f"{Ansi.RED}[!]{Ansi.RESET}"


def log(msg):
    print(f"{icon_ok()} {msg}")


def warn(msg):
    print(f"{icon_warn()} {msg}")


def err(msg):
    print(f"{icon_err()} {msg}")


def banner():
    print(f"{Ansi.RED}╔══════════════════════════════════════╗{Ansi.RESET}")
    print(f"{Ansi.RED}║         Azox Network Starting        ║{Ansi.RESET}")
    print(f"{Ansi.RED}║            Please stand by           ║{Ansi.RESET}")
    print(f"{Ansi.RED}╚══════════════════════════════════════╝{Ansi.RESET}")


def parse_env(path):
    cfg = {}
    if not path.exists():
        return cfg
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        cfg[key] = val
    return cfg


def update_env_value(path: Path, key: str, value: str, quote=False):
    new_line = f'{key}="{value}"' if quote else f"{key}={value}"
    if not path.exists():
        path.write_text(new_line + "\n", encoding="utf-8")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    found = False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(new_line)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(new_line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def remove_env_keys(path: Path, keys):
    if not path.exists():
        return
    keys = set(keys)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if any(stripped.startswith(f"{k}=") for k in keys):
            continue
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def bool_env(cfg, key, default=False):
    raw = cfg.get(key, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def int_env(cfg, key, default):
    try:
        return int(str(cfg.get(key, default)).strip())
    except Exception:
        return int(default)


def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "azox-mc-start/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def normalize_key(text: str):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def download_file(url, dst: Path, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "azox-mc-start/1.0"})
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with urllib.request.urlopen(req, timeout=timeout) as r, tmp.open("wb") as w:
        shutil.copyfileobj(r, w)
    tmp.replace(dst)


def parse_purpur_jar_name(jar_name):
    m = re.match(r"^purpur-(\d+\.\d+\.\d+)-(\d+)\.jar$", jar_name)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def port_is_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True, None
    except PermissionError as ex:
        return None, str(ex)
    except OSError:
        return False, None
    finally:
        s.close()


def update_server_properties(server_dir: Path, port: int):
    props = server_dir / "server.properties"
    if not props.exists():
        warn("server.properties not found; creating a minimal file.")
        props.write_text(f"server-port={port}\nquery.port={port}\n", encoding="utf-8")
        return True

    lines = props.read_text(encoding="utf-8").splitlines()
    changed = False
    seen_server_port = False
    seen_query_port = False
    out = []
    for line in lines:
        if line.startswith("server-port="):
            seen_server_port = True
            current = line.split("=", 1)[1].strip()
            if current != str(port):
                out.append(f"server-port={port}")
                changed = True
            else:
                out.append(line)
            continue
        if line.startswith("query.port="):
            seen_query_port = True
            current = line.split("=", 1)[1].strip()
            if current != str(port):
                out.append(f"query.port={port}")
                changed = True
            else:
                out.append(line)
            continue
        out.append(line)
    if not seen_server_port:
        out.append(f"server-port={port}")
        changed = True
    if not seen_query_port:
        out.append(f"query.port={port}")
        changed = True
    if changed:
        props.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def check_eula(server_dir: Path, enforce=True):
    eula = server_dir / "eula.txt"
    if not eula.exists():
        eula.write_text("eula=true\n", encoding="utf-8")
        log("EULA accepted by creating eula.txt")
        return True
    content = eula.read_text(encoding="utf-8")
    if "eula=true" in content:
        return True
    eula.write_text("eula=true\n", encoding="utf-8")
    log("EULA updated to eula=true")
    return True


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def purge_old_files(path: Path, days: int):
    now = time.time()
    cutoff = now - days * 86400
    removed = 0
    if not path.exists():
        return removed
    for p in path.glob("*.jar"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def read_essentials_artifacts():
    if not ESSENTIALS_ARTIFACTS_FILE.exists():
        return []
    out = []
    for raw in ESSENTIALS_ARTIFACTS_FILE.read_text(encoding="utf-8").splitlines():
        v = raw.strip()
        if not v or v.startswith("#"):
            continue
        out.append(v)
    return out


def read_csv_or_newline_tokens(path: Path):
    if not path.exists():
        return []
    tokens = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        for part in parts:
            if part:
                tokens.append(part)
    return tokens


def parse_maven_latest(xml_text):
    m_latest = re.search(r"<latest>([^<]+)</latest>", xml_text)
    return m_latest.group(1) if m_latest else None


def parse_maven_timestamp_build(xml_text):
    ts = re.search(r"<timestamp>([^<]+)</timestamp>", xml_text)
    bn = re.search(r"<buildNumber>([^<]+)</buildNumber>", xml_text)
    if not ts or not bn:
        return None, None
    return ts.group(1), bn.group(1)


class ProgressLine:
    def __init__(self, total, bar_length=22):
        self.total = max(1, int(total))
        self.bar_length = bar_length
        self.count_width = len(str(self.total))
        self.last_len = 0

    def update(self, done, current=None):
        done = max(0, min(int(done), self.total))
        pct = int((done / self.total) * 100)
        filled = int((done / self.total) * self.bar_length)
        bar = "=" * filled + " " * (self.bar_length - filled)
        line = f"{done:>{self.count_width}}/{self.total} [{bar}] {pct:3d}%"
        if current:
            line += f" | {current}"
        pad = " " * max(0, self.last_len - len(line))
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()
        self.last_len = len(line)

    def finish(self):
        sys.stdout.write("\n")
        sys.stdout.flush()


def update_essentialsx(plugins_dir: Path):
    artifacts = read_essentials_artifacts()
    if not artifacts:
        warn("No EssentialsX artifacts configured; skipping.")
        return [], []

    base_url = "https://repo.essentialsx.net/snapshots/net/essentialsx"
    updated = []
    failed = []
    progress = ProgressLine(len(artifacts))
    done = 0
    for artifact in artifacts:
        progress.update(done, artifact)
        try:
            m1 = urllib.request.urlopen(
                urllib.request.Request(
                    f"{base_url}/{artifact}/maven-metadata.xml",
                    headers={"User-Agent": "azox-mc-start/1.0"},
                ),
                timeout=20,
            ).read().decode("utf-8")
            latest = parse_maven_latest(m1)
            if not latest:
                failed.append(f"{artifact}: no latest version")
                continue
            m2 = urllib.request.urlopen(
                urllib.request.Request(
                    f"{base_url}/{artifact}/{latest}/maven-metadata.xml",
                    headers={"User-Agent": "azox-mc-start/1.0"},
                ),
                timeout=20,
            ).read().decode("utf-8")
            ts, bn = parse_maven_timestamp_build(m2)
            if not ts or not bn:
                failed.append(f"{artifact}: no timestamp/build number")
                continue

            base_ver = latest.replace("-SNAPSHOT", "")
            maven_name = f"{artifact}-{base_ver}-{ts}-{bn}.jar"
            clean_name = f"{artifact}-{base_ver}-{bn}.jar"
            target = plugins_dir / clean_name
            if target.exists():
                continue

            url = f"{base_url}/{artifact}/{latest}/{maven_name}"
            download_file(url, target, timeout=60)
            for old in plugins_dir.glob(f"{artifact}-*.jar"):
                if old.name != clean_name:
                    old.unlink(missing_ok=True)
            updated.append(clean_name)
        except Exception as ex:
            failed.append(f"{artifact}: {ex}")
        finally:
            done += 1
            progress.update(done, artifact)
    progress.finish()
    return updated, failed


def load_modrinth_manifest():
    if not MODRINTH_MANIFEST.exists():
        return []
    try:
        data = json.loads(MODRINTH_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("plugins", [])
    return []


def load_modrinth_sources():
    sources = []
    seen = set()

    for entry in load_modrinth_manifest():
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug") or entry.get("id")
        key = f"slug:{slug.lower()}" if slug else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        sources.append(entry)

    for token in read_csv_or_newline_tokens(FETCH_LIST_FILE):
        key = f"query:{token.lower()}"
        if key in seen:
            continue
        seen.add(key)
        sources.append({"query": token})

    return sources


def resolve_modrinth_project(entry):
    if entry.get("slug") or entry.get("id"):
        slug = entry.get("slug") or entry.get("id")
        return {
            "slug": slug,
            "cleanup_prefix": entry.get("cleanup_prefix"),
            "filename": entry.get("filename"),
            "aliases": [slug],
        }, None

    query = (entry.get("query") or "").strip()
    if not query:
        return None, "empty query"

    params = urllib.parse.urlencode(
        {
            "query": query,
            "limit": 10,
            "index": "relevance",
        }
    )
    data = http_json(f"https://api.modrinth.com/v2/search?{params}", timeout=30)
    hits = data.get("hits", []) if isinstance(data, dict) else []
    # Modrinth server plugins are often tagged as "mod" projects with paper/spigot loaders.
    server_hits = []
    for h in hits:
        ptype = str(h.get("project_type", "")).lower()
        if ptype not in ("plugin", "mod"):
            continue
        cats = [str(c).lower() for c in h.get("categories", [])]
        dloaders = [str(c).lower() for c in h.get("display_categories", [])]
        merged = set(cats + dloaders)
        if merged.intersection({"paper", "purpur", "spigot", "bukkit", "folia"}):
            server_hits.append(h)
            continue
        # Keep as fallback if no explicit loader category is present.
        if ptype == "plugin":
            server_hits.append(h)

    if not server_hits:
        return None, f"{query}: no plugin results"

    qn = query.lower()
    selected = None
    for h in server_hits:
        slug = str(h.get("slug", "")).lower()
        title = str(h.get("title", "")).lower()
        if qn == slug or qn == title:
            selected = h
            break
    if not selected:
        selected = server_hits[0]

    slug = selected.get("slug")
    title = selected.get("title")
    aliases = [query, slug]
    if title:
        aliases.append(title)
    return {
        "slug": slug,
        "cleanup_prefix": entry.get("cleanup_prefix"),
        "filename": entry.get("filename"),
        "aliases": aliases,
    }, None


def pick_latest_version(versions):
    if not versions:
        return None
    return sorted(
        versions,
        key=lambda x: x.get("date_published", ""),
        reverse=True,
    )[0]


def print_progress(done, total):
    if total <= 0:
        return
    width = 22
    fill = int((done / total) * width)
    pct = int((done / total) * 100)
    bar = "=" * fill + " " * (width - fill)
    print(f"{done}/{total} [{bar}] {pct}%")


def resolve_filename(entry, version_obj):
    explicit = entry.get("filename")
    if explicit:
        return explicit
    files = version_obj.get("files", [])
    primary = None
    for f in files:
        if f.get("primary"):
            primary = f
            break
    if primary:
        return primary.get("filename")
    return files[0].get("filename") if files else None


def update_modrinth_plugins(plugins_dir: Path, mc_version: str):
    plugins = load_modrinth_sources()
    if not plugins:
        warn("No Modrinth source list configured; skipping.")
        return [], [], []

    resolved = []
    failed = []
    seen_slugs = set()
    keep_keys = set()
    for raw in plugins:
        try:
            entry, failure = resolve_modrinth_project(raw)
            if failure:
                failed.append(failure)
                continue
            slug = str(entry.get("slug", "")).strip()
            if not slug:
                failed.append("resolved project had empty slug")
                continue
            low = slug.lower()
            if low in seen_slugs:
                continue
            seen_slugs.add(low)
            resolved.append(entry)
            for alias in entry.get("aliases", []):
                nk = normalize_key(alias)
                if nk:
                    keep_keys.add(nk)
        except Exception as ex:
            failed.append(f"resolve error: {ex}")

    updated = []
    managed_names = []
    total = len(resolved)
    done = 0
    progress = ProgressLine(total if total > 0 else 1)
    if total == 0:
        progress.update(1, "No resolved Modrinth projects")
        progress.finish()
        return updated, failed, managed_names, keep_keys

    for entry in resolved:
        slug = entry.get("slug")
        progress.update(done, slug)
        try:
            # 1) Purpur exact MC version 2) Paper exact MC version
            # 3) Purpur any version 4) Paper any version 5) Spigot/Bukkit/Folia exact 6) same any
            query_attempts = [
                {"loaders": ["purpur"], "game_versions": [mc_version]},
                {"loaders": ["paper"], "game_versions": [mc_version]},
                {"loaders": ["purpur"]},
                {"loaders": ["paper"]},
                {"loaders": ["spigot", "bukkit", "folia"], "game_versions": [mc_version]},
                {"loaders": ["spigot", "bukkit", "folia"]},
            ]
            versions = []
            for attempt in query_attempts:
                params = {"loaders": json.dumps(attempt["loaders"])}
                if "game_versions" in attempt:
                    params["game_versions"] = json.dumps(attempt["game_versions"])
                q = urllib.parse.urlencode(params)
                url = f"https://api.modrinth.com/v2/project/{slug}/version?{q}"
                versions = http_json(url, timeout=30)
                if versions:
                    break
            if not versions:
                failed.append(f"{slug}: no matching version for {mc_version}")
                continue
            latest = pick_latest_version(versions)
            file_name = resolve_filename(entry, latest)
            if not file_name:
                failed.append(f"{slug}: no downloadable file")
                continue
            managed_names.append(file_name)
            target = plugins_dir / file_name
            if target.exists():
                continue

            files = latest.get("files", [])
            download_url = None
            for f in files:
                if f.get("filename") == file_name:
                    download_url = f.get("url")
                    break
            if not download_url and files:
                download_url = files[0].get("url")
                file_name = files[0].get("filename")
                target = plugins_dir / file_name
                managed_names[-1] = file_name
            if not download_url:
                failed.append(f"{slug}: no file URL")
                continue

            download_file(download_url, target, timeout=60)
            # Remove stale jars from same slug if user sets cleanup_prefix.
            prefix = entry.get("cleanup_prefix")
            if prefix:
                for old in plugins_dir.glob(f"{prefix}*.jar"):
                    if old.name != file_name:
                        old.unlink(missing_ok=True)
            updated.append(file_name)
        except urllib.error.HTTPError as ex:
            failed.append(f"{slug}: HTTP {ex.code}")
        except Exception as ex:
            failed.append(f"{slug}: {ex}")
        finally:
            done += 1
            progress.update(done, slug)
    progress.finish()

    return updated, failed, managed_names, keep_keys


def allowlist_tokens():
    return read_csv_or_newline_tokens(ALLOWLIST_FILE)


def plugin_matches_allowlist(jar_name: str, patterns):
    if not patterns:
        return False
    jar_lower = jar_name.lower()
    stem_lower = Path(jar_name).stem.lower()
    stem_norm = normalize_key(stem_lower)
    for pat in patterns:
        candidate = pat.strip()
        if not candidate:
            continue
        low = candidate.lower()
        norm = normalize_key(low.removesuffix(".jar"))
        if any(ch in low for ch in "*?[]"):
            if fnmatch.fnmatch(jar_lower, low) or fnmatch.fnmatch(stem_lower, low):
                return True
            continue
        if stem_lower == low or jar_lower == low:
            return True
        if jar_lower == f"{low}.jar":
            return True
        if stem_lower.startswith(f"{low}-"):
            return True
        if norm and (stem_norm == norm or stem_norm.startswith(norm)):
            return True
    return False


def plugin_matches_essentials_list(jar_name: str, artifacts):
    low = jar_name.lower()
    for artifact in artifacts:
        prefix = f"{artifact.lower()}-"
        if low.startswith(prefix):
            return True
    return False


def plugin_matches_modrinth_keys(jar_name: str, keep_keys):
    if not keep_keys:
        return False
    stem_norm = normalize_key(Path(jar_name).stem)
    if not stem_norm:
        return False
    for key in keep_keys:
        if stem_norm == key or stem_norm.startswith(key):
            return True
    return False


def check_unlisted_plugins(
    plugins_dir: Path,
    managed_plugin_names,
    essentials_artifacts,
    modrinth_keep_keys,
    retention_days: int,
    purge_old_enabled: bool,
):
    ensure_dir(plugins_dir)
    removed_dir = plugins_dir / "removed-plugins"
    ensure_dir(removed_dir)

    allow_patterns = allowlist_tokens()

    expected = set(managed_plugin_names)
    removed = []

    for jar in plugins_dir.glob("*.jar"):
        name = jar.name
        if name in expected:
            continue
        if plugin_matches_allowlist(name, allow_patterns):
            continue
        if plugin_matches_essentials_list(name, essentials_artifacts):
            continue
        if plugin_matches_modrinth_keys(name, modrinth_keep_keys):
            continue
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = removed_dir / f"{stamp}-{name}"
        jar.rename(dst)
        removed.append(name)

    purged = purge_old_files(removed_dir, retention_days) if purge_old_enabled else 0
    return removed, purged


def clear_session_locks(server_dir: Path):
    locks = list(server_dir.glob("**/session.lock"))
    if not locks:
        log("No session lock found")
        return []
    log("Found session lock")
    removed = []
    for lock in locks:
        try:
            rel = lock.relative_to(server_dir)
        except ValueError:
            rel = lock
        lock.unlink(missing_ok=True)
        removed.append(f"./{rel}")
    return removed


def start_boot_proxy(port, motd, timeout):
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT_DIR / "boot_proxy.py"),
            "--port",
            str(port),
            "--motd",
            motd,
            "--timeout",
            str(timeout),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def stop_boot_proxy(proc):
    if not proc:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def update_purpur_if_needed(server_dir: Path, jar_name: str, enabled=True):
    version, build = parse_purpur_jar_name(jar_name)
    if not enabled:
        return jar_name, False, "Purpur update disabled"
    if not version:
        return jar_name, False, f"Could not parse Purpur version from {jar_name}"
    try:
        info = http_json(f"https://api.purpurmc.org/v2/purpur/{version}")
        latest = int(info.get("builds", {}).get("latest"))
        configured_path = server_dir / jar_name
        if latest <= build and configured_path.exists():
            return jar_name, False, "Already on latest build"
        new_name = f"purpur-{version}-{latest}.jar"
        dst = server_dir / new_name
        if dst.exists():
            return new_name, True, f"Using existing latest Purpur jar {new_name}"
        url = f"https://api.purpurmc.org/v2/purpur/{version}/{latest}/download"
        download_file(url, dst, timeout=120)
        return new_name, True, f"Updated Purpur to {new_name}"
    except Exception as ex:
        return jar_name, False, f"Purpur update failed: {ex}"


def start_server_loop(server_dir: Path, jar_name: str, ram_min: str, ram_max: str, auto_restart: bool):
    jar = server_dir / jar_name
    if not jar.exists():
        raise FileNotFoundError(f"Jar not found: {jar}")
    while True:
        cmd = [
            "java",
            f"-Xms{ram_min}",
            f"-Xmx{ram_max}",
            "-jar",
            jar.name,
            "--nogui",
        ]
        proc = subprocess.Popen(cmd, cwd=str(server_dir))
        code = proc.wait()
        if code == 0:
            log("Minecraft exited cleanly.")
            if auto_restart:
                warn("AUTO_RESTART=true, restarting in 3s...")
                time.sleep(3)
                continue
            break
        err(f"Minecraft exited with code {code}.")
        if auto_restart:
            warn("AUTO_RESTART=true, restarting in 5s...")
            time.sleep(5)
            continue
        break


def main():
    parser = argparse.ArgumentParser(description="Azox Minecraft startup orchestrator")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--no-run", action="store_true", help="Run checks only, do not launch Java server.")
    args = parser.parse_args()

    config_path = Path(args.config)
    cfg = parse_env(config_path)
    server_dir = Path(cfg.get("SERVER_DIR", "/home/ximotu/azox-scripts/sv")).expanduser()
    jar_name = cfg.get("JAR_FILE", "purpur-1.21.11-2564.jar")
    port = int_env(cfg, "PORT", 25565)
    ram_min = cfg.get("RAM_MIN", "512M")
    ram_max = cfg.get("RAM_MAX", "4G")
    auto_restart = bool_env(cfg, "AUTO_RESTART", True)
    retention_days = int_env(cfg, "DELETED_MOD_RETENTION_DAYS", 7)
    enable_eula = bool_env(cfg, "ENABLE_EULA", bool_env(cfg, "CHECK_EULA", True))
    update_purpur = bool_env(cfg, "UPDATE_PURPUR", True)
    update_essentials = bool_env(cfg, "UPDATE_ESSENTIALSX", True)
    update_modrinth = bool_env(cfg, "UPDATE_MODRINTH", True)
    remove_locks = bool_env(cfg, "REMOVE_LOCKS", True)
    purge_old_enabled = bool_env(cfg, "PURGE_OLD_FILES", True)
    no_run = args.no_run or bool_env(cfg, "NO_RUN", False)
    boot_motd = cfg.get("BOOT_MOTD", "Server is starting, please wait...")
    boot_timeout = int_env(cfg, "BOOT_MOTD_TIMEOUT", 300)

    banner()

    if not server_dir.exists():
        err(f"SERVER_DIR does not exist: {server_dir}")
        return 2
    plugins_dir = server_dir / "plugins"
    ensure_dir(plugins_dir)

    divider()
    log("Checking Server Properties")
    changed = update_server_properties(server_dir, port)
    if changed:
        log(f"Port updated to {port}")
    else:
        log(f"Port OK ({port})")

    divider()
    log("Checking Port Available")
    free_state, free_reason = port_is_free(port)
    if free_state is False:
        err(f"Other instance of Minecraft (or another process) is already running on port {port}. Aborting.")
        return 3
    if free_state is None:
        warn(f"Could not verify port availability in this environment ({free_reason}); continuing.")
    log("Port Available")

    divider()
    boot_proc = start_boot_proxy(port, boot_motd, boot_timeout)
    log(f"Boot proxy started on port {port}")

    if update_purpur:
        divider()
        log("Checking Purpur for updates")
        jar_name, purpur_updated, purpur_msg = update_purpur_if_needed(server_dir, jar_name, enabled=update_purpur)
        if purpur_updated:
            log(purpur_msg)
            update_env_value(config_path, "JAR_FILE", jar_name, quote=True)
            remove_env_keys(config_path, ["JAR_NAME"])
        else:
            warn(purpur_msg)

    managed_names = []
    modrinth_keep_keys = set()
    if update_modrinth:
        divider()
        log("Updating Modrinth Plugins")
        mc_version, _build = parse_purpur_jar_name(jar_name)
        if not mc_version:
            mc_version = "1.21.11"
        mod_updated, mod_failed, managed_names, modrinth_keep_keys = update_modrinth_plugins(plugins_dir, mc_version)
        if mod_updated:
            log(f"Updated {len(mod_updated)} plugin(s): {', '.join(mod_updated)}")
        else:
            log("Updated: All Plugins up to date. No updates required.")
        if mod_failed:
            warn(f"Modrinth warnings ({len(mod_failed)}): " + "; ".join(mod_failed))

    essentials_artifacts = read_essentials_artifacts()
    if update_essentials:
        divider()
        log("Checking EssentialsX for updates")
        ess_updated, ess_failed = update_essentialsx(plugins_dir)
        if ess_updated:
            for item in ess_updated:
                log(f"Updated: {item}")
        else:
            log("EssentialsX: All configured artifacts are up to date.")
        if ess_failed:
            warn(f"EssentialsX warnings ({len(ess_failed)}): " + "; ".join(ess_failed))

    if enable_eula:
        divider()
        log("Checking EULA")
        if check_eula(server_dir, enforce=True):
            log("EULA OK")
            update_env_value(config_path, "ENABLE_EULA", "true", quote=False)

    divider()
    log("Checking for unlisted plugins")
    removed, purged = check_unlisted_plugins(
        plugins_dir=plugins_dir,
        managed_plugin_names=managed_names,
        essentials_artifacts=essentials_artifacts,
        modrinth_keep_keys=modrinth_keep_keys,
        retention_days=retention_days,
        purge_old_enabled=purge_old_enabled,
    )
    if removed:
        for item in removed:
            warn(f"Removed Plugin: {item} (moved to removed-plugins)")
    else:
        log("No unlisted plugins found.")
    if purge_old_enabled:
        log(f"Purged {purged} deleted plugin(s) older than {retention_days} days")

    if remove_locks:
        divider()
        log("Checking for session lock")
        removed_locks = clear_session_locks(server_dir)
        if removed_locks:
            log("Removing session locks:")
            for item in removed_locks:
                print(f"    Removed: {item}")

    divider()
    stop_boot_proxy(boot_proc)
    log("Boot proxy stopped. Starting Minecraft server...")

    if no_run:
        warn("--no-run enabled; launch skipped.")
        return 0

    def handle_sig(signum, _frame):
        warn(f"Received signal {signum}, shutting down.")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)

    try:
        start_server_loop(server_dir, jar_name, ram_min, ram_max, auto_restart)
    except FileNotFoundError as ex:
        err(str(ex))
        return 4
    except KeyboardInterrupt:
        warn("Launcher interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
