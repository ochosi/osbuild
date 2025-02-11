"""Entrypoints for osbuild

This module contains the application and API entrypoints of `osbuild`, the
command-line-interface to osbuild. The `osbuild_cli()` entrypoint can be safely
used from tests to run the cli.
"""


import argparse
import json
import os
import sys

import osbuild
import osbuild.meta
import osbuild.monitor
from osbuild.objectstore import ObjectStore


RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"


def parse_manifest(path):
    if path == "-":
        manifest = json.load(sys.stdin)
    else:
        with open(path) as f:
            manifest = json.load(f)

    return manifest


def show_validation(result, name):
    if name == "-":
        name = "<stdin>"

    print(f"{BOLD}{name}{RESET} ", end='')

    if result:
        print(f"is {BOLD}{GREEN}valid{RESET}")
        return

    print(f"has {BOLD}{RED}errors{RESET}:")
    print("")

    for error in result:
        print(f"{BOLD}{error.id}{RESET}:")
        print(f"  {error.message}\n")


def export(name_or_id, output_directory, store, manifest):
    pipeline = manifest[name_or_id]
    obj = store.get(pipeline.id)
    dest = os.path.join(output_directory, name_or_id)

    os.makedirs(dest, exist_ok=True)
    obj.export(dest)


def parse_arguments(sys_argv):
    parser = argparse.ArgumentParser(description="Build operating system images")

    parser.add_argument("manifest_path", metavar="MANIFEST",
                        help="json file containing the manifest that should be built, or a '-' to read from stdin")
    parser.add_argument("--store", metavar="DIRECTORY", type=os.path.abspath,
                        default=".osbuild",
                        help="directory where intermediary os trees are stored")
    parser.add_argument("-l", "--libdir", metavar="DIRECTORY", type=os.path.abspath, default="/usr/lib/osbuild",
                        help="the directory containing stages, assemblers, and the osbuild library")
    parser.add_argument("--checkpoint", metavar="ID", action="append", type=str, default=None,
                        help="stage to commit to the object store during build (can be passed multiple times)")
    parser.add_argument("--export", metavar="ID", action="append", type=str, default=None,
                        help="object to export, can be passed multiple times")
    parser.add_argument("--json", action="store_true",
                        help="output results in JSON format")
    parser.add_argument("--output-directory", metavar="DIRECTORY", type=os.path.abspath,
                        help="directory where result objects are stored")
    parser.add_argument("--inspect", action="store_true",
                        help="return the manifest in JSON format including all the ids")

    return parser.parse_args(sys_argv[1:])


# pylint: disable=too-many-branches,too-many-return-statements
def osbuild_cli():
    args = parse_arguments(sys.argv)
    desc = parse_manifest(args.manifest_path)

    index = osbuild.meta.Index(args.libdir)

    # detect the format from the manifest description
    info = index.detect_format_info(desc)
    if not info:
        print("Unsupported manifest format")
        return 2
    fmt = info.module

    # first thing is validation of the manifest
    res = fmt.validate(desc, index)
    if not res:
        if args.json or args.inspect:
            json.dump(res.as_dict(), sys.stdout)
            sys.stdout.write("\n")
        else:
            show_validation(res, args.manifest_path)
        return 2

    manifest = fmt.load(desc, index)

    if args.checkpoint:
        missed = manifest.mark_checkpoints(args.checkpoint)
        if missed:
            for checkpoint in missed:
                print(f"Checkpoint {BOLD}{checkpoint}{RESET} not found!")
            print(f"{RESET}{BOLD}{RED}Failed{RESET}")
            return 1

    if args.inspect:
        result = fmt.describe(manifest, with_id=True)
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
        return 0

    if not args.output_directory and not args.checkpoint:
        print("No output directory or checkpoints specified, exited without building.")
        return 0

    monitor_name = "NullMonitor" if args.json else "LogMonitor"
    monitor = osbuild.monitor.make(monitor_name, sys.stdout.fileno())

    try:
        output_directory = args.output_directory
        with ObjectStore(args.store) as object_store:

            manifest.download(object_store, monitor, args.libdir)

            r = manifest.build(
                object_store,
                monitor,
                args.libdir,
                output_directory=output_directory
            )

            if r["success"] and args.export:
                if not output_directory:
                    raise ValueError("Need --output-directory for --export")
                for pid in args.export:
                    export(pid, output_directory, object_store, manifest)

    except KeyboardInterrupt:
        print()
        print(f"{RESET}{BOLD}{RED}Aborted{RESET}")
        return 130

    if args.json:
        r = fmt.output(manifest, r)
        json.dump(r, sys.stdout)
        sys.stdout.write("\n")
    else:
        if r["success"]:
            for name, pl in manifest.pipelines.items():
                print(f"{name + ':': <10}\t{pl.id}")
        else:
            print()
            print(f"{RESET}{BOLD}{RED}Failed{RESET}")

    return 0 if r["success"] else 1
