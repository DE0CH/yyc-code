# Background briefs

Each stream is a topic of two parts (Deyao, 2026-10-23): its **definition**, what the topic means (`streams.json`
`brief`, one or two sentences, no rules), and its **background**, one short brief per stream here, `<stream id>.md` (about 4,000 characters: names, people,
current affairs, easily confused things; Deyao, 2026-10-28: cost). `sources/<stream id>.md` is the long sourced
original each short brief was condensed from ([n] citations and a source list); the scorer never reads it. The scorer (`streams.py` `topic_blocks()`) puts each topic in
every scoring prompt as `<topic>` with `<definition>` and `<background>` (citations and sources stripped) and asks how
relevant each post is to it, per the shared rubric. A stream without a file has the definition alone.

Every sourced original is written from `prompts/TEMPLATE.txt` with only the topic line changed (`prompts/<id>.txt`), one fresh
agent per stream (Deyao reviewed the first set on 2026-10-23 and said later ones need no review). The topics
block is the same in every scoring call, so it is sent as a prompt-cache prefix (`ai.Prompt`): billed at a tenth of
the input price on calls within minutes of each other. Editing a brief changes `defs_hash`, so past posts
become due again for the score backfill, which runs only when switched on by hand (kv `score_backfill`).
