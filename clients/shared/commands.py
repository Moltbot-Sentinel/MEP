import shlex


def parse_task_args(text: str, default_bounty: float, default_model: str):
    tokens = shlex.split(text)
    bounty = default_bounty
    model = default_model
    target = None
    payload_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("--bounty", "-b") and i + 1 < len(tokens):
            i += 1
            bounty = float(tokens[i])
        elif token == "--model" and i + 1 < len(tokens):
            i += 1
            model = tokens[i]
        elif token == "--target" and i + 1 < len(tokens):
            i += 1
            target = tokens[i]
        else:
            payload_parts.append(token)
        i += 1
    payload = " ".join(payload_parts).strip()
    return payload, bounty, model, target
