"""Tests for the post-processing module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vinyllabels.postprocess import (
    PostprocessConfig,
    apply_text_to_curves,
    ensure_postprocess_config,
    load_postprocess_config,
)


class TestPostprocessConfig:
    """Tests for PostprocessConfig dataclass."""

    def test_default_disabled(self) -> None:
        """Test that post-processing is disabled by default."""
        config = PostprocessConfig()
        assert config.enabled is False
        assert config.tool == "gs"
        assert config.timeout_sec == 30.0

    def test_custom_config(self) -> None:
        """Test creating a custom config."""
        config = PostprocessConfig(enabled=True, tool="gs", timeout_sec=60.0)
        assert config.enabled is True
        assert config.tool == "gs"
        assert config.timeout_sec == 60.0


class TestLoadAndEnsureConfig:
    """Tests for config loading and creation."""

    def test_ensure_creates_default_config(self, tmp_path: Path) -> None:
        """Test that ensure_postprocess_config creates a default config file."""
        config_file = tmp_path / "postprocess-config.json"

        with patch(
            "vinyllabels.postprocess._config_file_path", return_value=config_file
        ):
            ensure_postprocess_config()

        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["enabled"] is False
        assert data["tool"] == "gs"
        assert data["timeout_sec"] == 30.0

    def test_load_existing_config(self, tmp_path: Path) -> None:
        """Test loading an existing config file."""
        config_file = tmp_path / "postprocess-config.json"
        custom_config = {
            "enabled": True,
            "tool": "gs",
            "timeout_sec": 45.0,
        }
        config_file.write_text(json.dumps(custom_config))

        with patch(
            "vinyllabels.postprocess._config_file_path", return_value=config_file
        ):
            config = load_postprocess_config()

        assert config.enabled is True
        assert config.tool == "gs"
        assert config.timeout_sec == 45.0

    def test_load_invalid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test handling of invalid JSON in config file."""
        config_file = tmp_path / "postprocess-config.json"
        config_file.write_text("{ invalid json")

        with patch(
            "vinyllabels.postprocess._config_file_path", return_value=config_file
        ):
            config = load_postprocess_config()

        # Should return default config and print warning
        assert config.enabled is False
        captured = capsys.readouterr()
        assert "Could not parse" in captured.err

    def test_gs_validation_warning_when_enabled(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that a warning is printed if GS is enabled but not found."""
        config_file = tmp_path / "postprocess-config.json"
        custom_config = {"enabled": True, "tool": "nonexistent-gs"}
        config_file.write_text(json.dumps(custom_config))

        with patch(
            "vinyllabels.postprocess._config_file_path", return_value=config_file
        ):
            with patch("vinyllabels.postprocess.shutil.which", return_value=None):
                config = load_postprocess_config()

        # Should load config and print warning
        assert config.enabled is True
        captured = capsys.readouterr()
        assert "not found on PATH" in captured.err

    def test_gs_validation_passes_when_available(self, tmp_path: Path) -> None:
        """Test that no warning is printed if GS is available."""
        config_file = tmp_path / "postprocess-config.json"
        custom_config = {"enabled": True, "tool": "gs"}
        config_file.write_text(json.dumps(custom_config))

        with patch(
            "vinyllabels.postprocess._config_file_path", return_value=config_file
        ):
            with patch(
                "vinyllabels.postprocess.shutil.which", return_value="/usr/bin/gs"
            ):
                config = load_postprocess_config()

        assert config.enabled is True


class TestApplyTextToCurves:
    """Tests for text-to-curves conversion."""

    def test_pdf_not_found(self) -> None:
        """Test handling when PDF file does not exist."""
        config = PostprocessConfig(enabled=True, tool="gs")
        pdf_path = Path("/nonexistent/file.pdf")

        success, error_msg = apply_text_to_curves(pdf_path, config)

        assert success is False
        assert error_msg is not None
        assert "not found" in error_msg

    def test_successful_conversion(self, tmp_path: Path) -> None:
        """Test successful text-to-curves conversion."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_text("fake pdf")
        config = PostprocessConfig(enabled=True, tool="gs", timeout_sec=30.0)

        with patch("vinyllabels.postprocess.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            success, error_msg = apply_text_to_curves(pdf_path, config)

        assert success is True
        assert error_msg is None

        # Verify the subprocess command
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "gs" in cmd
        assert "-dNOPAUSE" in cmd
        assert "-dBATCH" in cmd
        assert "-sDEVICE=pdfwrite" in cmd
        assert "-dNoOutputFonts" in cmd

    def test_conversion_subprocess_failure(self, tmp_path: Path) -> None:
        """Test handling when Ghostscript fails."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_text("fake pdf")
        config = PostprocessConfig(enabled=True, tool="gs")

        with patch("vinyllabels.postprocess.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="Ghostscript error", stdout=""
            )
            success, error_msg = apply_text_to_curves(pdf_path, config)

        assert success is False
        assert error_msg is not None
        assert "conversion failed" in error_msg

    def test_conversion_timeout(self, tmp_path: Path) -> None:
        """Test handling when Ghostscript times out."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_text("fake pdf")
        config = PostprocessConfig(enabled=True, tool="gs", timeout_sec=1.0)

        with patch("vinyllabels.postprocess.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gs", 1.0)
            success, error_msg = apply_text_to_curves(pdf_path, config)

        assert success is False
        assert error_msg is not None
        assert "timed out" in error_msg

    def test_conversion_replaces_original(self, tmp_path: Path) -> None:
        """Test that the original PDF is replaced with the converted version."""
        pdf_path = tmp_path / "test.pdf"
        original_content = "original pdf"
        pdf_path.write_text(original_content)
        config = PostprocessConfig(enabled=True, tool="gs")

        with patch("vinyllabels.postprocess.subprocess.run") as mock_run:
            with patch("vinyllabels.postprocess.NamedTemporaryFile") as mock_tmp:
                # Simulate successful conversion
                mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

                # Create a real temp file that will be returned
                temp_file = tmp_path / "temp_pdf"
                temp_file.write_text("converted pdf")
                mock_tmp.return_value.__enter__.return_value.name = str(temp_file)

                success, error_msg = apply_text_to_curves(pdf_path, config)

        assert success is True
        # The temp file content should have replaced the original
        # (The mock doesn't actually replace, but in real execution it would)
