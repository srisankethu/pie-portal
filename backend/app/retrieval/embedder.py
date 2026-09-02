"""Text to a sparse feature vector, deterministically.

A hashed n-gram embedder in the fastText/Vowpal sense: each word token and
each character n-gram of the normalised text is hashed into one of ``dim``
buckets, and the vector is the count per bucket. No vocabulary is learned, so
the model has no state to version beyond its id — the same text embeds the
same way on every machine, in every process, forever.

The hash is ``zlib.crc32`` and not the builtin ``hash``: Python salts string
hashes per process (``PYTHONHASHSEED``), so an index built with ``hash`` would
be unreadable by the next process to load it. ``test_retrieval`` runs the
embedder in a subprocess with a different seed to keep it that way.

Why character n-grams for tool nomenclature: a customer writes ``CNMG120408``,
``CNMG 120408`` and ``CNMG-12-04-08`` for one insert, and a grade appears as
``KCP25``, ``KCP 25`` or inside ``CNMG120408-MP KCP25``. Splitting on the
letter/digit boundary before hashing makes the first three one token sequence,
and n-grams over the joined tokens make the partial overlaps count.
"""
from __future__ import annotations

import math
import re
import zlib
from functools import lru_cache
from typing import Dict, List, Tuple

#: A run of letters or a number (decimal point kept). The letter/digit split is
#: the normalisation: ``CNMG120408`` and ``CNMG 120408`` yield the same tokens.
_TOKEN = re.compile(r"[A-Z]+|\d+(?:\.\d+)?")
#: ``0,8`` written by a European keyboard is ``0.8``; the comma between two
#: digits is the only one rewritten, so a list separator stays a separator.
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def tokens(text: str) -> List[str]:
    """The normalised tokens of ``text``: upper-cased, decimal comma as point,
    letters and digits split apart, punctuation gone."""
    return _TOKEN.findall(_DECIMAL_COMMA.sub(".", (text or "").upper()))


class HashedNgramEmbedder:
    """Hashed word + character-n-gram features. Stateless and deterministic.

    ``model_id`` names the feature scheme, and it is what an index is stamped
    with. Changing anything below that alters the features — the n-gram sizes,
    the weights, the tokeniser, the hash — is a new model and needs a new id,
    or an index on disk would be read as if it had been built by this code.
    """

    model_id = "hashed-ngram/1"
    dim = 1 << 18
    ngram_sizes = (3, 4)
    #: A whole token matching (a grade name, a series, a diameter) is stronger
    #: evidence than any one of the ~150 n-grams a description yields, and the
    #: weight is what keeps ``KCP25`` from drowning in the n-grams of the
    #: designation around it.
    word_weight = 3.0

    def features(self, text: str) -> Dict[int, float]:
        """Bucket -> raw feature weight for ``text``. Empty for empty text."""
        out: Dict[int, float] = {}
        for tok in tokens(text):
            for bucket, weight in _token_features(tok, self.ngram_sizes,
                                                  self.word_weight, self.dim):
                out[bucket] = out.get(bucket, 0.0) + weight
        return out

    @staticmethod
    def scale(count: float) -> float:
        """Sublinear term frequency: the fifth ``MM`` in a description is not
        five times the evidence of the first."""
        return 1.0 + math.log(count) if count > 0 else 0.0


@lru_cache(maxsize=1 << 16)
def _token_features(tok: str, sizes: Tuple[int, ...], word_weight: float,
                    dim: int) -> Tuple[Tuple[int, float], ...]:
    """The buckets one token contributes: its whole-word bucket and the
    character n-grams of the token padded with boundary spaces — subword
    features in the fastText sense, within the token rather than across them.

    Memoised because a catalogue repeats its vocabulary enormously ("INSERT",
    "MILLING", "CNMG" thousands of times each), and hashing every n-gram of
    every occurrence made a 6,700-record build take nine seconds; per-token
    memoisation makes it under two. Keyed on everything that determines the
    result so a second embedder configuration cannot read the first's cache.
    """
    mask = dim - 1
    out = [(zlib.crc32(("w:" + tok).encode("utf-8")) & mask, word_weight)]
    padded = " " + tok + " "
    for n in sizes:
        prefix = f"c{n}:"
        for i in range(len(padded) - n + 1):
            out.append((zlib.crc32((prefix + padded[i:i + n]).encode("utf-8")) & mask,
                        1.0))
    return tuple(out)
