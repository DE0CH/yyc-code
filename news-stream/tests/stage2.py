"""Test helper for two-stage scoring (streams.confirm, Deyao 2026-10-20): a test's stub model answers stage 2 (purpose
<stage 1's>_verify, Opus) exactly as it answered stage 1, so a test about something else sees the verdicts it set up.
The stage-2 call is not left in the test's own call log. tests/test_two_stage.py tests the two stages themselves."""


def agree(fake, calls=None):
    def jsonc(prompt, purpose, *a, **k):
        k.pop("reasoning", None)  # the stage-2 model's OpenRouter options (streams.CONFIRM_OPTIONS)
        k.pop("provider", None)
        k.pop("timeout", None)
        if not purpose.endswith("_verify"):
            return fake(prompt, purpose, *a, **k)
        n = len(calls) if calls is not None else 0
        try:
            return fake(prompt, purpose[:-len("_verify")], *a, **k)
        finally:
            if calls is not None:
                del calls[n:]
    return jsonc
