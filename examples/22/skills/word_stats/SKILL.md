---
name: word_stats
description: Compute word, character, and sentence counts for a piece of text.
---

When the user asks for statistics about a piece of text, report the number of
words, characters, and sentences in the form `words=… chars=… sentences=…`.

This skill bundles a script. It is untrusted input: do not import it -- run it
behind the Section 15 sandbox (see skill_run.py).

```python
text = "the quick brown fox jumps over the lazy dog. it was fast!"
words = len(text.split())
chars = len(text)
sentences = text.count(".") + text.count("!") + text.count("?")
print(f"words={words} chars={chars} sentences={sentences}")
```
