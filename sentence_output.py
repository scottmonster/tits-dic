"""Send settled recognition text in order while retaining an unfinished tail."""
import queue
import re
import threading


_END = re.compile(r'''[.!?]+["'”’)\]]*(?=\s|$)''')
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e"}


def sentence_end(text):
    """Find a sentence boundary, excluding common titles and initials."""
    for match in _END.finditer(text):
        punctuation = match.group().rstrip("\"'”’)]")
        if punctuation == ".":
            word = text[:match.start()].split()[-1] if text[:match.start()].strip() else ""
            if word.casefold() in _ABBREVIATIONS or re.fullmatch(r"(?:[A-Za-z]\.)*[A-Za-z]", word):
                continue
        return match.end()
    return None


class SentenceOutput:
    """A single typing worker keeps slow keyboard output out of the ASR loop."""

    def __init__(self, type_text, cancelled, *, word_limit=40, emit=lambda *args, **kwargs: None):
        self.type_text = type_text
        self.cancelled = cancelled
        self.word_limit = word_limit
        self.emit = emit
        self.committed = ""
        self.pending = ""
        self.chunks = 0
        self.error = None
        self.closed = False
        self.discard = threading.Event()
        self.queue = queue.Queue()
        self.worker = threading.Thread(target=self._run, name="sentence-typing", daemon=True)
        self.worker.start()

    def update(self, text, *, final=False):
        if self.error is not None:
            raise self.error
        # Already queued text is immutable. Unsent tails can still acquire
        # punctuation at a span boundary without duplicating any output.
        if self.committed and text != self.committed and not text.startswith(self.committed + " "):
            raise RuntimeError("Recognition revised text already settled for sentence output")
        self.pending = text[len(self.committed):].strip()
        while self.pending:
            end = sentence_end(self.pending)
            words = list(re.finditer(r"\S+", self.pending))
            if self.word_limit and len(words) > self.word_limit:
                word_end = words[self.word_limit - 1].end()
                end = min(end, word_end) if end is not None else word_end
            if end is None:
                if not final:
                    break
                end = len(self.pending)
            chunk = self.pending[:end]
            self.pending = self.pending[end:].strip()
            addition = (" " if self.chunks else "") + chunk
            self.committed += addition
            self.queue.put(addition)
            self.chunks += 1
            self.emit("sentence_queued", chunk=self.chunks, text_chars=len(chunk), final=final)

    def _run(self):
        while True:
            text = self.queue.get()
            if text is None:
                return
            if self.cancelled.is_set() or self.discard.is_set() or self.error is not None:
                continue
            try:
                self.type_text(text)
            except Exception as exc:
                self.error = exc

    def close(self, *, discard=False):
        if discard:
            self.discard.set()
        if not self.closed:
            self.closed = True
            self.queue.put(None)
        self.worker.join()

    def finish(self, text):
        self.update(text, final=True)
        self.close()
        if self.error is not None:
            raise self.error
