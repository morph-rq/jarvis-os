"""Spoken text: nothing technical reaches a voice, tags only where they are performed."""

import re

import pytest

from jarvis.speech import ACKS, SHOWN, Acks, TagBudget, for_speech, strip_tags, voice_style

TECH = re.compile(r"```|def |import |\{|\}|\$ |/home/|~/|https?://|Traceback|File \"|sk_|\bjson\b|subprocess|--\w|=|;|\|",
                  re.I)


def spoken(text, lang="en", **kw):
    out = for_speech(text, lang, **kw)
    assert not TECH.search(out), f"technical text would be spoken: {out!r}"
    return out


def test_plain_reply_is_unchanged():
    assert spoken("I've made the changes. Everything passed the tests.") == \
        "I've made the changes. Everything passed the tests."


def test_code_block_is_never_spoken():
    reply = "Here's the script:\n```python\nimport subprocess\nsubprocess.run(['ls', '--output'])\n```\nIt lists the folder."
    assert spoken(reply) == "Here's the script. It lists the folder. " + SHOWN["en"]


def test_a_hundred_lines_of_code_become_one_sentence():
    code = "\n".join(f"def f{i}(x):\n    return x * {i}" for i in range(50))
    assert spoken(f"I wrote the module:\n```\n{code}\n```") == "I wrote the module. " + SHOWN["en"]


def test_unfenced_code_and_shell_lines_are_dropped():
    reply = "Done. Run this:\n$ jarvis-workos config set voice.tts elevenlabs\nsudo systemctl restart jarvisd\nThat's all."
    assert spoken(reply) == "Done. Run this. That's all. " + SHOWN["en"]


def test_traceback_is_explained_not_read():
    reply = ("The upload failed because the server refused the login.\nTraceback (most recent call last):\n"
             '  File "/usr/lib/python3.14/site-packages/x.py", line 12, in run\nPermissionError: [Errno 13] denied')
    assert spoken(reply) == "The upload failed because the server refused the login. " + SHOWN["en"]


def test_json_and_tool_payloads():
    assert spoken('{"ok": true, "results": ["/home/roman/a.txt"], "count": 1}') == SHOWN["en"]
    assert spoken('Result:\n{"status": "awaiting_user_approval", "tool": "run_shell"}') == "Result. " + SHOWN["en"]


def test_log_lines():
    reply = "2026-10-24 09:29:45,434 jarvis.audio WARNING microphone lost\nINFO: started\nThe microphone reconnected."
    assert spoken(reply) == "The microphone reconnected. " + SHOWN["en"]


def test_markdown_lists_and_emphasis_read_as_sentences():
    reply = "## Today\n- **Standup** at 10\n- Lunch with Anna\n1. Call the bank"
    assert spoken(reply) == "Today Standup at 10. Lunch with Anna. Call the bank."


def test_links_paths_and_secrets():
    out = spoken("Saved to /home/roman/Documents/report.pdf, see https://example.com/x?y=1 and [the docs](https://d.io).")
    assert out == "Saved to report.pdf, see a link and the docs."
    out = spoken("Your key is sk_test_0fake0key0for0tests0only0000000000000000 and it works.")
    assert "0fake0key" not in out and out.endswith(SHOWN["en"])


def test_inline_code_keeps_plain_names_only():
    assert spoken("Opened `Firefox` and ran `rm -rf ~/tmp/x`.") == "Opened Firefox and ran. " + SHOWN["en"]
    assert spoken("Run it with `python3 list.py --all` and it prints your files.") == \
        "Run it with and it prints your files. " + SHOWN["en"]
    assert spoken("Opened `Visual Studio Code` and `report.pdf`.") == "Opened Visual Studio Code and report.pdf."


def test_identifiers_are_not_spoken():
    assert spoken("Changed cfg_voice.tts and RouterBrain.speak().\nAll good.") == "All good. " + SHOWN["en"]


def test_long_answers_are_cut_with_a_pointer_to_the_screen():
    long = " ".join(f"This is sentence number {i} of a very long explanation." for i in range(40))
    out = spoken(long)
    assert len(out) < 480 and out.endswith("The rest is on your screen.")


@pytest.mark.parametrize("lang,reply,expected", [
    ("ru", "Готово, вот код:\n```bash\nls -la\n```", "Готово, вот код. " + SHOWN["ru"]),
    ("fr", "C'est fait.\n```\nprint('x')\n```", "C'est fait. " + SHOWN["fr"]),
    ("ru", "Нашёл 2:\n1. /home/roman/отчёт.docx\n2. /home/roman/план.xlsx", "Нашёл 2. отчёт.docx. план.xlsx."),
])
def test_languages(lang, reply, expected):
    assert spoken(reply, lang) == expected


def test_tags_removed_for_engines_that_would_read_them():
    assert spoken("[laughs] Oh, that's a good one. [whispers] Really.") == "Oh, that's a good one. Really."
    assert strip_tags("[sighs] Okay [1]") == "Okay"


def test_tags_kept_rationed_and_varied_for_eleven():
    budget = TagBudget()
    first = spoken("[laughs] Ha. [excited] Great! [whispers] Psst. [shouts] HEY [evil cackle] no", tags=budget)
    assert first == "[laughs] Ha. [excited] Great! Psst. HEY no"
    second = spoken("[laughs] Again? [sighs] Fine.", tags=budget)
    assert second == "Again? [sighs] Fine."   # last reply's tag is not repeated
    assert spoken("[laughs] Okay.", tags=TagBudget()) == "[laughs] Okay."


def test_a_tag_line_is_not_mistaken_for_code():
    assert spoken("[chuckles] Sure.", tags=TagBudget()) == "[chuckles] Sure."


def test_nothing_left_to_say():
    assert for_speech("") == ""
    assert spoken("```\nx = 1\n```", "ru") == SHOWN["ru"]


def test_acks_never_repeat_back_to_back():
    acks = Acks(seed=1)
    said = [acks.pick("ru") for _ in range(30)]
    assert all(a != b for a, b in zip(said, said[1:]))
    assert set(said) <= set(ACKS["ru"])
    assert acks.pick("de") in ACKS["en"]


def test_voice_style_mentions_tags_only_when_expressive():
    assert "[laughs]" in voice_style(True) and "[laughs]" not in voice_style(False)
    assert "French" in voice_style(False)
