from __future__ import annotations

import shutil
from pathlib import Path
import subprocess
import tempfile
from typing import Any
import wave

from pywhispercpp.model import Model as WhisperModel

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS_DIR = ROOT / "models"


class TitsModel(WhisperModel):
    def __init__(
        self,
        model: str | Path = "tiny",
        models_dir: str | Path | None = None,
        model_dir: str | Path | None = None,
        language: str = "en",
        **params: Any,
    ) -> None:
        if models_dir is not None and model_dir is not None:
            raise ValueError("pass only one of models_dir or model_dir")

        selected_models_dir = model_dir if model_dir is not None else models_dir
        resolved_models_dir = (
            Path(selected_models_dir).expanduser()
            if selected_models_dir is not None
            else DEFAULT_MODELS_DIR
        )
        resolved_model = self._resolve_model(model, resolved_models_dir)

        super().__init__(
            model=str(resolved_model),
            models_dir=str(resolved_models_dir),
            language=language,
            **params,
        )

    def transcribe(self, media: str | Any, *args: Any, **kwargs: Any):
        if not isinstance(media, str):
            return super().transcribe(media, *args, **kwargs)

        prepared_media, cleanup_path = self._prepare_media(Path(media).expanduser())
        try:
            return super().transcribe(str(prepared_media), *args, **kwargs)
        finally:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)

    @staticmethod
    def _resolve_model(model: str | Path, models_dir: Path) -> str | Path:
        model_path = Path(model).expanduser()
        if model_path.is_file():
            return model_path

        candidate = models_dir / model_path
        if candidate.is_file():
            return candidate

        if candidate.suffix != ".bin":
            bin_candidate = candidate.with_name(f"{candidate.name}.bin")
            if bin_candidate.is_file():
                return bin_candidate

        return model

    @staticmethod
    def _prepare_media(media_path: Path) -> tuple[Path, Path | None]:
        if media_path.suffix.lower() == ".wav" and TitsModel._is_normalized_wav(
            media_path
        ):
            return media_path, None

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg is required to normalize audio to mono 16 kHz WAV"
            )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            output_path = Path(handle.name)

        result = subprocess.run(
            [
                ffmpeg,
                "-i",
                str(media_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output_path),
                "-y",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(
                result.stderr.strip() or "ffmpeg failed to normalize audio"
            )

        return output_path, output_path

    @staticmethod
    def _is_normalized_wav(media_path: Path) -> bool:
        try:
            with wave.open(str(media_path), "rb") as wav_file:
                return (
                    wav_file.getnchannels() == 1
                    and wav_file.getframerate() == 16000
                    and wav_file.getsampwidth() == 2
                )
        except (wave.Error, FileNotFoundError):
            return False
