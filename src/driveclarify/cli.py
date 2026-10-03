"""Single command entry for evaluation, models and native experiments."""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="driveclarify", description=__doc__)
    parser.add_argument("command", choices=["evaluate", "assets", "prepare", "benchmark"])
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    args = parser.parse_args(argv[:1])
    remaining = argv[1:]
    if args.command == "evaluate":
        from .evaluation import main as run
    elif args.command == "assets":
        from .tools.assets import main as run
    elif args.command == "prepare":
        from .tools.prepare import main as run
    else:
        from .tools.benchmark import main as run
    return run(remaining)
