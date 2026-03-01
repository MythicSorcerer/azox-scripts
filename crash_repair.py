#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import urllib.request
from pathlib import Path


class CrashTypes:
    EULA_NOT_SET = "EULA_NOT_SET"
    WRONG_JAVA = "WRONG_JAVA"
    BAD_JVM_FLAGS = "BAD_JVM_FLAGS"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    PLUGIN_WRONG_VERSION = "PLUGIN_WRONG_VERSION"
    PLUGIN_CONFLICT = "PLUGIN_CONFLICT"
    CONFIG_PARSE_ERROR = "CONFIG_PARSE_ERROR"
    OOM = "OOM"
    DISK_FULL = "DISK_FULL"
    PORT_CONFLICT = "PORT_CONFLICT"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    POSSIBLE_CACHE_CORRUPTION = "POSSIBLE_CACHE_CORRUPTION"
    WORLD_CORRUPT = "WORLD_CORRUPT"
    DATAPACK_ERROR = "DATAPACK_ERROR"
    MODLOADER_MISMATCH = "MODLOADER_MISMATCH"
    NATIVE_LIB_ERROR = "NATIVE_LIB_ERROR"
    UNKNOWN = "UNKNOWN"
    GIVE_UP = "GIVE_UP"


class CrashRepairEngine:
    def __init__(
        self,
        server_dir: Path,
        script_dir: Path,
        config_path: Path,
        jar_name: str,
        port: int,
        ram_min: str,
        ram_max: str,
        java_bin: str,
        extra_jvm_flags: str,
        max_crashes: int,
        log,
        warn,
        err,
    ):
        self.server_dir = server_dir
        self.script_dir = script_dir
        self.config_path = config_path
        self.jar_name = jar_name
        self.port = int(port)
        self.ram_min = ram_min
        self.ram_max = ram_max
        self.java_bin = java_bin
        self.extra_jvm_flags = extra_jvm_flags
        self.max_crashes = max_crashes
        self.log = log
        self.warn = warn
        self.err = err

        self.modrinth_list = script_dir / "modrinth_list.txt"
        self.removed_list = script_dir / "removed_list.txt"
        self.state_path = server_dir / ".crash_repair_state.json"
        self.plugin_dir = server_dir / "plugins"
        self.removed_plugins_dir = self.plugin_dir / "removed-plugins"
        self.quarantine_dir = self.plugin_dir / "quarantine"

    def load_state(self):
        if not self.state_path.exists():
            return {
                "crash_counter": 0,
                "signature_counts": {},
                "learning": {},
                "unknown_phase": 0,
            }
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "crash_counter": 0,
                "signature_counts": {},
                "learning": {},
                "unknown_phase": 0,
            }

    def save_state(self, state):
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def reset_crash_state(self):
        state = self.load_state()
        state["crash_counter"] = 0
        state["signature_counts"] = {}
        state["unknown_phase"] = 0
        self.save_state(state)

    def parse_env(self):
        cfg = {}
        if not self.config_path.exists():
            return cfg
        for raw in self.config_path.read_text(encoding="utf-8").splitlines():
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

    def update_env_value(self, key, value, quote=False):
        newline = f'{key}="{value}"' if quote else f"{key}={value}"
        if not self.config_path.exists():
            self.config_path.write_text(newline + "\n", encoding="utf-8")
            return
        out = []
        found = False
        for line in self.config_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                out.append(newline)
                found = True
            else:
                out.append(line)
        if not found:
            out.append(newline)
        self.config_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    def append_removed_log(self, action, value):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.removed_list.parent.mkdir(parents=True, exist_ok=True)
        with self.removed_list.open("a", encoding="utf-8") as f:
            f.write(f"{stamp},{action},{value}\n")

    def parse_purpur_version(self):
        m = re.match(r"^purpur-(\d+\.\d+\.\d+)-(\d+)\.jar$", self.jar_name)
        if not m:
            return None, None
        return m.group(1), int(m.group(2))

    def run_once(self):
        jar = self.server_dir / self.jar_name
        if not jar.exists():
            raise FileNotFoundError(f"Jar not found: {jar}")

        cmd = [
            self.java_bin,
            f"-Xms{self.ram_min}",
            f"-Xmx{self.ram_max}",
        ]
        if self.extra_jvm_flags:
            cmd.extend(self.extra_jvm_flags.split())
        cmd.extend(["-jar", jar.name, "--nogui"])

        force_upgrade_flag = self.server_dir / ".force_upgrade_once"
        if force_upgrade_flag.exists():
            cmd.append("--forceUpgrade")
            force_upgrade_flag.unlink(missing_ok=True)

        proc = subprocess.Popen(
            cmd,
            cwd=str(self.server_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        lines = []
        started_successfully = False
        if proc.stdout:
            for line in proc.stdout:
                print(line, end="")
                lines.append(line.rstrip("\n"))
                lower = line.lower()
                if " done (" in lower and "for help, type" in lower:
                    started_successfully = True
        code = proc.wait()
        return {
            "exit_code": code,
            "started": started_successfully,
            "log_text": "\n".join(lines),
            "lines": lines,
        }

    def build_signature(self, lines):
        clean = [ln for ln in lines if ln.strip()]
        joined = "\n".join(clean[:25])
        return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()

    def classify(self, log_text, same_sig_count):
        weighted_rules = [
            (CrashTypes.EULA_NOT_SET, r"agree to the eula", 10),
            (CrashTypes.WRONG_JAVA, r"unsupportedclassversionerror|class file version", 9),
            (CrashTypes.BAD_JVM_FLAGS, r"could not create the java virtual machine", 9),
            (CrashTypes.MISSING_DEPENDENCY, r"could not load plugin.*depend", 8),
            (CrashTypes.PLUGIN_WRONG_VERSION, r"not compatible with this server version", 8),
            (CrashTypes.PLUGIN_CONFLICT, r"conflicts with", 8),
            (CrashTypes.CONFIG_PARSE_ERROR, r"invalidconfigurationexception|while parsing yaml", 8),
            (CrashTypes.OOM, r"outofmemoryerror", 9),
            (CrashTypes.DISK_FULL, r"no space left on device", 9),
            (CrashTypes.PORT_CONFLICT, r"address already in use", 9),
            (CrashTypes.PERMISSION_ERROR, r"permission denied", 9),
            (CrashTypes.WORLD_CORRUPT, r"failed to load chunk|chunk corrupted|palette", 8),
            (CrashTypes.DATAPACK_ERROR, r"failed to load datapacks", 8),
            (CrashTypes.MODLOADER_MISMATCH, r"fabric\.mod\.json|quilt\.mod\.json|neoforge", 7),
            (CrashTypes.NATIVE_LIB_ERROR, r"unsatisfiedlinkerror|lwjgl", 8),
        ]
        scores = {}
        lower = log_text.lower()
        for ctype, patt, weight in weighted_rules:
            if re.search(patt, lower):
                scores[ctype] = scores.get(ctype, 0) + weight

        if same_sig_count >= 2 and not scores:
            return CrashTypes.POSSIBLE_CACHE_CORRUPTION
        if not scores:
            return CrashTypes.UNKNOWN
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[0][0]

    def apply_repair(self, crash_type, log_text, state):
        if crash_type == CrashTypes.EULA_NOT_SET:
            eula = self.server_dir / "eula.txt"
            eula.unlink(missing_ok=True)
            eula.write_text("eula=true\n", encoding="utf-8")
            self.update_env_value("ENABLE_EULA", "true")
            return True, "EULA accepted"

        if crash_type == CrashTypes.WRONG_JAVA:
            return self.fix_wrong_java(log_text)

        if crash_type == CrashTypes.BAD_JVM_FLAGS:
            self.update_env_value("EXTRA_JVM_FLAGS", "", quote=True)
            self.extra_jvm_flags = ""
            return True, "Reset EXTRA_JVM_FLAGS"

        if crash_type == CrashTypes.MISSING_DEPENDENCY:
            dep = self.extract_dependency_name(log_text)
            if dep:
                self.append_unique_token(self.modrinth_list, dep)
                return True, f"Added missing dependency to modrinth_list: {dep}"
            return False, "Could not extract dependency name"

        if crash_type == CrashTypes.PLUGIN_WRONG_VERSION:
            plugin = self.extract_plugin_name(log_text)
            if plugin:
                self.move_plugin_jar(plugin)
                self.append_removed_log("incompatible_plugin_removed", plugin)
                return True, f"Removed incompatible plugin {plugin}"
            return False, "Could not identify incompatible plugin"

        if crash_type == CrashTypes.PLUGIN_CONFLICT:
            a, b = self.extract_plugin_conflict(log_text)
            removed = self.remove_later_plugin_from_list(a, b)
            if removed:
                return True, f"Removed conflicting plugin from modrinth_list: {removed}"
            return False, "Could not resolve plugin conflict entries"

        if crash_type == CrashTypes.CONFIG_PARSE_ERROR:
            path = self.extract_config_path(log_text)
            if path:
                target = (self.server_dir / path).resolve()
                if target.exists():
                    broken = target.with_suffix(target.suffix + ".broken")
                    target.rename(broken)
                    return True, f"Renamed broken config {path}"
            return False, "Could not isolate broken config path"

        if crash_type == CrashTypes.OOM:
            return self.fix_oom()

        if crash_type == CrashTypes.DISK_FULL:
            self.cleanup_disk()
            return True, "Cleaned old logs/backups for disk space"

        if crash_type == CrashTypes.PORT_CONFLICT:
            return self.kill_port_process()

        if crash_type == CrashTypes.PERMISSION_ERROR:
            subprocess.run(["chmod", "-R", "u+rwX", str(self.server_dir)], check=False)
            return True, "Applied chmod -R u+rwX"

        if crash_type == CrashTypes.POSSIBLE_CACHE_CORRUPTION:
            self.purge_caches()
            return True, "Purged libraries/cache/session locks"

        if crash_type == CrashTypes.WORLD_CORRUPT:
            self.handle_world_corruption()
            return True, "Applied world corruption recovery strategy"

        if crash_type == CrashTypes.DATAPACK_ERROR:
            self.disable_datapacks()
            return True, "Moved datapacks to datapacks.disabled"

        if crash_type == CrashTypes.MODLOADER_MISMATCH:
            self.quarantine_modloader_files()
            return True, "Moved incompatible modloader files to quarantine"

        if crash_type == CrashTypes.NATIVE_LIB_ERROR:
            self.purge_caches()
            self.redownload_purpur()
            return True, "Purged native libs and refreshed Purpur jar"

        if crash_type == CrashTypes.UNKNOWN:
            return self.unknown_fallback(state)

        return False, "No repair strategy"

    def process_crash(self, crash_data):
        state = self.load_state()
        state["crash_counter"] = int(state.get("crash_counter", 0)) + 1
        if state["crash_counter"] > self.max_crashes:
            self.save_state(state)
            return False, CrashTypes.GIVE_UP, "Crash limit exceeded"

        sig = self.build_signature(crash_data["lines"])
        sig_counts = state.setdefault("signature_counts", {})
        sig_counts[sig] = int(sig_counts.get(sig, 0)) + 1
        same_sig_count = sig_counts[sig]

        learning = state.setdefault("learning", {})
        crash_type = self.classify(crash_data["log_text"], same_sig_count)
        if sig in learning:
            crash_type = learning[sig]

        if same_sig_count >= 3 and crash_type != CrashTypes.UNKNOWN:
            # escalate strategy for repeated same crash by forcing cache cleanup first
            self.purge_caches()

        ok, msg = self.apply_repair(crash_type, crash_data["log_text"], state)
        if ok:
            learning[sig] = crash_type
        self.save_state(state)
        return ok, crash_type, msg

    def append_unique_token(self, path: Path, token: str):
        token = token.strip()
        if not token:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if path.exists():
            existing = [x.strip() for x in path.read_text(encoding="utf-8").splitlines()]
        normalized = {x.lower() for x in existing if x and not x.startswith("#")}
        if token.lower() in normalized:
            return
        with path.open("a", encoding="utf-8") as f:
            f.write(token + "\n")

    def remove_token(self, path: Path, token: str):
        if not path.exists():
            return False
        token_low = token.strip().lower()
        out = []
        changed = False
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                out.append(raw)
                continue
            parts = [p.strip() for p in line.split(",")]
            kept = [p for p in parts if p.lower() != token_low]
            if len(kept) != len(parts):
                changed = True
            if kept:
                out.append(", ".join(kept))
        if changed:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return changed

    def extract_dependency_name(self, text):
        m = re.search(r"depend(?:ency)?[:=]\s*([A-Za-z0-9_+.\-]+)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"missing dependency\s+([A-Za-z0-9_+.\-]+)", text, flags=re.IGNORECASE)
        return m.group(1) if m else None

    def extract_plugin_name(self, text):
        patterns = [
            r"Could not load plugin ['\"]?([^'\"\s]+)['\"]?",
            r"\[([A-Za-z0-9_+.\-]+)\].*not compatible with this server version",
            r"Plugin\s+([A-Za-z0-9_+.\-]+)\s+is not compatible",
        ]
        for patt in patterns:
            m = re.search(patt, text, flags=re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    def move_plugin_jar(self, plugin_name):
        self.removed_plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_low = plugin_name.lower()
        for jar in self.plugin_dir.glob("*.jar"):
            if jar.stem.lower().startswith(plugin_low):
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                jar.rename(self.removed_plugins_dir / f"{stamp}-{jar.name}")
                return True
        return False

    def extract_plugin_conflict(self, text):
        m = re.search(
            r"([A-Za-z0-9_+.\-]+)\s+.*conflicts with\s+([A-Za-z0-9_+.\-]+)",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return None, None
        return m.group(1), m.group(2)

    def remove_later_plugin_from_list(self, a, b):
        if not a or not b or not self.modrinth_list.exists():
            return None
        tokens = []
        for raw in self.modrinth_list.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens.extend([p.strip() for p in line.split(",") if p.strip()])
        low = [t.lower() for t in tokens]
        a_low = a.lower()
        b_low = b.lower()
        if a_low not in low and b_low not in low:
            return None
        if a_low in low and b_low in low:
            remove_target = b if low.index(b_low) > low.index(a_low) else a
        elif a_low in low:
            remove_target = a
        else:
            remove_target = b
        if self.remove_token(self.modrinth_list, remove_target):
            return remove_target
        return None

    def extract_config_path(self, text):
        m = re.search(r"([^\s:]+\.ya?ml)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).lstrip("./")
        return None

    def parse_mem(self, v):
        m = re.match(r"^\s*(\d+)\s*([mMgG])\s*$", str(v))
        if not m:
            return None
        n = int(m.group(1))
        u = m.group(2).upper()
        return n * 1024 if u == "G" else n

    def format_mem(self, mb):
        if mb % 1024 == 0:
            return f"{mb // 1024}G"
        return f"{mb}M"

    def free_mem_mb(self):
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            data = meminfo.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"MemAvailable:\s+(\d+)\s+kB", data)
            if m:
                return int(m.group(1)) // 1024
        return None

    def reduce_view_distance(self):
        path = self.server_dir / "server.properties"
        if not path.exists():
            return False
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        changed = False
        found = False
        for line in lines:
            if line.startswith("view-distance="):
                found = True
                current = int(re.sub(r"[^\d]", "", line.split("=", 1)[1]) or "10")
                new = max(2, current - 2)
                if new != current:
                    changed = True
                out.append(f"view-distance={new}")
            else:
                out.append(line)
        if not found:
            out.append("view-distance=8")
            changed = True
        if changed:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
        return changed

    def disable_heaviest_plugin(self):
        jars = sorted(self.plugin_dir.glob("*.jar"), key=lambda p: p.stat().st_size, reverse=True)
        if not jars:
            return None
        self.removed_plugins_dir.mkdir(parents=True, exist_ok=True)
        jar = jars[0]
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        jar.rename(self.removed_plugins_dir / f"{stamp}-{jar.name}")
        self.append_removed_log("oom_disable_heaviest", jar.name)
        return jar.name

    def fix_oom(self):
        current_mb = self.parse_mem(self.ram_max)
        if current_mb is None:
            return False, "Could not parse RAM_MAX"
        free_mb = self.free_mem_mb()
        if free_mb is not None and free_mb >= 512:
            new_mb = current_mb + 512
            new_val = self.format_mem(new_mb)
            self.ram_max = new_val
            self.update_env_value("RAM_MAX", new_val, quote=True)
            return True, f"Increased RAM_MAX to {new_val}"
        self.reduce_view_distance()
        disabled = self.disable_heaviest_plugin()
        if disabled:
            return True, f"Reduced view-distance and disabled heaviest plugin {disabled}"
        return True, "Reduced view-distance due to OOM"

    def cleanup_disk(self):
        now = dt.datetime.now().timestamp()
        logs_dir = self.server_dir / "logs"
        backups_dir = self.server_dir / "backups"
        for p in logs_dir.glob("*"):
            try:
                if p.is_file() and now - p.stat().st_mtime > 7 * 86400:
                    p.unlink()
            except Exception:
                continue
        if backups_dir.exists():
            backups = sorted([p for p in backups_dir.iterdir() if p.is_file()], key=lambda p: p.stat().st_mtime)
            for old in backups[:-3]:
                old.unlink(missing_ok=True)

    def kill_port_process(self):
        try:
            out = subprocess.check_output(["lsof", "-ti", f"tcp:{self.port}"], text=True, stderr=subprocess.DEVNULL)
            pids = [x.strip() for x in out.splitlines() if x.strip().isdigit()]
            if not pids:
                return False, "No PID found for port conflict"
            for pid in pids:
                os.kill(int(pid), signal.SIGTERM)
            return True, f"Terminated process(es) on port {self.port}: {', '.join(pids)}"
        except Exception as ex:
            return False, f"Failed to terminate port process: {ex}"

    def purge_caches(self):
        for rel in ["libraries", "cache"]:
            path = self.server_dir / rel
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        for lock in self.server_dir.glob("**/session.lock"):
            lock.unlink(missing_ok=True)

    def handle_world_corruption(self):
        backup_root = self.server_dir / "backups" / "world-corrupt"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        for world_name in ["world", "world_nether", "world_the_end"]:
            src = self.server_dir / world_name
            if src.exists() and src.is_dir():
                archive_base = backup_root / f"{world_name}-{stamp}"
                shutil.make_archive(str(archive_base), "zip", root_dir=str(src))
        (self.server_dir / ".force_upgrade_once").write_text("1\n", encoding="utf-8")

        # Region binary isolation step: move half region files out each attempt.
        for world_name in ["world", "world_nether", "world_the_end"]:
            region = self.server_dir / world_name / "region"
            quarantine = self.server_dir / world_name / "region.quarantine"
            if not region.exists():
                continue
            files = sorted(region.glob("*.mca"))
            if len(files) < 2:
                continue
            quarantine.mkdir(parents=True, exist_ok=True)
            half = len(files) // 2
            for p in files[:half]:
                p.rename(quarantine / p.name)

    def disable_datapacks(self):
        for datapacks in self.server_dir.glob("**/datapacks"):
            if datapacks.is_dir():
                dst = datapacks.with_name("datapacks.disabled")
                if dst.exists():
                    continue
                datapacks.rename(dst)

    def quarantine_modloader_files(self):
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        for p in self.plugin_dir.glob("*.jar"):
            low = p.name.lower()
            if any(x in low for x in ["fabric", "quilt", "forge", "neoforge"]):
                p.rename(self.quarantine_dir / p.name)
                self.append_removed_log("modloader_quarantine", p.name)

    def redownload_purpur(self):
        version, _build = self.parse_purpur_version()
        if not version:
            return False
        info = self.http_json(f"https://api.purpurmc.org/v2/purpur/{version}")
        latest = int(info.get("builds", {}).get("latest"))
        new_name = f"purpur-{version}-{latest}.jar"
        dst = self.server_dir / new_name
        if not dst.exists():
            url = f"https://api.purpurmc.org/v2/purpur/{version}/{latest}/download"
            self.download_file(url, dst)
        self.jar_name = new_name
        self.update_env_value("JAR_FILE", new_name, quote=True)
        return True

    def http_json(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "azox-mc-start/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def download_file(self, url, dst: Path):
        req = urllib.request.Request(url, headers={"User-Agent": "azox-mc-start/1.0"})
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as w:
            shutil.copyfileobj(r, w)
        tmp.replace(dst)

    def fix_wrong_java(self, log_text):
        req_major = self.required_java_major(log_text)
        candidates = self.find_java_candidates()
        selected = None
        selected_major = -1
        for jbin in candidates:
            major = self.java_major(jbin)
            if major is None:
                continue
            if major >= req_major and major > selected_major:
                selected = jbin
                selected_major = major
        if not selected:
            return False, f"No Java binary found for required major {req_major}"
        self.java_bin = selected
        self.update_env_value("JAVA_BIN", selected, quote=True)
        return True, f"Switched JAVA_BIN to {selected} (Java {selected_major})"

    def required_java_major(self, log_text):
        m = re.search(r"class file version\s+(\d+)", log_text, flags=re.IGNORECASE)
        if m:
            cf = int(m.group(1))
            mapping = {52: 8, 55: 11, 61: 17, 65: 21, 67: 23}
            return mapping.get(cf, 21)
        mc_version, _ = self.parse_purpur_version()
        if not mc_version:
            return 21
        major_minor = ".".join(mc_version.split(".")[:2])
        if major_minor in ("1.20", "1.21"):
            return 21
        return 17

    def find_java_candidates(self):
        names = ["java", "java23", "java22", "java21", "java20", "java19", "java17"]
        found = []
        for name in names:
            path = shutil.which(name)
            if path and path not in found:
                found.append(path)
        return found

    def java_major(self, jbin):
        try:
            p = subprocess.run([jbin, "-version"], capture_output=True, text=True, check=False)
            text = (p.stdout or "") + "\n" + (p.stderr or "")
            m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
            if not m:
                return None
            if m.group(1) == "1":
                return int(m.group(2) or "8")
            return int(m.group(1))
        except Exception:
            return None

    def unknown_fallback(self, state):
        phase = int(state.get("unknown_phase", 0)) + 1
        state["unknown_phase"] = phase
        if phase == 1:
            self.purge_caches()
            return True, "Unknown crash fallback phase 1: purged caches"
        if phase == 2:
            self.disable_half_plugins()
            return True, "Unknown crash fallback phase 2: disabled half plugins"
        if phase == 3:
            isolated = self.binary_isolate_plugin_step()
            if isolated:
                self.remove_token(self.modrinth_list, Path(isolated).stem)
                return True, f"Unknown crash fallback phase 3: isolated plugin {isolated}"
            return True, "Unknown crash fallback phase 3: isolation step applied"
        if phase == 4:
            self.disable_all_plugins()
            return True, "Unknown crash fallback phase 4: disabled all plugins"
        if phase == 5:
            ok = self.redownload_purpur()
            return ok, "Unknown crash fallback phase 5: restored clean jar"
        return False, "Unknown crash fallback exhausted"

    def disable_half_plugins(self):
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        jars = sorted(self.plugin_dir.glob("*.jar"))
        if len(jars) < 2:
            return
        half = len(jars) // 2
        for p in jars[:half]:
            p.rename(self.quarantine_dir / p.name)
            self.append_removed_log("unknown_half_disable", p.name)

    def disable_all_plugins(self):
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        for p in self.plugin_dir.glob("*.jar"):
            p.rename(self.quarantine_dir / p.name)
            self.append_removed_log("unknown_disable_all", p.name)

    def binary_isolate_plugin_step(self):
        jars = sorted(self.plugin_dir.glob("*.jar"))
        if not jars:
            return None
        if len(jars) == 1:
            culprit = jars[0]
            self.removed_plugins_dir.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            culprit.rename(self.removed_plugins_dir / f"{stamp}-{culprit.name}")
            self.append_removed_log("unknown_binary_isolate", culprit.name)
            return culprit.name
        self.disable_half_plugins()
        return None
