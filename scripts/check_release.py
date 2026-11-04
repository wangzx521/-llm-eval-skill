"""Verify the exact public source allowlist and skill references before publication."""

import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {"eval-gpqa", "eval-gsm8k", "eval-math500", "eval-ceval", "eval-humaneval", "eval-ifeval"}
ROOT_FILES = {".gitignore", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "source-migration.json"}
FOLDERS = {"src", "skills", "docs", "licenses", "examples", "tests", "scripts", ".github"}
IGNORED = {".git", "build", "dist", "__pycache__", "llm_eval_skills.egg-info"}


def release_files():
    files = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED for part in relative.parts):
            continue
        if not path.is_file():
            continue
        assert not path.is_symlink(), f"Symlink forbidden {relative}"
        assert (len(relative.parts) == 1 and relative.name in ROOT_FILES) or relative.parts[0] in FOLDERS, f"Unexpected file {relative}"
        assert path.suffix in {".md", ".py", ".toml", ".json", ".jsonl", ".txt", ".yml"} or relative.name in {"LICENSE", ".gitignore"}, f"Unexpected type {relative}"
        text = path.read_text(encoding="utf-8")
        for pattern in (r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----", r"gh[pousr]_[A-Za-z0-9]{30,}", r"sk-[A-Za-z0-9]{30,}", r"hf_[A-Za-z0-9]{30,}"):
            assert not re.search(pattern, text), f"Credential-shaped text in {relative}"
        assert "/home/" + "dsh/" not in text and "GPU" + "-" not in text, f"Private historical machine identity in {relative}"
        assert "C:\\Users\\" + "25465" not in text, f"Private workstation path in {relative}"
        files.append(path)
    return files


def main():
    assert {path.name for path in (ROOT / "skills").iterdir()} == SKILLS
    for name in sorted(SKILLS):
        folder = ROOT / "skills" / name
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith(f"---\nname: {name}\ndescription: ")
        assert (folder / "scripts/evaluate.py").is_file()
        assert (folder / "references/protocol.md").is_file()
        for reference in re.findall(r"\]\(([^)]+)\)", text):
            if "://" not in reference:
                assert (folder / reference).is_file(), reference
    files = release_files()
    manifest = [{"path": p.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for p in files]
    print(json.dumps({"skills": len(SKILLS), "files": manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
