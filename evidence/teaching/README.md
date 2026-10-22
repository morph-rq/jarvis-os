# Teaching: quizzes, flashcards, progress (recorded 2026-10-22)

Recorded on the headless test desktop (`omarchy/dev/headless/`): sway, the real Caelestia shell with
the Jarvis island, and a real jarvisd from this repository. No model key was configured, so every
step below ran without a model. The deck was seeded directly into jarvisd's SQLite database.

| Shot | What happened |
|---|---|
| 01 | "quiz me on my spanish flashcards" (shell IPC `ask`): router → `learn` quiz from the deck; the other cards' answers are the wrong options |
| 02 | Option D clicked with a real pointer (`headless.sh click`, a wlr virtual pointer) → island `learn` op → jarvisd marked it right and the card updated in place |
| 03 | A wrong option clicked: the right answer is shown, the dot turns red |
| 04 | Typed answers ("goodbye", "b"), then "stop the quiz": the card becomes the scorecard (per-topic bars, weak topics in amber, missed questions) |
| 05 | "Quiz my weak spots" clicked: a quiz on the weak topic built from the deck |
| 06 | "review my spanish flashcards", Show answer clicked: the back and the again/hard/good/easy grades. Good → the card moved to box 1, due in 24 h (checked in SQLite) |
| 07 | A typed flashcard answer ("dog" for *gato*): marked wrong, kept for relearning, verdict shown on the next card |
| 08 | "how am I doing with my studies": accuracy, streak, per-topic bars, decks with cards due |
| 09–10 | The same cards in the Living Workspace, in headless Chromium (Playwright). An option clicked there goes through the card's declared `tool:learn` action and is marked by jarvisd |

Not verified here: voice (Gemini Live calling `learn` while the user speaks), and questions or
cards written by a model from a subject or document. Both need an API key. The unit tests cover
those paths with a scripted model (`service/tests/test_learn.py`).
