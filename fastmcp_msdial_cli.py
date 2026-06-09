import argparse
import os
import sys
from pathlib import Path

from msdial_reader import (
    detect_file_type,
    export_json,
    find_supported_files,
    parse_aef_file,
    parse_arf_file,
    parse_pai2_file,
    summarize_parsed_data,
)

try:
    import fastmcp  # type: ignore
    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False


def choose_file_from_directory(directory: str) -> str:
    candidates = find_supported_files(directory)
    flattened = []
    for kind, files in candidates.items():
        for path in files:
            flattened.append((kind, path))

    if not flattened:
        raise FileNotFoundError(f'No supported files found in directory: {directory}')

    print('Supported files found:')
    for index, (kind, path) in enumerate(flattened, start=1):
        print(f'  {index}. [{kind}] {path}')

    selection = input('Select file number to parse: ').strip()
    try:
        idx = int(selection) - 1
        return flattened[idx][1]
    except Exception:
        raise ValueError('Invalid selection')


def prompt_for_path() -> str:
    value = input('Enter the full file path or data directory: ').strip()
    if not value:
        raise ValueError('No path entered')
    return value


def parse_file(file_path: str):
    file_type = detect_file_type(file_path)
    if file_type == 'arf':
        return parse_arf_file(file_path), file_type
    if file_type == 'aef':
        return parse_aef_file(file_path), file_type
    if file_type == 'pai2':
        return parse_pai2_file(file_path), file_type
    raise ValueError(f'Unsupported file type for: {file_path}')


def run_interactive(args):
    if args.file:
        path = args.file
    else:
        path = prompt_for_path()

    if os.path.isdir(path):
        file_path = choose_file_from_directory(path)
    else:
        file_path = path

    data, file_type = parse_file(file_path)
    summary = summarize_parsed_data(data, file_type)
    print('\nParse complete:')
    print(f"  file: {file_path}")
    print(f"  type: {file_type}")
    print(f"  records: {summary['count']}")
    print(f"  keys: {summary['sample_keys']}")

    if args.output:
        output_path = args.output
        export_json(data, output_path)
        print(f'Exported JSON to: {output_path}')
    return data


def run_fastmcp_agent(args):
    if not FASTMCP_AVAILABLE:
        print('FastMCP is not installed. Install it via requirements.txt or pip install fastmcp')
        return None

    # This placeholder can be extended once FastMCP is available in the environment.
    print('FastMCP is available. The next step is to implement a dedicated agent wrapper here.')
    return None


def main():
    parser = argparse.ArgumentParser(description='MS-DIAL file parser for interactive and FastMCP usage.')
    parser.add_argument('--file', '-f', help='Path to .arf2, .EIC.aef, or .pai2 input file.')
    parser.add_argument('--output', '-o', help='Optional output JSON path.')
    parser.add_argument('--fastmcp', action='store_true', help='Check FastMCP availability and prepare an agent stub.')
    args = parser.parse_args()

    if args.fastmcp:
        return run_fastmcp_agent(args)

    try:
        run_interactive(args)
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
