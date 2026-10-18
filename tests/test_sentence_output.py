"""Sentence output ordering and failure handling without keyboard access."""
import threading
import unittest

from sentence_output import SentenceOutput, sentence_end


class SentenceOutputTests(unittest.TestCase):
    def make_output(self, type_text=None, **kwargs):
        self.typed = []
        self.cancelled = threading.Event()
        output = SentenceOutput(type_text or self.typed.append, self.cancelled, **kwargs)
        self.addCleanup(lambda: output.close(discard=True))
        return output

    def test_completed_sentences_type_before_release_and_tail_only_once(self):
        sent = threading.Event()
        received = []
        def type_text(text):
            received.append(text)
            sent.set()
        output = self.make_output(type_text)
        output.update("First sentence. An unfinished")
        self.assertTrue(sent.wait(2))
        self.assertEqual(received, ["First sentence."])
        self.assertEqual(output.pending, "An unfinished")
        output.update("First sentence. An unfinished sentence! Last")
        output.finish("First sentence. An unfinished sentence! Last sentence.")
        self.assertEqual(received, ["First sentence.", " An unfinished sentence!", " Last sentence."])

    def test_repeated_stable_updates_do_not_duplicate_text(self):
        output = self.make_output()
        output.update("Done. A tail")
        output.update("Done. A tail")
        output.finish("Done. A tail")
        self.assertEqual("".join(self.typed), "Done. A tail")

    def test_unsent_tail_can_acquire_punctuation_at_the_next_span(self):
        output = self.make_output()
        output.update("First sentence. A tail")
        output.update("First sentence. A tail. Another")
        output.finish("First sentence. A tail. Another sentence.")
        self.assertEqual("".join(self.typed), "First sentence. A tail. Another sentence.")

    def test_word_count_fallback_keeps_a_tail_without_punctuation(self):
        output = self.make_output(word_limit=4)
        output.update("one two three four five six seven eight nine")
        self.assertEqual(output.pending, "nine")
        self.assertEqual(output.chunks, 2)
        output.finish("one two three four five six seven eight nine ten")
        self.assertEqual(self.typed, ["one two three four", " five six seven eight", " nine ten"])

    def test_zero_word_limit_waits_for_punctuation_or_release(self):
        output = self.make_output(word_limit=0)
        text = " ".join(["word"] * 100)
        output.update(text)
        self.assertEqual(output.chunks, 0)
        output.finish(text)
        self.assertEqual(self.typed, [text])

    def test_abbreviations_decimal_numbers_and_closing_quotes(self):
        text = 'Dr. Smith paid 3.14 dollars in the U.S. office. "Really?" Yes!'
        output = self.make_output()
        output.finish(text)
        self.assertEqual(self.typed, ['Dr. Smith paid 3.14 dollars in the U.S. office.',
                                     ' "Really?"', ' Yes!'])
        self.assertIsNone(sentence_end("An unfinished sentence"))

    def test_cancellation_skips_queued_chunks(self):
        started, finish = threading.Event(), threading.Event()
        received = []
        def type_text(text):
            received.append(text)
            started.set()
            finish.wait(2)
        output = self.make_output(type_text)
        output.update("First. Second. Third.")
        self.assertTrue(started.wait(2))
        self.cancelled.set()
        finish.set()
        output.close()
        self.assertEqual(received, ["First."])

    def test_typing_failure_is_reported_without_retrying_or_sending_later_chunks(self):
        received = []
        def type_text(text):
            received.append(text)
            raise RuntimeError("Keyboard unavailable")
        output = self.make_output(type_text)
        with self.assertRaisesRegex(RuntimeError, "Keyboard unavailable"):
            output.finish("First. Second.")
        self.assertEqual(received, ["First."])

    def test_revised_settled_text_is_rejected(self):
        output = self.make_output()
        output.update("Take the train.")
        with self.assertRaisesRegex(RuntimeError, "revised text"):
            output.finish("Take the plane.")


if __name__ == "__main__":
    unittest.main()
