"""Bounded shell words, statements and redirections, never an interpreter."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PureWindowsPath


class CommandSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class ShellCommands:
    commands: tuple[tuple[str, ...], ...]
    redirects: tuple[str, ...]


def executable_name(value: str) -> str:
    return PureWindowsPath(value).name.casefold().removesuffix(".exe")


def shell_commands(text: str, *, depth: int = 0) -> ShellCommands:
    if depth > 4:
        raise CommandSyntaxError("shell wrapper nesting exceeds the supported literal grammar")
    statements, redirects, words = [], [], []
    word, index, redirect, started = "", 0, False, False

    def flush_word():
        nonlocal word, redirect, started
        if started or word:
            if redirect:
                redirects.append(word)
                redirect = False
            else:
                words.append(word)
            word = ""
            started = False

    def flush_statement():
        flush_word()
        if redirect:
            raise CommandSyntaxError("redirection target is missing")
        if words:
            statements.append(tuple(words))
            words.clear()

    while index < len(text):
        char = text[index]
        if text[index : index + 2] == "<#" and not word:
            end = text.find("#>", index + 2)
            if end == -1:
                raise CommandSyntaxError("unterminated block comment")
            index = end + 2
            continue
        if text[index : index + 2] in {"@'", '@"'} and not word:
            quote = text[index + 1]
            end = re.search(r"(?m)^[ \t]*" + re.escape(quote) + "@", text[index + 2 :])
            if end is None:
                raise CommandSyntaxError("unterminated here-string")
            if quote == '"' and "$(" in text[index + 2 : index + 2 + end.start()]:
                raise CommandSyntaxError("here-string command interpolation is unresolved")
            word = "<literal-here-string>"
            index += 2 + end.end()
            continue
        if text[index : index + 2] == "<<":
            flush_word()
            marker = re.match(r"<<-?[ \t]*(['\"]?)([A-Za-z_][\w]*)\1", text[index:])
            if marker is None:
                raise CommandSyntaxError("nonliteral here-document delimiter")
            body = text.find("\n", index + marker.end())
            if body == -1:
                raise CommandSyntaxError("here-document body is missing")
            end = re.search(r"(?m)^[\t]*" + re.escape(marker[2]) + r"[\r]?$", text[body + 1 :])
            if end is None:
                raise CommandSyntaxError("unterminated here-document")
            tail = shell_commands(text[index + marker.end() : body], depth=depth + 1)
            redirects.extend(tail.redirects)
            flush_statement()
            statements.extend(tail.commands)
            index = body + 1 + end.end()
            continue
        if char in "'\"":
            quote = char
            started = True
            index += 1
            while index < len(text):
                char = text[index]
                if char == quote:
                    if quote == "'" and text[index : index + 2] == "''":
                        word += "'"
                        index += 2
                        continue
                    index += 1
                    break
                if quote == '"' and text[index : index + 2] == "$(":
                    raise CommandSyntaxError("quoted command interpolation is unresolved")
                if (
                    quote == '"'
                    and char in {"`", "\\"}
                    and index + 1 < len(text)
                    and text[index + 1] == quote
                ):
                    word += quote
                    index += 2
                else:
                    word += char
                    index += 1
            else:
                raise CommandSyntaxError("unterminated quoted argument")
            continue
        if char == "#" and not word:
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        if char in " \t\r":
            flush_word()
        elif char in ";\n|&":
            if char == "&" and not word and not words:
                index += 1  # PowerShell's literal invocation operator.
                continue
            flush_statement()
        elif char == ">":
            if word.isdigit():
                word = ""
            flush_word()
            while index + 1 < len(text) and text[index + 1] == ">":
                index += 1
            if index + 1 < len(text) and text[index + 1] == "&":
                index += 2
                while index < len(text) and text[index].isdigit():
                    index += 1
                continue
            redirect = True
        elif char == "`" and index + 1 < len(text):
            index += 1
            word += text[index]
        elif char in "(){}":
            raise CommandSyntaxError(
                "shell grouping or substitution is outside the literal grammar"
            )
        else:
            word += char
        index += 1
    flush_statement()
    commands = []
    for statement in statements:
        program = executable_name(statement[0])
        if statement[0].startswith("$") and statement[1:2] == ("=",):
            # A literal data assignment stays data; an explicit command on the RHS
            # must receive the same screening as a standalone invocation.
            if len(statement) > 2 and executable_name(statement[2]) in {
                "git", "cmd", "powershell", "pwsh", "bash", "sh", "iex", "invoke-expression",
                "remove-item", "rm", "rmdir", "rd", "del", "erase", "docker", "podman",
                "docker-compose", "podman-compose",
            }:
                statement = statement[2:]
                program = executable_name(statement[0])
        elif any(char in statement[0] for char in "$%"):
            raise CommandSyntaxError("command entry requires unresolved shell expansion")
        if program in {"cmd", "powershell", "pwsh", "bash", "sh"}:
            options = {"/c", "/k"} if program == "cmd" else {"-c", "-command"}
            position = next(
                (i for i, value in enumerate(statement[1:], 1) if value.casefold() in options), None
            )
            if position is None or position + 1 == len(statement):
                raise CommandSyntaxError(
                    "shell wrapper command is not a literal supported argument"
                )
            parts = statement[position + 1 :]
            if program in {"bash", "sh"}:
                # Following arguments become $0/$1, not additional shell source.
                parts = parts[:1]
            if len(parts) > 1 and re.search(r"\s", parts[0]) and not parts[0].endswith(".exe"):
                raise CommandSyntaxError("wrapper mixes a script argument and additional words")
            nested = shell_commands(
                parts[0] if len(parts) == 1 else shlex.join(parts), depth=depth + 1
            )
            commands.extend(nested.commands)
            redirects.extend(nested.redirects)
        elif program in {"iex", "invoke-expression"}:
            raise CommandSyntaxError(
                "evaluated shell expressions have no verifiable literal target"
            )
        else:
            commands.append(statement)
    return ShellCommands(tuple(commands), tuple(redirects))
