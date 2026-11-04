"""Standalone IFEval skill entrypoint. Requires installed shared package."""
import sys

try:
    from llm_eval_skills.cli import main
except ImportError:
    raise SystemExit("Install the shared llm-eval-skills[scoring] package first")


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and arguments[0] in ("prepare", "run", "resume", "score"):
        arguments = [arguments[0], "--test", "ifeval", *arguments[1:]]
    raise SystemExit(main(arguments))
