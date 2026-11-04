"""P4 final-field rule, independent of any model name."""


def extract_final(raw, thinking=False):
    choices = raw.get("choices") or []
    if not choices:
        return {"text": "", "answer_state": "missing_choice", "truncated": False}
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    truncated = choice.get("finish_reason") == "length"
    if not isinstance(content, str):
        state, final = "missing_final", ""
    elif "</think>" in content:
        final = content.split("</think>", 1)[1].strip()
        state = "ok" if final else "empty_final"
    elif "<think>" in content or (thinking and reasoning is None):
        state, final = "unclosed_or_unseparated_thinking", ""
    else:
        final = content
        state = "ok" if final.strip() else "empty_final"
    return {"text": final, "answer_state": state, "truncated": truncated}
