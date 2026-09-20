"""The only module that talks to a language model.

Prompts live in `prompts/` as Markdown files with a version header, so they
can be read and edited without touching code. The version travels with every
score: changing a prompt must not silently reuse answers produced by the old
one. Rendering uses string.Template rather than f-strings or .format() —
company text is data, and neither `{}` nor `{{` in it can then be mistaken
for a placeholder.
"""

from functools import lru_cache
from pathlib import Path
from string import Template

from company_reach.errors import PromptError

_PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """Return (version, template text) for `prompts/<name>.md`."""
    path = _PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"no prompt file for {name!r} at {path}")
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise PromptError(f"{path.name} has no version header")
    header, _, body = raw[4:].partition("\n---\n")
    version = ""
    for line in header.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "version":
            version = value.strip()
    if not version:
        raise PromptError(f"{path.name} header has no version")
    return version, body.strip()


def render(name: str, **variables: str) -> tuple[str, str]:
    """Return (version, rendered text). Missing variables are an error, not
    an empty string: a prompt silently missing its goal would score nothing
    sensible and nobody would notice."""
    version, template = load_prompt(name)
    try:
        return version, Template(template).substitute(**variables)
    except KeyError as e:
        raise PromptError(f"prompt {name!r} needs variable {e.args[0]!r}") from e
