#!/usr/bin/env python3
"""pin-image.py: read or set one image's newTag in apps/kustomization.yaml, by its placeholder name, leaving the other
images' pins alone; PIN_FILE=PATH: that kustomization instead (the news-stream, site and yyc-mcp image workflows each pin their own; a whole-file edit would let
one build move another's tag).

  deploy/pin-image.py NAME            print NAME's current tag
  deploy/pin-image.py NAME TAG        set it (exit 0 also when it already was TAG)
"""
import os, re, sys

# PIN_FILE: another kustomization with the same images: block (clusters/talos/kustomization.yaml for yyc-socks)
F = os.environ.get("PIN_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps", "kustomization.yaml")


def block(s, name):
    m = re.search(r"(?m)^  - name: " + re.escape(name) + r"\n(?:    \S.*\n)*?    newTag: *'?([\w.-]*)'?\n", s)
    if not m:
        sys.exit(f"no image {name} in {F}")
    return m


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    s = open(F).read()
    m = block(s, sys.argv[1])
    if len(sys.argv) == 2:
        print(m.group(1))
        return
    tag = sys.argv[2]
    if not re.fullmatch(r"[\w.-]+", tag):
        sys.exit(f"not a tag: {tag}")
    start, end = m.span(1)
    q = s[start - 1] == "'"
    s = s[:start] + tag + s[end:] if q else s[:m.start(1)] + f"'{tag}'" + s[m.end(1):]
    open(F, "w").write(s)


if __name__ == "__main__":
    main()
