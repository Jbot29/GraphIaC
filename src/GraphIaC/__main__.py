import argparse
import importlib.util
import os
import sqlite3

import boto3

import GraphIaC

from .logs import setup_logger

logger = setup_logger()

# python -m GraphIaC <profile> --infra_file infra.py plan
# python -m GraphIaC <profile> --infra_file site.giac plan


def load_user_infra_module(file_path):
    # Dynamically import the user's infrastructure definition file
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_infra(gioc, path):
    """Populate state from an infra file — a Python module or a .giac DSL
    source (see dsl/spec.md). Returns the list of BLOCKED items (always
    empty for Python infra, which does its own phase logic)."""
    if path.endswith(".giac"):
        from GraphIaC import dsl

        with open(path) as f:
            res = dsl.parse(f.read())
        for w in res["warnings"]:
            logger.warning(f"{path}:{w['line']}: {w['msg']}")
        if res["errors"]:
            for e in res["errors"]:
                logger.error(f"{path}:{e['line']}: {e['msg']}")
            raise SystemExit(1)
        try:
            return dsl.load_graph(gioc, res["graph"], base_dir=os.path.dirname(os.path.abspath(path)))
        except FileNotFoundError as e:
            logger.error(f"{path}: {e}")
            raise SystemExit(1) from None

    module = load_user_infra_module(path)
    module.infra(gioc)
    return []


def main():
    parser = argparse.ArgumentParser(description="Infrastructure tool")

    parser.add_argument("profile", help="Aws Profile to use")
    parser.add_argument("--infra_file", help="Path to the infrastructure definition (.py or .giac)")
    parser.add_argument("--port", type=int, default=8642, help="Port for the serve command")
    parser.add_argument(
        "command",
        choices=["plan", "run", "diagram", "verify", "serve"],
        help="The command to run (e.g., plan, run, verify, serve)",
    )

    args = parser.parse_args()

    if not args.infra_file:
        print("Infra file needed")
        return

    session = boto3.session.Session(profile_name=args.profile)

    if args.command == "serve":
        # the editor UI owns the source from here — nothing is pre-loaded
        from GraphIaC.server import serve

        if not args.infra_file.endswith(".giac"):
            print("serve works with .giac infra files")
            return
        serve(session, args.infra_file, port=args.port)
        return

    base = os.path.splitext(args.infra_file)[0]
    db_conn = sqlite3.connect(base + ".db")

    gioc = GraphIaC.init(session, db_conn)
    blocked = load_infra(gioc, args.infra_file)

    if args.command == "plan":
        logger.plan("Plan")
        changes = GraphIaC.plan(gioc, blocked)
        logger.info("Changes:")
        for change in changes:
            logger.info(f"\tChange: {change.operation} {change.obj}")

    elif args.command == "run":
        GraphIaC.run(gioc, blocked)

    elif args.command == "verify":
        failed = GraphIaC.verify(gioc)
        raise SystemExit(1 if failed else 0)

    elif args.command == "diagram":
        print("Diagram")
        print(gioc.G)
        GraphIaC.export_graph(gioc, base)

    else:
        print(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
