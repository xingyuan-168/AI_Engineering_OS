"""Parse remote hosts, canonicalizing only GitHub's exact official endpoints."""

import re
from urllib.parse import urlsplit


def remote_host(remote_url: str) -> str | None:
    value = remote_url.strip()
    if re.fullmatch(r"git@[^:]+:[^/]+/[^/]+(?:\.git)?", value):
        host = value.split("@", 1)[1].split(":", 1)[0].casefold()
        return "github.com" if host == "ssh.github.com" else host
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.username not in {None, "git"}:
            return None
        if parsed.password is not None or not parsed.hostname:
            return None
        host = parsed.hostname.casefold()
        port = parsed.port
    except ValueError:
        return None
    if host == "ssh.github.com":
        if parsed.scheme != "ssh" or port not in {None, 443}:
            return None
        return "github.com"
    if host == "github.com":
        allowed_ports = {None, 443} if parsed.scheme == "https" else {None, 22}
        if port not in allowed_ports:
            return None
    return host
