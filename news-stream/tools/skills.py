"""Skills (Agent Skills format, agentskills.io): news-stream/skills/<name>/SKILL.md with YAML frontmatter `name` and
`description`, then the instructions. A model sees only the index (name + description) and loads a skill's text with
skill_load when a question needs it (progressive disclosure); MCP clients get them as prompts and resources."""
import os, re

from . import tool

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


def _read(name):
    p = os.path.join(DIR, name, "SKILL.md")
    s = open(p, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n(.*)", s, re.S)
    if not m:
        raise ValueError(f"{p}: no frontmatter")
    meta = dict(re.findall(r"^(\w[\w-]*):\s*(.+)$", m.group(1), re.M))
    if meta.get("name") != name or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
        raise ValueError(f"{p}: name must match its folder, lowercase-hyphen, at most 64 characters")
    if not 1 <= len(meta.get("description", "")) <= 1024:
        raise ValueError(f"{p}: description must be 1-1024 characters")
    return meta, m.group(2).strip()


def index():
    """[(name, description)] of every skill"""
    return [(n, _read(n)[0]["description"]) for n in sorted(os.listdir(DIR)) if os.path.isfile(os.path.join(DIR, n, "SKILL.md"))]


def body(name):
    return _read(name)[1]


@tool("skill_load",
      "Load one of YYC's analysis skills (step-by-step instructions and the output format) before doing that analysis. "
      "The skills and what each is for are listed in your instructions.",
      {"name": {"type": "string", "description": "the skill's name, e.g. exec-summary"}}, ["name"])
def skill_load(a, ctx):
    names = [n for n, _ in index()]
    if a["name"] not in names:
        raise ValueError(f"no skill {a['name']!r}; skills: {', '.join(names)}")
    return {"name": a["name"], "instructions": body(a["name"])}
