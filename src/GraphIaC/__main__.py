import argparse
import importlib.util
import os
import sqlite3

import boto3

import GraphIaC

from .logs import setup_logger

logger = setup_logger()

# logging.basicConfig(encoding='utf-8', level=logging.INFO)
# python -m a


def load_user_infra_module(file_path):
    # Dynamically import the user's infrastructure definition file
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(profile, db_conn, user_infra_module):
    logger.info("GraphIOC: Plan")

    session = boto3.session.Session(profile_name=profile)

    gioc = GraphIaC.init(session, db_conn)

    user_infra_module.infra(gioc)

    changes = GraphIaC.plan(gioc)

    logger.info("Changes:")
    for change in changes:
        logger.info(f"\tChange: {change.operation} {change.obj}")

    return


def main():
    # Set up argument parsing
    parser = argparse.ArgumentParser(description="Infrastructure tool")

    parser.add_argument("profile", help="Aws Profile to use")
    parser.add_argument("--infra_file", help="Path to the user's infrastructure definition file")
    # parser.add_argument('version',help="Version")
    parser.add_argument(
        "command",
        choices=["plan", "run", "diagram", "verify"],
        help="The command to run (e.g., plan, run, verify)",
    )

    args = parser.parse_args()

    user_module_path = ""

    # Load the user's infrastructure file
    if args.infra_file:
        user_module_path = args.infra_file
    else:
        print("Infra file needed")
        return

    user_infra_module = load_user_infra_module(user_module_path)

    db_path = user_module_path.replace(".py", ".db")

    diagram_path = user_module_path.replace(".py", "")

    db_conn = sqlite3.connect(db_path)

    # Execute the specified command
    if args.command == "plan":
        logger.plan("Plan")

        plan(args.profile, db_conn, user_infra_module)

        return

    print(args.profile)
    session = boto3.session.Session(profile_name=args.profile)
    gioc = GraphIaC.init(session, db_conn)

    if args.command == "run":
        user_infra_module.infra(gioc)
        GraphIaC.run(gioc)
        return

    elif args.command == "verify":
        user_infra_module.infra(gioc)
        failed = GraphIaC.verify(gioc)
        raise SystemExit(1 if failed else 0)

    elif args.command == "diagram":
        print("Diagram")

        gioc = GraphIaC.init(session, db_path)
        user_infra_module.infra(gioc)
        print(gioc.G)

        GraphIaC.export_graph(gioc, diagram_path)
        # GraphIOC.plan(gioc)
    else:
        print(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
