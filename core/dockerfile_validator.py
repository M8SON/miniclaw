"""
Dockerfile allowlist validator for voice-installed skills.

Permitted instructions:
  FROM miniclaw/base:latest     (only this exact base — no multi-stage)
  RUN pip install / pip3 install
  RUN apt-get update / apt-get install / apt-get clean
  RUN rm -rf /var/lib/apt/lists  (common apt cleanup)
  COPY <relative-local-path> <dest>
  WORKDIR
  CMD
  ENV

Everything else is rejected, including:
  ADD (can fetch URLs), multi-stage FROM, curl|sh, wget|sh, eval,
  netcat, absolute COPY sources, Docker socket references.
"""

import re
from pathlib import Path


class DockerfileValidationError(Exception):
    pass


BLOCKED_PATTERNS = [
    (r"curl\s+.*\|\s*(ba)?sh",   "curl pipe to shell"),
    (r"wget\s+.*\|\s*(ba)?sh",   "wget pipe to shell"),
    (r"\beval\b",                 "eval"),
    (r"\bnetcat\b",               "netcat"),
    (r"(?<!\w)nc\s+-",            "netcat (nc)"),
    (r"--privileged",             "privileged flag"),
    (r"/var/run/docker",          "Docker socket reference"),
    (r"^COPY\s+https?://",        "COPY from URL"),
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


def validate(dockerfile_path: Path) -> None:
    """
    Validate a Dockerfile against the MiniClaw skill allowlist.
    Raises DockerfileValidationError with a descriptive message on failure.
    """
    text = dockerfile_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    from_count = 0

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Global blocked pattern check (applies to every line)
        for pattern, label in BLOCKED_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                raise DockerfileValidationError(
                    f"Line {lineno}: blocked pattern '{label}' — {raw_line!r}"
                )

        instruction = line.split()[0].upper() if line.split() else ""

        if instruction == "FROM":
            from_count += 1
            if from_count > 1:
                raise DockerfileValidationError(
                    f"Line {lineno}: multi-stage builds are not allowed (second FROM)"
                )
            parts = line.split()
            # Handle optional AS alias: FROM image AS alias
            image_ref = parts[1] if len(parts) > 1 else ""
            if image_ref.lower() != "miniclaw/base:latest":
                raise DockerfileValidationError(
                    f"Line {lineno}: base image must be 'miniclaw/base:latest', got {image_ref!r}"
                )

        elif instruction == "ADD":
            raise DockerfileValidationError(
                f"Line {lineno}: ADD is not allowed — use COPY instead"
            )

        elif instruction == "RUN":
            run_body = re.sub(r"^RUN\s+", "", line, flags=re.IGNORECASE).strip()
            # Strip shell -c wrapper if present
            run_body = re.sub(r"^/bin/(ba)?sh\s+-c\s+", "", run_body).strip("\"'")
            if not _is_allowed_run(run_body):
                raise DockerfileValidationError(
                    f"Line {lineno}: RUN only permits pip install / apt-get commands. "
                    f"Got: {run_body!r}"
                )

        elif instruction == "COPY":
            parts = line.split()
            # Skip past any --flag options (e.g. --chown=user:group, --chmod=755)
            src_parts = [p for p in parts[1:] if not p.startswith("--")]
            if len(src_parts) >= 2:
                sources = src_parts[:-1]
                invalid = next((src for src in sources if not _is_relative_copy_source(src)), None)
                if invalid is not None:
                    raise DockerfileValidationError(
                        f"Line {lineno}: COPY source must be a relative local path, "
                        f"got {invalid!r}"
                    )

    if from_count == 0:
        raise DockerfileValidationError("Dockerfile has no FROM instruction")


def _is_allowed_run(run_body: str) -> bool:
    """
    Return True if every && -separated segment starts with an allowed prefix.
    """
    segments = [s.strip() for s in re.split(r"\s*(?:&&|\|\||;)\s*", run_body)]
    return all(
        any(seg.startswith(prefix) for prefix in ALLOWED_RUN_PREFIXES)
        for seg in segments
        if seg
    )


def _is_relative_copy_source(src: str) -> bool:
    """Return True when a COPY source stays within the build context."""
    if not src or src.startswith("/"):
        return False

    src_path = Path(src)
    return ".." not in src_path.parts
