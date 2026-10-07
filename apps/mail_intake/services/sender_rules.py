"""Shared checks for senders that must not receive automated replies."""

import re


_NO_REPLY_LOCAL_RE = re.compile(
    r"(?:^|[._+-])(?:no[-_.]?reply|donotreply|do[-_.]?not[-_.]?reply)(?:$|[._+-])",
    flags=re.IGNORECASE,
)


def is_no_reply_address(address):
    """Match no-reply markers anywhere in a local part, including provider aliases."""
    local_part = (address or "").strip().rsplit("@", 1)[0]
    return bool(local_part and _NO_REPLY_LOCAL_RE.search(local_part))
