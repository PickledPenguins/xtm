# 08 — A document marker

`---` is the habit of every YAML file people write by hand. PyYAML accepts
it and the built-in reader has no concept of a document, so the config
loads only where PyYAML is installed -- exactly the machines xtm exists to
serve.

**The fix:** delete the line. xtm's config is always one document.
