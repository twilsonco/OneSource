"""Post-processing for generated PDFs (e.g., text-to-curves conversion).

This module provides configuration management and subprocess wrappers for
post-processing tasks applied to PDFs after generation. Currently supports
text-to-curves conversion via Ghostscript (gs).

Configuration is loaded from ``postprocess-config.json`` at the project root.
If the file does not exist, a default template is created on first load.

Errors during post-processing are logged as warnings and do NOT fail the job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


__all__ = [
    "PostprocessConfig",
    "apply_text_to_curves",
    "ensure_postprocess_config",
    "load_postprocess_config",
]


@dataclass
class PostprocessConfig:
    """Configuration for post-processing options."""

    enabled: bool = False
    tool: str = "gs"
    timeout_sec: float = 30.0


def _find_project_root() -> Path:
    """Find the project root (where pyproject.toml lives).

    Returns the directory containing pyproject.toml, searching upward from the
    current working directory. If not found within 10 levels, returns cwd.
    """
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "pyproject.toml").exists():
            return cwd
        parent = cwd.parent
        if parent == cwd:
            break  # reached filesystem root
        cwd = parent
    return Path.cwd()


def _config_file_path() -> Path:
    """Return the path to postprocess-config.json (project root)."""
    return _find_project_root() / "postprocess-config.json"


def _validate_gs_available(tool: str) -> tuple[bool, str | None]:
    """Check if Ghostscript (gs) is available on PATH.

    Args:
        tool: The tool name to search for (e.g., 'gs').

    Returns:
        (available, error_msg): True if tool found on PATH, False otherwise.
        error_msg is None on success, a descriptive message on failure.
    """
    if shutil.which(tool) is not None:
        return True, None
    return False, f"Ghostscript tool '{tool}' not found on PATH"


def ensure_postprocess_config() -> PostprocessConfig:
    """Load or create the postprocess config file.

    If the config file does not exist at the project root, creates a default
    template with post-processing disabled. Validates that Ghostscript is
    available on PATH and warns if missing (non-blocking).

    Returns:
        PostprocessConfig: The configuration object.
    """
    config_path = _config_file_path()

    # Load or create the config file
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            config = PostprocessConfig(
                enabled=data.get("enabled", False),
                tool=data.get("tool", "gs"),
                timeout_sec=data.get("timeout_sec", 30.0),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(
                f"Warning: Could not parse {config_path}: {e}. Using defaults.",
                file=sys.stderr,
            )
            config = PostprocessConfig()
    else:
        # Create default template
        default_config = {
            "enabled": False,
            "tool": "gs",
            "timeout_sec": 30.0,
        }
        try:
            config_path.write_text(
                json.dumps(default_config, indent=2), encoding="utf-8"
            )
        except OSError as e:
            print(
                f"Warning: Could not create {config_path}: {e}. Using defaults.",
                file=sys.stderr,
            )
        config = PostprocessConfig()

    # Validate GS availability (warn, don't fail)
    if config.enabled:
        available, error_msg = _validate_gs_available(config.tool)
        if not available:
            print(f"Warning: {error_msg}", file=sys.stderr)

    return config


def load_postprocess_config() -> PostprocessConfig:
    """Load the postprocess config, creating defaults if needed.

    Calls ensure_postprocess_config() to handle file creation and validation.

    Returns:
        PostprocessConfig: The configuration object.
    """
    return ensure_postprocess_config()


def apply_text_to_curves(
    pdf_path: Path, config: PostprocessConfig
) -> tuple[bool, str | None]:
    """Convert text in a PDF to curves using Ghostscript.

    Calls ``gs`` with ``-dNoOutputFonts`` to convert all text to vector curves,
    then replaces the original PDF with the output.

    Args:
        pdf_path: Path to the input PDF file.
        config: PostprocessConfig object with tool, timeout, etc.

    Returns:
        (success, error_msg): True if conversion succeeded, False otherwise.
        error_msg is None on success, a descriptive error message on failure.
    """
    if not pdf_path.exists():
        return False, f"PDF file not found: {pdf_path}"

    try:
        # Create a temporary file for the output
        with NamedTemporaryFile(
            suffix=".pdf", dir=pdf_path.parent, delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        # Build the Ghostscript command
        cmd = [
            config.tool,
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=pdfwrite",
            "-dNoOutputFonts",
            f"-sOutputFile={tmp_path}",
            str(pdf_path),
        ]

        # Run the conversion
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeout_sec,
        )

        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            error_output = (
                result.stderr.strip() if result.stderr else result.stdout.strip()
            )
            return False, f"Ghostscript conversion failed: {error_output}"

        # Replace the original PDF with the converted version
        try:
            tmp_path.replace(pdf_path)
        except OSError as e:
            tmp_path.unlink(missing_ok=True)
            return False, f"Could not replace original PDF: {e}"

        return True, None

    except subprocess.TimeoutExpired:
        return False, f"Ghostscript conversion timed out after {config.timeout_sec}s"
    except Exception as e:
        return False, f"Text-to-curves conversion failed: {type(e).__name__}: {e}"
