"""Follow-ups: what the last research in this conversation found, so the next question can build on it.

Long-term conversation context stays in Jarvis's memory (jarvis.memory history); this only keeps the last
few research results of each conversation in memory for 30 minutes: the question, the answer's opening
and the sources it cited, enough to rewrite "and the second one?" into a real query and to answer
"which of those is cheapest?" from the same sources without searching again.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

KEEP_S = 30 * 60

FOLLOW_START = re.compile(
    r"^(and|but|also|what about|how about|then|so|ok so|and what|what else|tell me more|more about|go deeper|deeper|"
    r"more detail|the (first|second|third|last|other) one|which of (them|those|these)|of those|among them|"
    r"а|и|а что|а как|тогда|ещё|еще|подробнее|какой из них|какая из них|из них|"
    r"ал|енді|тағы|толығырақ)\b", re.I)
PRONOUN = re.compile(r"\b(it|its|they|them|their|this|that|those|these|he|she|him|her|one|ones|"
                     r"он|она|оно|они|его|её|ее|их|это|этот|эта|эти|тот|та|те|ол|олар|бұл|осы)\b", re.I)
REUSE = re.compile(
    r"\b(which of (them|those|these)|of (them|those|these)|among (them|those)|the (first|second|third|last|other) one|"
    r"cheapest|most expensive|lightest|heaviest|longest|shortest|best of|rank (them|those)|sort (them|those)|"
    r"summari[sz]e (that|it|those)|какой из них|какая из них|из них|самый дешёвый|самый дешевый|сравни их)\b", re.I)
DEEPER = re.compile(r"\b(go deeper|dig deeper|more detail|in more depth|research (it|that) (more|further)|"
                    r"tell me more|подробнее|глубже|толығырақ)\b", re.I)


@dataclass
class Turn:
    query: str
    effective_query: str
    mode: str
    answer: str
    sources: list[dict]
    at: float = field(default_factory=time.time)
    lang: str = "en"


class Threads:
    def __init__(self, keep: int = 5) -> None:
        self.keep = keep
        self._turns: dict[str, list[Turn]] = {}

    def last(self, conversation: str) -> Turn | None:
        turns = [t for t in self._turns.get(conversation, []) if time.time() - t.at < KEEP_S]
        self._turns[conversation] = turns
        return turns[-1] if turns else None

    def add(self, conversation: str, turn: Turn) -> None:
        turns = self._turns.setdefault(conversation, [])
        turns.append(turn)
        del turns[:-self.keep]

    def forget(self, conversation: str | None = None) -> None:
        if conversation is None:
            self._turns.clear()
        else:
            self._turns.pop(conversation, None)

    def plan(self, conversation: str, query: str) -> dict:
        """How to run `query` given the conversation so far:
        {query, followup: bool, reuse_only: bool, deeper: bool, previous: {query, sources} | None}."""
        prev = self.last(conversation)
        out = {"query": query, "followup": False, "reuse_only": False, "deeper": False, "previous": None}
        if prev is None:
            return out
        words = query.split()
        followup = bool(FOLLOW_START.search(query.strip())) or (len(words) <= 7 and bool(PRONOUN.search(query)))
        if not followup:
            return out
        out["followup"] = True
        out["previous"] = {"query": prev.effective_query, "sources": prev.sources[:8]}
        if DEEPER.search(query):
            out["deeper"] = True
            out["query"] = prev.effective_query
            return out
        out["reuse_only"] = bool(REUSE.search(query)) and bool(prev.sources)
        # The search engines see a self-contained query: the earlier topic plus what is new now.
        new = FOLLOW_START.sub("", query.strip()).strip(" ,?.!") or query
        out["query"] = f"{prev.effective_query} {new}".strip() if len(new.split()) < 8 else new
        return out
