"""
Dockerfile validator for MiniClaw skills.

Per-tier allowlist:
  bundled  — no validation (trusted; repo-reviewed)
  authored — FROM must be miniclaw/base:latest; RUN only pip/apt prefixes; no ADD; no USER
  imported — everything authored allows, PLUS apt-get packages must be in
             the allowlist (core.apt_allowlist), and pip install must not use
             --index-url / --extra-index-url
"""

import re
from pathlib import Path

from core.apt_allowlist import load_apt_allowlist
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED, TIER_DEV


class DockerfileValidationError(Exception):
    pass


BLOCKED_PATTERNS = [
    (r"curl\s+.*\|\s*(ba)?sh", "curl pipe to shell"),
    (r"wget\s+.*\|\s*(ba)?sh", "wget pipe to shell"),
    (r"\beval\b",               "eval"),
    (r"\bnetcat\b",             "netcat"),
    (r"(?<!\w)nc\s+-",          "netcat (nc)"),
    (r"--privileged",           "privileged flag"),
    (r"/var/run/docker",        "Docker socket reference"),
    (r"^COPY\s+https?://",      "COPY from URL"),
]

ALLOWED_RUN_PREFIXES = (
    "pip install",
    "pip3 install",
    "apt-get install",
    "apt-get update",
    "apt-get clean",
    "apt-get -y install",
    "apt-get -y update",
    "rm -rf /var/lib/apt/lists",
)

BLOCKED_INSTRUCTIONS = {"ADD", "USER", "VOLUME"}


def validate(dockerfile_path: Path, *, tier: str = TIER_AUTHORED) -> None:
    """
    Validate a Dockerfile against the per-tier allowlist.

    Raises DockerfileValidationError with a descriptive message on failure.
    Bundled and dev tiers bypass validation entirely.
    """
    if tier in (TIER_BUNDLED, TIER_DEV):
        return

    apt_allowlist = load_apt_allowlist() if tier == TIER_IMPORTED else None
    text = dockerfile_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    from_count = 0

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        for pattern, label in BLOCKED_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                raise DockerfileValidationError(
                    f"Line {lineno}: blocked pattern '{label}' — {raw_line!r}"
                )

        instruction = line.split()[0].upper() if line.split() else ""

        if instruction in BLOCKED_INSTRUCTIONS:
            raise DockerfileValidationError(
                f"Line {lineno}: {instruction} is not allowed for tier {tier!r}"
            )

        if instruction == "FROM":
            from_count += 1
            if from_count > 1:
                raise DockerfileValidationError(
                    f"Line {lineno}: multi-stage builds are not allowed (second FROM)"
                )
            parts = line.split()
            image_ref = parts[1] if len(parts) > 1 else ""
            if image_ref.lower() != "miniclaw/base:latest":
                raise DockerfileValidationError(
                    f"Line {lineno}: base image must be 'miniclaw/base:latest', got {image_ref!r}"
                )

        elif instruction == "RUN":
            run_body = re.sub(r"^RUN\s+", "", line, flags=re.IGNORECASE).strip()
            run_body = re.sub(r"^/bin/(ba)?sh\s+-c\s+", "", run_body).strip("\"'")
            if not _is_allowed_run(run_body):
                raise DockerfileValidationError(
                    f"Line {lineno}: RUN only permits pip install / apt-get commands. "
                    f"Got: {run_body!r}"
                )
            if tier == TIER_IMPORTED:
                _validate_run_imported(run_body, apt_allowlist, lineno)

        elif instruction == "COPY":
            parts = line.split()
            if any(p.startswith("--from=") for p in parts[1:]):
                raise DockerfileValidationError(
                    f"Line {lineno}: COPY --from is not allowed for tier {tier!r}"
                )
            src_parts = [p for p in parts[1:] if not p.startswith("--")]
            if len(src_parts) >= 2:
                sources = src_parts[:-1]
                invalid = next(
                    (src for src in sources if not _is_relative_copy_source(src)),
                    None,
                )
                if invalid is not None:
                    raise DockerfileValidationError(
                        f"Line {lineno}: COPY source must be a relative local path, "
                        f"got {invalid!r}"
                    )

    if from_count == 0:
        raise DockerfileValidationError("Dockerfile has no FROM instruction")


def _is_allowed_run(run_body: str) -> bool:
    segments = [s.strip() for s in re.split(r"\s*(?:&&|\|\||;)\s*", run_body)]
    return all(
        any(seg.startswith(prefix) for prefix in ALLOWED_RUN_PREFIXES)
        for seg in segments
        if seg
    )


def _validate_run_imported(run_body: str, apt_allowlist: frozenset[str], lineno: int) -> None:
    segments = [s.strip() for s in re.split(r"\s*(?:&&|\|\||;)\s*", run_body) if s.strip()]
    for seg in segments:
        if seg.startswith("pip install") or seg.startswith("pip3 install"):
            if "--index-url" in seg or "--extra-index-url" in seg:
                raise DockerfileValidationError(
                    f"Line {lineno}: pip install --index-url / --extra-index-url "
                    "is not allowed for imported skills"
                )
        if seg.startswith("apt-get install") or seg.startswith("apt-get -y install"):
            tokens = [
                t for t in seg.split()
                if t not in {"apt-get", "install", "-y", "--yes", "--no-install-recommends"}
            ]
            for pkg in tokens:
                if pkg in apt_allowlist:
                    continue
                raise DockerfileValidationError(
                    f"Line {lineno}: apt package {pkg!r} is not in the allowlist "
                    "(extend via ~/.miniclaw/config/apt-allowlist.txt)"
                )


def _is_relative_copy_source(src: str) -> bool:
    if not src or src.startswith("/"):
        return False
    src_path = Path(src)
    return ".." not in src_path.parts
