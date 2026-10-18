"""CPU recognizer adapters used by the dictation pipeline."""
import os
from types import SimpleNamespace

DEFAULT_MODEL = "phonon-2"


class PhononModel:
    def __init__(self, n_threads=4):
        os.environ.update(CUDA_VISIBLE_DEVICES="", FERMION_DEVICE="cpu",
                          FERMION_CPU_THREADS=str(n_threads), OMP_NUM_THREADS=str(n_threads),
                          OMP_WAIT_POLICY="PASSIVE")
        import torch
        import fermion
        torch.set_num_threads(n_threads)
        if torch.get_num_interop_threads() != 1:
            torch.set_num_interop_threads(1)
        self.speech = fermion.load_speech("phonon-2")

    def transcribe(self, audio, *, token_timestamps=False, **kwargs):
        result = self.speech.transcribe_array_detailed(audio)
        if token_timestamps:
            return [SimpleNamespace(text=w["text"], t0=w["start"] * 100, t1=w["end"] * 100)
                    for w in result.words]
        return [SimpleNamespace(text=result.text, t0=0, t1=len(audio) / 160)]


def load_model(model=DEFAULT_MODEL, *, n_threads=4, **kwargs):
    if str(model).lower() in {"phonon-2", "fermionresearch/phonon-2"}:
        return PhononModel(n_threads=n_threads)
    # Whisper remains available when an explicit ggml model is selected.
    from pywhispercpp.model import Model
    return Model(model=model, n_threads=n_threads, **kwargs)
