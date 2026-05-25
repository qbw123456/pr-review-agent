"""s03: three-gate permission pipeline before tool execution."""

from .config import WORKDIR

# Gate 1: hard deny — always blocked (bash only)
DENY_LIST = [
    "rm -rf /",
    "sudo",
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=",
    "> /dev/sda",
    "> /dev/",
]

# Gate 2: risky bash patterns (PR review: git destructive, rm, etc.)
BASH_RISKY_KEYWORDS = [
    "rm ",
    "chmod 777",
    "chmod -R",
    "> /etc/",
    "git push",
    "git reset --hard",
    "git clean -fd",
    "git checkout --",
]


def check_deny_list(command: str) -> str | None:
    for pattern in DENY_LIST:
        if pattern in command:
            return f"Blocked: '{pattern}' is on the deny list"
    return None


def _path_outside_workspace(path: str) -> bool:
    try:
        return not (WORKDIR / path).resolve().is_relative_to(WORKDIR)
    except (OSError, ValueError):
        return True


def check_rules(tool_name: str, args: dict, *, review_mode: bool) -> str | None:
    if review_mode and tool_name in ("write_file", "edit_file"):
        return "Repository modifications disabled during review"

    if tool_name in ("write_file", "edit_file"):
        if _path_outside_workspace(args.get("path", "")):
            return "Writing outside workspace"

    if tool_name == "bash":
        command = args.get("command", "")
        if any(kw in command for kw in BASH_RISKY_KEYWORDS):
            return "Potentially destructive command"

    return None


def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"\n\033[33m⚠  {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"


def check_permission(block, *, interactive: bool, review_mode: bool) -> bool:
    """Return True if the tool call may execute."""
    if block.name == "bash":
        reason = check_deny_list(block.input.get("command", ""))
        if reason:
            print(f"\n\033[31m⛔ {reason}\033[0m")
            return False

    reason = check_rules(block.name, block.input, review_mode=review_mode)
    if reason:
        auto_deny = not interactive or (
            review_mode
            and block.name in ("write_file", "edit_file", "bash")
        )
        if auto_deny:
            print(f"\n\033[33m⚠  {reason} — denied\033[0m")
            return False
        decision = ask_user(block.name, block.input, reason)
        if decision == "deny":
            return False

    return True
