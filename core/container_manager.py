"""
Container Manager - Handles Docker container lifecycle for skill execution.

Spins up a sandboxed container on demand when a skill is invoked, passes
the request via SKILL_INPUT, collects stdout as the result, and tears the
container down. All skills use the same execution model.

Designed for constrained environments (Raspberry Pi) where containers
should not persist between calls.
"""

import os
import re
import json
import time
import signal
import threading
import subprocess
import logging
import urllib.request
import urllib.parse
from datetime import date
from pathlib import Path

from core.mempalace_bridge import MemPalaceBridge

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_PORT = 7860
DASHBOARD_LOCK = Path.home() / ".miniclaw" / "dashboard.lock"


class ContainerManager:
    """Manages Docker containers for skill execution."""

    DEFAULT_TIMEOUT = 30
    DEFAULT_MEMORY_LIMIT = "256m"

    def __init__(self, memory_limit: str = DEFAULT_MEMORY_LIMIT):
        self.memory_limit = memory_limit
        self._meta_skill_executor = None  # injected from main.py after construction
        self._orchestrator = None          # injected from main.py after construction
        self.docker_available = False
        self.docker_error = None
        self._dashboard_timer: threading.Timer | None = None
        self._mpv_process: subprocess.Popen | None = None
        self._native_handlers = {
            "install_skill": self._execute_install_skill,
            "set_env_var": self._execute_set_env_var,
            "save_memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud_play": self._execute_soundcloud,
        }
        self._verify_docker()

    def _verify_docker(self):
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                self.docker_available = False
                stderr = result.stderr.decode(errors="replace").lower()
                if "permission denied" in stderr:
                    self.docker_error = "Docker is installed but this session cannot access the daemon"
                else:
                    self.docker_error = "Docker daemon is not running"
                logger.warning(self.docker_error)
                return
            self.docker_available = True
            self.docker_error = None
            logger.info("Docker is available")
        except FileNotFoundError:
            self.docker_available = False
            self.docker_error = "Docker is not installed"
            logger.warning(self.docker_error)
        except subprocess.TimeoutExpired:
            self.docker_available = False
            self.docker_error = "Docker daemon did not respond in time"
            logger.warning(self.docker_error)

    def execute_skill(self, skill, tool_input: dict) -> str:
        """
        Execute a skill by spinning up its container, passing input,
        and returning stdout as the result.

        Input is passed as JSON via the SKILL_INPUT environment variable.
        Output is read from stdout as plain text or JSON.
        """
        config = skill.execution_config

        # Native skills bypass Docker entirely
        if config.get("type") == "native":
            return self._execute_native_skill(skill, tool_input)

        if not self.docker_available:
            return f"Skill unavailable: {self.docker_error or 'Docker is unavailable'}"

        image = config.get("image", "")
        timeout = config.get("timeout_seconds", self.DEFAULT_TIMEOUT)

        if not image:
            return f"Error: no container image defined for skill '{skill.name}'"

        cmd = self._build_docker_cmd(
            image=image,
            env_vars=self._collect_env_vars(config.get("env_passthrough", [])),
            devices=config.get("devices", []),
            input_data=json.dumps(tool_input),
            memory=config.get("memory", self.memory_limit),
            read_only=config.get("read_only", True),
            extra_tmpfs=config.get("extra_tmpfs", []),
            volumes=config.get("volumes", []),
        )

        return self._run_container(cmd, tool_input, timeout)

    def _build_docker_cmd(
        self,
        image: str,
        env_vars: dict[str, str] | None = None,
        devices: list[str] | None = None,
        input_data: str = "",
        memory: str | None = None,
        read_only: bool = True,
        extra_tmpfs: list[str] | None = None,
        volumes: list[str] | None = None,
    ) -> list[str]:
        """Build a docker run command with security constraints.

        read_only and extra_tmpfs can be overridden per skill via config.yaml
        for skills that need a writable filesystem (e.g. browser automation).
        """
        cmd = [
            "docker", "run",
            "--rm",
            "-i",
            "--network=host",
            f"--memory={memory or self.memory_limit}",
            "--cpus=1.0",
            "--security-opt=no-new-privileges",
        ]

        if read_only:
            cmd.append("--read-only")

        cmd.extend(["--tmpfs=/tmp:size=64m"])
        for tmpfs in (extra_tmpfs or []):
            cmd.extend(["--tmpfs", tmpfs])

        for volume in (volumes or []):
            expanded = os.path.expandvars(os.path.expanduser(volume))
            cmd.extend(["-v", expanded])

        if env_vars:
            for key, value in env_vars.items():
                cmd.extend(["-e", f"{key}={value}"])

        if input_data:
            cmd.extend(["-e", f"SKILL_INPUT={input_data}"])

        if devices:
            for device in devices:
                cmd.extend(["--device", device])

        cmd.append(image)
        return cmd

    def _run_container(self, cmd: list[str], tool_input: dict, timeout: int) -> str:
        """Run the container and return its stdout output."""
        logger.info("Running container: %s", " ".join(cmd[-3:]))
        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                input=json.dumps(tool_input).encode(),
                capture_output=True,
                timeout=timeout,
            )

            elapsed = time.time() - start_time
            logger.info("Container finished in %.1fs (exit=%d)", elapsed, result.returncode)

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                logger.warning("Container error: %s", stderr[:500])
                return f"Skill execution error: {stderr[:500]}"

            output = result.stdout.decode(errors="replace").strip()
            return output if output else "Skill completed with no output"

        except subprocess.TimeoutExpired:
            logger.warning("Container timed out after %ds", timeout)
            return f"Skill timed out after {timeout} seconds"

        except Exception as e:
            logger.error("Container execution failed: %s", e)
            return f"Skill execution failed: {str(e)}"

    def _execute_native_skill(self, skill, tool_input: dict) -> str:
        """Route to a registered native (non-Docker) skill handler."""
        handler = self._native_handlers.get(skill.name)
        if handler is None:
            return f"No native handler registered for skill '{skill.name}'"
        return handler(tool_input)

    def _execute_install_skill(self, tool_input: dict) -> str:
        """Delegate voice-driven skill installation to the meta skill executor."""
        if self._meta_skill_executor is None:
            return "Meta skill executor not initialised — restart MiniClaw in voice mode."
        return self._meta_skill_executor.run(tool_input)

    def _execute_set_env_var(self, tool_input: dict) -> str:
        """Write a key=value pair to .env and reload skills."""
        key = str(tool_input.get("key", "")).strip()
        value = str(tool_input.get("value", "")).strip()

        if not key:
            return "Error: no key provided."

        if not re.match(r'^[A-Z][A-Z0-9_]*$', key):
            return f"Error: '{key}' is not a valid environment variable name."

        # Only allow keys that are actually needed by a skipped skill
        if self._orchestrator is not None:
            allowed = self._orchestrator.skill_loader.get_missing_env_vars()
            if key not in allowed:
                return (
                    f"Error: '{key}' is not required by any unavailable skill. "
                    f"Allowed keys: {', '.join(sorted(allowed)) or 'none'}."
                )

        # Write to .env
        env_path = REPO_ROOT / ".env"
        try:
            existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        except OSError as e:
            return f"Error reading .env: {e}"

        lines = existing.splitlines(keepends=True)
        new_line = f"{key}={value}\n"
        found = False
        for i, line in enumerate(lines):
            if re.match(rf'^{re.escape(key)}\s*=', line):
                lines[i] = new_line
                found = True
                break
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append(new_line)

        try:
            env_path.write_text("".join(lines), encoding="utf-8")
        except OSError as e:
            return f"Error writing .env: {e}"

        # Update the running process environment
        os.environ[key] = value

        # Reload skills so newly satisfied requirements take effect
        if self._orchestrator is None:
            logger.info("Set env var %s", key)
            return f"Set {key} successfully."

        self._orchestrator.reload_skills()
        logger.info("Set env var %s and reloaded skills", key)

        skipped = list(self._orchestrator.skill_loader.skipped_skills.keys())
        if skipped:
            return f"Set {key}. Skills still unavailable: {', '.join(skipped)}."
        return f"Set {key}. All skills are now available."

    def _execute_save_memory(self, tool_input: dict) -> str:
        """Write a memory note to the markdown vault and optionally MemPalace."""
        topic = str(tool_input.get("topic", "")).strip()
        content = str(tool_input.get("content", "")).strip()

        if not topic or not content:
            return "Error: both topic and content are required."

        vault_path = Path(os.environ.get("MEMORY_VAULT_PATH", Path.home() / ".miniclaw" / "memory"))
        try:
            vault_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Error creating memory vault: {e}"

        date_str = date.today().isoformat()
        slug = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
        filename = f"{date_str}_{slug}.md"

        # If a note with the same topic slug already exists, update it in place
        # rather than creating a duplicate with a new date prefix.
        existing = sorted(vault_path.glob(f"*_{slug}.md"))
        note_path = existing[-1] if existing else vault_path / filename

        note = f"---\ndate: {date_str}\ntopic: {topic}\n---\n\n{content}\n"
        try:
            note_path.write_text(note, encoding="utf-8")
        except OSError as e:
            return f"Error saving memory: {e}"

        mempalace_saved = self._save_memory_to_mempalace(
            topic=topic, content=content, note_path=note_path,
            note_id=f"vault_{note_path.stem}",
        )
        logger.info("Memory saved: %s", note_path)
        if mempalace_saved:
            return f"Memory saved: {filename} and filed to MemPalace."
        return f"Memory saved: {filename}"

    def _save_memory_to_mempalace(
        self, topic: str, content: str, note_path: Path, note_id: str = ""
    ) -> bool:
        """Optionally mirror saved memories into MemPalace."""
        if not self._should_mirror_memory_to_mempalace():
            return False

        try:
            bridge = MemPalaceBridge()
            return bridge.save_memory(
                topic=topic, content=content, source_file=str(note_path), note_id=note_id
            )
        except Exception:
            logger.exception("Failed to mirror memory into MemPalace")
            return False

    def _should_mirror_memory_to_mempalace(self) -> bool:
        """Return True when saved memories should also be filed into MemPalace."""
        override = os.environ.get("MEMPALACE_SAVE_MEMORY", "").strip().lower()
        if override in {"1", "true", "yes", "on"}:
            return True
        if override in {"0", "false", "no", "off"}:
            return False

        backend = os.environ.get("MEMORY_BACKEND", "auto").strip().lower()
        if backend == "vault":
            return False

        try:
            return MemPalaceBridge().is_available()
        except Exception:
            logger.exception("Failed to detect MemPalace availability")
            return False

    def _find_chromium(self) -> str | None:
        """Return the path to the first Chromium binary found on PATH, or None."""
        for name in ["chromium-browser", "chromium", "google-chrome-stable", "google-chrome"]:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        return None

    def _cleanup_dashboard_lock(self, lock: dict) -> None:
        """Kill host Chromium and stop the Docker container from a lock dict."""
        chromium_pid = lock.get("chromium_pid")
        container_id = lock.get("container_id")
        if chromium_pid:
            try:
                os.kill(chromium_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if container_id:
            try:
                subprocess.run(
                    ["docker", "stop", container_id],
                    capture_output=True,
                    timeout=15,
                )
            except Exception:
                pass

    def _close_dashboard_internal(self) -> None:
        """Called by the auto-timeout timer. Closes dashboard without returning a value."""
        if DASHBOARD_LOCK.exists():
            try:
                lock = json.loads(DASHBOARD_LOCK.read_text())
                self._cleanup_dashboard_lock(lock)
            except Exception:
                logger.exception("Error during dashboard auto-close")
            DASHBOARD_LOCK.unlink(missing_ok=True)
        self._dashboard_timer = None
        logger.info("Dashboard auto-closed by timeout")

    def _close_dashboard(self) -> str:
        """Close the dashboard: kill Chromium, stop container, cancel timer."""
        if not DASHBOARD_LOCK.exists():
            return "No dashboard is currently open."
        if self._dashboard_timer:
            self._dashboard_timer.cancel()
            self._dashboard_timer = None
        try:
            lock = json.loads(DASHBOARD_LOCK.read_text())
            self._cleanup_dashboard_lock(lock)
            DASHBOARD_LOCK.unlink(missing_ok=True)
        except Exception as exc:
            logger.exception("Error closing dashboard")
            return f"Error closing dashboard: {exc}"
        return "Display closed."

    def _open_dashboard(self, panels: list, timeout_minutes: int, location: str = "", news_sources: list = None, gdelt_queries: list = None) -> str:
        """Start the dashboard container + host Chromium, write lock, start timer."""
        # --- Handle already-running dashboard ---
        if DASHBOARD_LOCK.exists():
            try:
                lock = json.loads(DASHBOARD_LOCK.read_text())
                os.kill(lock["chromium_pid"], 0)  # signal 0 = existence check
                # Still running — push content update
                params = {"panels": ",".join(panels)}
                if gdelt_queries:
                    params["gdelt_queries"] = "|".join(gdelt_queries)
                if news_sources:
                    params["news_sources"] = ",".join(news_sources)
                url = f"http://localhost:{lock['port']}/refresh?" + urllib.parse.urlencode(params)
                urllib.request.urlopen(url, timeout=5)
                return f"Dashboard updated with {', '.join(panels)}."
            except ProcessLookupError:
                # Chromium died — stale lock, cancel old timer and relaunch
                if self._dashboard_timer:
                    self._dashboard_timer.cancel()
                    self._dashboard_timer = None
                try:
                    self._cleanup_dashboard_lock(lock)
                except Exception:
                    pass
                DASHBOARD_LOCK.unlink(missing_ok=True)
            except Exception:
                DASHBOARD_LOCK.unlink(missing_ok=True)

        if not self.docker_available:
            return f"Dashboard unavailable: {self.docker_error or 'Docker is not running'}."

        # Ensure ~/.miniclaw exists (volume mount target)
        miniclaw_dir = Path.home() / ".miniclaw"
        miniclaw_dir.mkdir(parents=True, exist_ok=True)

        # --- Build RSS feed list from selected source groups ---
        rss_source_map = {
            "osint":    ["https://bellingcat.com/feed/", "https://www.twz.com/rss"],
            "world":    ["https://www.aljazeera.com/xml/rss/all.xml"],
            "local_vt": ["https://vtdigger.org/feed/", "https://www.sevendaysvt.com/rss"],
        }
        selected_sources = news_sources or ["osint", "world"]
        rss_feeds = []
        for src in selected_sources:
            rss_feeds.extend(rss_source_map.get(src, []))

        # --- Build GDELT queries ---
        queries = list(gdelt_queries or [])
        # Add location query if Claude passed one and it's not already included
        city = location.strip() or os.environ.get("WEATHER_LOCATION", "New York,NY").split(",")[0].strip()
        if city and not any(city.lower() in q.lower() for q in queries):
            queries.append(city)

        dashboard_config = json.dumps({
            "rss_feeds": rss_feeds,
            "gdelt_queries": queries,
            "stock_tickers": ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY"],
            "hazards": {
                "enabled": "news" in panels,
                "limit": 3,
                "min_score": 40,
                "days": 14,
                "fetch_limit": 20,
                "categories": [
                    "wildfires",
                    "severeStorms",
                    "volcanoes",
                    "floods",
                    "earthquakes",
                    "landslides",
                    "extremeTemperatures",
                    "dustHaze",
                ],
            },
        })
        weather_loc = location.strip() or os.environ.get("WEATHER_LOCATION", "New York,NY")

        # --- Start Flask container (detached) ---
        docker_cmd = [
            "docker", "run", "-d",
            "--network=host",
            "--memory=512m",
            "--cpus=1.5",
            "--security-opt=no-new-privileges",
            "--tmpfs=/tmp:size=64m",
            "--tmpfs=/dev/shm:size=256m",
            "-v", f"{miniclaw_dir}:/miniclaw",
            "-e", f"SKILL_INPUT={json.dumps({'panels': panels, 'timeout_minutes': timeout_minutes})}",
            "-e", f"DASHBOARD_CONFIG={dashboard_config}",
            "-e", f"WEATHER_LOCATION={weather_loc}",
            "miniclaw/dashboard:latest",
        ]
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return f"Failed to start dashboard: {result.stderr.strip()[:300]}"

        container_id = result.stdout.strip()

        # --- Wait for Flask to be ready (up to 10s) ---
        for _ in range(20):
            try:
                urllib.request.urlopen(
                    f"http://localhost:{DASHBOARD_PORT}/health", timeout=1
                )
                break
            except Exception:
                time.sleep(0.5)
        else:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=10)
            return "Dashboard container started but server did not respond."

        # --- Launch Chromium on host in kiosk mode ---
        chromium = self._find_chromium()
        if not chromium:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=10)
            return "I don't see a display connected or Chromium is not installed."

        try:
            proc = subprocess.Popen(
                [chromium, "--kiosk", "--noerrdialogs",
                 "--disable-infobars", f"http://localhost:{DASHBOARD_PORT}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=10)
            return f"Failed to launch display: {exc}"

        # --- Write lock file ---
        DASHBOARD_LOCK.write_text(json.dumps({
            "chromium_pid": proc.pid,
            "container_id": container_id,
            "port": DASHBOARD_PORT,
        }))

        # --- Start auto-close timer ---
        if self._dashboard_timer:
            self._dashboard_timer.cancel()
        self._dashboard_timer = threading.Timer(
            timeout_minutes * 60, self._close_dashboard_internal
        )
        self._dashboard_timer.daemon = True
        self._dashboard_timer.start()

        panel_list = ", ".join(panels) if panels else "all panels"
        return f"Dashboard is up with {panel_list}."

    def _execute_dashboard(self, tool_input: dict) -> str:
        """Route open/close dashboard actions."""
        action = str(tool_input.get("action", "")).strip().lower()
        if action == "open":
            panels = tool_input.get("panels", ["news", "weather", "stocks", "music"])
            timeout_minutes = int(tool_input.get("timeout_minutes", 10))
            location = tool_input.get("location", "")
            news_sources = tool_input.get("news_sources", ["osint", "world"])
            gdelt_queries = tool_input.get("gdelt_queries", [])
            return self._open_dashboard(panels, timeout_minutes, location, news_sources, gdelt_queries)
        if action == "close":
            return self._close_dashboard()
        return f"Unknown dashboard action '{action}'. Use 'open' or 'close'."

    def _execute_soundcloud(self, tool_input: dict) -> str:
        """Play or stop music via yt-dlp + mpv on the host."""
        import shutil
        action = str(tool_input.get("action", "play")).strip().lower()

        if action == "stop":
            if self._mpv_process and self._mpv_process.poll() is None:
                self._mpv_process.terminate()
                self._mpv_process = None
                now_playing = Path.home() / ".miniclaw" / "now_playing.json"
                now_playing.unlink(missing_ok=True)
                return "Stopped."
            return "Nothing is playing."

        query = str(tool_input.get("query", "")).strip()
        if not query:
            return "No search query provided."

        if not shutil.which("yt-dlp"):
            return "yt-dlp not found. Install with: pip install yt-dlp"
        if not shutil.which("mpv"):
            return "mpv not found. Install with: sudo apt install mpv"

        # Stop any currently playing track
        if self._mpv_process and self._mpv_process.poll() is None:
            self._mpv_process.terminate()
            self._mpv_process = None

        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--get-title", "--get-url",
                    "-f", "bestaudio",
                    "--no-playlist",
                    "--cache-dir", "/tmp/yt-dlp-cache",
                    f"scsearch1:{query}",
                ],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return f"Search timed out for '{query}'."
        except FileNotFoundError:
            return "yt-dlp not found on PATH."

        if result.returncode != 0 or not result.stdout.strip():
            return f"No results found for '{query}' on SoundCloud."

        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return f"Could not retrieve stream for '{query}'."

        title, stream_url = lines[0], lines[1]

        self._mpv_process = subprocess.Popen(
            ["mpv", "--no-video", "--really-quiet", stream_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Write now_playing for dashboard music widget
        now_playing_path = Path.home() / ".miniclaw" / "now_playing.json"
        try:
            import time as _time
            now_playing_path.write_text(
                json.dumps({"title": title, "timestamp": _time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

        return f"Now playing: {title}"

    def _collect_env_vars(self, var_names: list[str]) -> dict[str, str]:
        """Collect env vars that exist in the host environment."""
        return {var: val for var in var_names if (val := os.environ.get(var))}
