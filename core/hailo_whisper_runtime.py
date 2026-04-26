from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import whisper
from whisper import tokenizer as whisper_tokenizer

try:
    from hailo_platform import HEF, FormatType, HailoSchedulingAlgorithm, VDevice

    _hailo_platform_import_error = None
except Exception as exc:  # pragma: no cover - exercised on Pi hardware
    HEF = None
    FormatType = None
    HailoSchedulingAlgorithm = None
    VDevice = None
    _hailo_platform_import_error = exc


SUPPORTED_HAILO_ARCHES = ("hailo8", "hailo8l", "hailo10h")
CHUNK_SECONDS_BY_MODEL = {
    "base": 5,
    "base.en": 5,
    "tiny": 10,
    "tiny.en": 10,
}


@dataclass(frozen=True)
class RuntimeAssets:
    model_dir: Path
    encoder_hef: Path
    decoder_hef: Path
    token_embedding_weight: Path
    onnx_add_input: Path
    hw_arch: str


class HailoTranscriptionRuntime:
    asset_kind = "transcription"

    def __init__(self, model_name: str, assets_root: Path, hw_arch: str | None = None):
        self.model_name = model_name
        self.assets_root = Path(assets_root)
        self.assets = self._resolve_assets(model_name, self.assets_root, hw_arch=hw_arch)
        self.decoding_sequence_length = 32 if "tiny" in self.model_name else 24
        self.timeout_ms = 100000000
        self.token_embedding_weight = np.load(self.assets.token_embedding_weight)
        self.onnx_add_input = np.load(self.assets.onnx_add_input)
        self.tokenizer = self._build_tokenizer(model_name)

    @classmethod
    def self_check(
        cls, model_name: str, assets_root: Path, hw_arch: str | None = None
    ) -> None:
        cls._resolve_assets(model_name, Path(assets_root), hw_arch=hw_arch)
        if _hailo_platform_import_error is not None:
            raise RuntimeError("hailo_platform python module not installed")

    @classmethod
    def _resolve_assets(
        cls, model_name: str, assets_root: Path, hw_arch: str | None = None
    ) -> RuntimeAssets:
        model_dir = assets_root / model_name
        if not model_dir.exists():
            raise RuntimeError(f"{cls.asset_kind} model asset missing")

        decoder_assets_dir = model_dir / "decoder_assets"
        token_embedding_weight = (
            decoder_assets_dir / f"token_embedding_weight_{model_name}.npy"
        )
        onnx_add_input = decoder_assets_dir / f"onnx_add_input_{model_name}.npy"
        if not token_embedding_weight.exists() or not onnx_add_input.exists():
            raise RuntimeError(f"{cls.asset_kind} model asset missing")

        hefs_root = model_dir / "hefs"
        selected_arch = hw_arch or cls._auto_detect_hw_arch(hefs_root, model_name)
        if selected_arch is None:
            raise RuntimeError(f"{cls.asset_kind} model HEF missing")

        encoder_hef = cls._resolve_encoder_hef(hefs_root / selected_arch, model_name)
        decoder_hef = cls._resolve_decoder_hef(hefs_root / selected_arch, model_name)
        if encoder_hef is None or decoder_hef is None:
            raise RuntimeError(f"{cls.asset_kind} model HEF missing")

        return RuntimeAssets(
            model_dir=model_dir,
            encoder_hef=encoder_hef,
            decoder_hef=decoder_hef,
            token_embedding_weight=token_embedding_weight,
            onnx_add_input=onnx_add_input,
            hw_arch=selected_arch,
        )

    @staticmethod
    def _auto_detect_hw_arch(hefs_root: Path, model_name: str) -> str | None:
        for arch in SUPPORTED_HAILO_ARCHES:
            arch_dir = hefs_root / arch
            if not arch_dir.exists():
                continue
            if (
                HailoTranscriptionRuntime._resolve_encoder_hef(arch_dir, model_name)
                is not None
                and HailoTranscriptionRuntime._resolve_decoder_hef(arch_dir, model_name)
                is not None
            ):
                return arch
        return None

    @staticmethod
    def _resolve_encoder_hef(arch_dir: Path, model_name: str) -> Path | None:
        candidates = {
            "base": ["base-whisper-encoder-5s.hef", "base-whisper-encoder-5s_h8l.hef"],
            "base.en": [
                "base_en-whisper-encoder-5s.hef",
                "base_en-whisper-encoder-5s_h8l.hef",
            ],
            "tiny": ["tiny-whisper-encoder-10s.hef", "tiny-whisper-encoder-10s_15dB.hef", "tiny-whisper-encoder-10s_15dB_h8l.hef"],
            "tiny.en": [
                "tiny_en-whisper-encoder-10s.hef",
                "tiny_en-whisper-encoder-10s_h8l.hef",
            ],
        }.get(model_name, [])
        for name in candidates:
            path = arch_dir / name
            if path.exists():
                return path
        return None

    @staticmethod
    def _resolve_decoder_hef(arch_dir: Path, model_name: str) -> Path | None:
        candidates = {
            "base": [
                "base-whisper-decoder-fixed-sequence-matmul-split.hef",
                "base-whisper-decoder-fixed-sequence-matmul-split_h8l.hef",
                "base-whisper-decoder-fixed-sequence.hef",
            ],
            "base.en": [
                "base_en-whisper-decoder-fixed-sequence.hef",
                "base_en-whisper-decoder-fixed-sequence-matmul-split.hef",
            ],
            "tiny": [
                "tiny-whisper-decoder-fixed-sequence-matmul-split.hef",
                "tiny-whisper-decoder-fixed-sequence-matmul-split_h8l.hef",
                "tiny-whisper-decoder-fixed-sequence.hef",
            ],
            "tiny.en": [
                "tiny_en-whisper-decoder-fixed-sequence.hef",
                "tiny_en-whisper-decoder-fixed-sequence-matmul-split.hef",
            ],
        }.get(model_name, [])
        for name in candidates:
            path = arch_dir / name
            if path.exists():
                return path
        return None

    @staticmethod
    def _build_tokenizer(model_name: str):
        multilingual = not model_name.endswith(".en")
        language = "en" if multilingual else None
        return whisper_tokenizer.get_tokenizer(
            multilingual=multilingual,
            language=language,
            task="transcribe",
        )

    def transcribe_file(self, audio_file: str) -> str:
        audio = whisper.load_audio(audio_file)
        return self._clean_transcription(" ".join(self._transcribe_audio_chunks(audio)))

    def _transcribe_audio_chunks(self, audio: np.ndarray) -> list[str]:
        texts: list[str] = []
        for mel in self._iter_mel_chunks(audio):
            text = self._transcribe_mel_chunk(mel)
            if text:
                texts.append(text)
        return texts

    def _iter_mel_chunks(self, audio: np.ndarray):
        chunk_seconds = CHUNK_SECONDS_BY_MODEL.get(self.model_name, 5)
        chunk_samples = int(chunk_seconds * 16000)
        if len(audio) == 0:
            audio = np.zeros(chunk_samples, dtype=np.float32)

        for start in range(0, max(len(audio), 1), chunk_samples):
            chunk = audio[start : start + chunk_samples]
            chunk = whisper.pad_or_trim(chunk, length=chunk_samples)
            mel = whisper.log_mel_spectrogram(chunk).cpu().numpy()
            mel = np.expand_dims(mel, axis=0)
            mel = np.expand_dims(mel, axis=2)
            yield np.ascontiguousarray(np.transpose(mel, (0, 2, 3, 1)).astype(np.float32))

    def _transcribe_mel_chunk(self, input_mel: np.ndarray) -> str:
        if _hailo_platform_import_error is not None:
            raise RuntimeError("hailo_platform python module not installed")

        params = VDevice.create_params()
        params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN

        decoder_hef = HEF(str(self.assets.decoder_hef))
        sorted_output_names = decoder_hef.get_sorted_output_names()
        decoder_model_name = decoder_hef.get_network_group_names()[0]

        with VDevice(params) as vdevice:
            encoder_infer_model = vdevice.create_infer_model(str(self.assets.encoder_hef))
            decoder_infer_model = vdevice.create_infer_model(str(self.assets.decoder_hef))

            encoder_infer_model.input().set_format_type(FormatType.FLOAT32)
            encoder_infer_model.output().set_format_type(FormatType.FLOAT32)
            decoder_infer_model.input(
                f"{decoder_model_name}/input_layer1"
            ).set_format_type(FormatType.FLOAT32)
            decoder_infer_model.input(
                f"{decoder_model_name}/input_layer2"
            ).set_format_type(FormatType.FLOAT32)
            for output_name in sorted_output_names:
                decoder_infer_model.output(output_name).set_format_type(FormatType.FLOAT32)

            with encoder_infer_model.configure() as encoder_configured:
                with decoder_infer_model.configure() as decoder_configured:
                    encoder_bindings = encoder_configured.create_bindings()
                    decoder_bindings = decoder_configured.create_bindings()

                    encoder_bindings.input().set_buffer(input_mel)
                    encoder_buffer = np.zeros(
                        encoder_infer_model.output().shape, dtype=np.float32
                    )
                    encoder_bindings.output().set_buffer(encoder_buffer)
                    encoder_configured.run([encoder_bindings], self.timeout_ms)
                    encoded_features = encoder_bindings.output().get_buffer()

                    decoder_input_ids = np.zeros(
                        (1, self.decoding_sequence_length), dtype=np.int64
                    )
                    decoder_input_ids[0, 0] = self.tokenizer.sot
                    generated_tokens: list[int] = []

                    for i in range(self.decoding_sequence_length - 1):
                        tokenized_ids = self._tokenization(decoder_input_ids)
                        decoder_bindings.input(
                            f"{decoder_model_name}/input_layer1"
                        ).set_buffer(encoded_features)
                        decoder_bindings.input(
                            f"{decoder_model_name}/input_layer2"
                        ).set_buffer(tokenized_ids)

                        for output_name in sorted_output_names:
                            output_buffer = np.zeros(
                                decoder_infer_model.output(output_name).shape,
                                dtype=np.float32,
                            )
                            decoder_bindings.output(output_name).set_buffer(output_buffer)

                        decoder_configured.run([decoder_bindings], self.timeout_ms)
                        decoder_outputs = np.concatenate(
                            [
                                decoder_bindings.output(name).get_buffer()
                                for name in sorted_output_names
                            ],
                            axis=2,
                        )
                        logits = self._apply_repetition_penalty(
                            np.squeeze(decoder_outputs[:, i], axis=0), generated_tokens
                        )
                        next_token = int(np.argmax(logits))
                        if next_token == self.tokenizer.eot:
                            break
                        generated_tokens.append(next_token)
                        decoder_input_ids[0, i + 1] = next_token

        return self.tokenizer.decode(generated_tokens).strip()

    def _tokenization(self, decoder_input_ids: np.ndarray) -> np.ndarray:
        gather_output = self.token_embedding_weight[decoder_input_ids]
        add_output = gather_output + self.onnx_add_input
        unsqueeze_output = np.expand_dims(add_output, axis=1)
        return np.ascontiguousarray(
            np.transpose(unsqueeze_output, (0, 2, 1, 3)).astype(np.float32)
        )

    @staticmethod
    def _apply_repetition_penalty(
        logits: np.ndarray, generated_tokens: list[int], penalty: float = 1.5, last_window: int = 8
    ) -> np.ndarray:
        logits = np.array(logits, copy=True)
        recent_tokens = set(generated_tokens[-last_window:])
        for token in recent_tokens:
            if 0 <= token < len(logits) and token not in {11, 13}:
                logits[token] /= penalty
        return logits

    @staticmethod
    def _clean_transcription(transcription: str) -> str:
        text = " ".join(transcription.split()).strip()
        if not text:
            return ""
        sentences: list[str] = []
        for sentence in text.split("."):
            sentence = sentence.strip()
            if not sentence:
                continue
            normalized = sentence.lower()
            if any(
                normalized in existing.lower() or existing.lower() in normalized
                for existing in sentences
            ):
                break
            sentences.append(sentence)
        cleaned = ". ".join(sentences).strip()
        if cleaned and cleaned[-1] not in ".?":
            cleaned += "."
        return cleaned


class HailoWakeRuntime(HailoTranscriptionRuntime):
    asset_kind = "wake"

    def transcribe_wake_audio(self, audio_float) -> str:
        audio = np.asarray(audio_float, dtype=np.float32).flatten()
        return " ".join(self._transcribe_audio_chunks(audio)).lower().strip()
