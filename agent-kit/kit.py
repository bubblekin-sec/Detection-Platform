"""Install the agent workflow kit into a project, or refresh the kit's Claude Code copy from this repo.

  python kit.py install <project> --target claude-code|copilot|both [--dry-run]
  python kit.py refresh [--check]

install copies only files that are missing. A file that already matches is SAME; a different file
in a kit location is CONFLICT, and nothing is copied until every conflict is resolved by hand.
An existing CLAUDE.md (or .claude/CLAUDE.md) is kept, since the workflow loads from .claude/rules.

refresh is for this repo's maintainers: it copies the repo's own .claude workflow files into
claude-code/ (check reports drift and exits 1). The copilot/ conversion is maintained by hand;
check also fails when its skills and agents, or their descriptions and argument hints, stop matching.
"""

import argparse
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
TARGETS = {"claude-code": KIT / "claude-code", "copilot": KIT / "copilot"}
REPO_CLAUDE = KIT.parent / ".claude"
THIRD_PARTY_SKILLS = {"interface-design"}  # installed from its own source; see README.md


def kit_files(root):
    # Markdown only, so stray files such as Thumbs.db or .DS_Store in a copied kit are never installed.
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.md") if path.is_file())


def normalized(data):
    return data.replace(b"\r\n", b"\n")


def disposition(project, target, content):
    if any(parent.exists() and not parent.is_dir() for parent in target.parents):
        return "CONFLICT"
    if target.is_symlink() or not target.parent.resolve().is_relative_to(project):
        return "CONFLICT"  # a linked file or folder would write outside the project
    if not target.exists():
        return "ADD"
    if target.is_file() and normalized(target.read_bytes()) == normalized(content):
        return "SAME"
    return "CONFLICT"


def install(project, targets, dry_run):
    if not project.is_dir():
        sys.exit("Choose an existing project directory, or create an empty one first.")
    existing_context = next(
        (path for path in (project / "CLAUDE.md", project / ".claude" / "CLAUDE.md") if path.is_file()), None
    )
    additions, conflicts = [], []
    print(f"Project: {project}")
    for name in targets:
        root = TARGETS[name]
        for relative in kit_files(root):
            if relative == "CLAUDE.md" and existing_context is not None:
                print(f"{'KEEP':8} {existing_context.relative_to(project).as_posix()}")
                continue
            content = (root / relative).read_bytes()
            target = project / relative
            action = disposition(project, target, content)
            print(f"{action:8} {relative}")
            if action == "ADD":
                additions.append((target, content))
            elif action == "CONFLICT":
                conflicts.append(relative)
    if conflicts:
        print("Merge the CONFLICT files by hand (see README.md). No files copied.", file=sys.stderr)
        return 1
    if dry_run:
        print(f"Preview complete. Files to add: {len(additions)}")
        return 0
    for target, content in additions:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(content)
    print(f"Files added: {len(additions)}")
    return 0


def repo_workflow_files():
    """The repo's own .claude workflow files that belong in the kit, as kit-relative paths."""
    files = {}
    for folder in ("agents", "rules"):
        for path in sorted((REPO_CLAUDE / folder).glob("*.md")):
            files[f".claude/{folder}/{path.name}"] = path
    for path in sorted((REPO_CLAUDE / "skills").glob("*/SKILL.md")):
        if path.parent.name not in THIRD_PARTY_SKILLS:
            files[f".claude/skills/{path.parent.name}/SKILL.md"] = path
    return files


def frontmatter(path):
    """The frontmatter fields that must match between the two versions."""
    text = path.read_text(encoding="utf-8")  # universal newlines: CRLF arrives as \n
    block = text.split("---\n", 2)[1] if text.startswith("---\n") else ""
    fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
    return {key: fields.get(key) for key in ("name", "description", "argument-hint")}


def copilot_pairs():
    """Copilot problems: a skill or agent missing on one side, or a description that differs."""
    claude, copilot = TARGETS["claude-code"] / ".claude", TARGETS["copilot"] / ".github"
    pairs = {f"skills/{p.parent.name}": (p, copilot / "skills" / p.parent.name / "SKILL.md")
             for p in (claude / "skills").glob("*/SKILL.md")}
    pairs |= {f"agents/{p.stem}": (p, copilot / "agents" / f"{p.stem}.agent.md") for p in (claude / "agents").glob("*.md")}
    problems = []
    for name, (source, twin) in sorted(pairs.items()):
        if not twin.is_file():
            problems.append(f"MISSING  copilot/.github/{twin.relative_to(copilot).as_posix()}")
        elif frontmatter(source) != frontmatter(twin):
            problems.append(f"DIFFERS  copilot/.github/{twin.relative_to(copilot).as_posix()} (name, description or argument-hint)")
    twins = {twin for _, twin in pairs.values()}
    for extra in sorted([*(copilot / "skills").glob("*/SKILL.md"), *(copilot / "agents").glob("*.agent.md")]):
        if extra not in twins:
            problems.append(f"EXTRA    copilot/.github/{extra.relative_to(copilot).as_posix()}")
    return problems


def refresh(check):
    root = TARGETS["claude-code"]
    wanted = repo_workflow_files()
    present = {relative for relative in kit_files(root) if relative.startswith(".claude/")}
    changes = []
    for relative, source in wanted.items():
        target = root / relative
        content = normalized(source.read_bytes())
        if not target.is_file() or normalized(target.read_bytes()) != content:
            changes.append(("UPDATE" if target.exists() else "ADD", relative, content))
    changes += [("REMOVE", relative, None) for relative in sorted(present - set(wanted))]
    for action, relative, _ in changes:
        print(f"{action:8} claude-code/{relative}")
    if check:
        problems = copilot_pairs() if not changes else []
        for problem in problems:
            print(problem)
        if changes:
            print("The kit's Claude Code copy differs from .claude/. Run: python agent-kit/kit.py refresh", file=sys.stderr)
            return 1
        if problems:
            print("Update the copilot/ files above by hand to match the Claude Code copy.", file=sys.stderr)
            return 1
        print("The kit matches .claude/, and every Claude Code skill and agent has a matching Copilot version.")
        return 0
    for action, relative, content in changes:
        target = root / relative
        if action == "REMOVE":
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    print(f"Changed: {len(changes)}. Update the matching copilot/ files by hand if the workflow changed.")
    for problem in copilot_pairs():
        print(problem)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="copy the kit into a project")
    install_parser.add_argument("project", type=Path, help="path to an existing project directory")
    install_parser.add_argument("--target", choices=("claude-code", "copilot", "both"), required=True,
                                help="the assistant your team uses; both can make Copilot list each skill twice")
    install_parser.add_argument("--dry-run", action="store_true", help="show the file plan without copying")
    refresh_parser = commands.add_parser("refresh", help="copy this repo's .claude workflow into claude-code/")
    refresh_parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args()
    if args.command == "install":
        targets = ("claude-code", "copilot") if args.target == "both" else (args.target,)
        return install(args.project.expanduser().resolve(), targets, args.dry_run)
    return refresh(args.check)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as error:
        sys.exit(f"Stopped: {error}")
