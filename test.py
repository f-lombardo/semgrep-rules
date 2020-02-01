#!/usr/bin/env python3

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path
from typing import List

"""
For each directory containing YAML rules, run those rules on the file in the same directory with the same name but different extension. 
E.g. eqeq.yaml runs on eqeq.py.
Validate that the output is annotated in the source file with by looking for a comment like:
 
 ```
 # ruleid:eqeq-is-bad
 ```
 On the preceeding line.

 """
YML_EXTENSIONS = {".yml", ".yaml"}

def print_debug(msg):
    global DEBUG
    if DEBUG:
        print(msg, file=sys.stderr)

def normalize_rule_id(line):
    """
    given a line like `     # ruleid:foobar` 
    return `foobar`
    """
    return line.strip().split(':')[1].strip()

def compute_confusion_matrix(reported, expected):
    true_positives = len(expected.intersection(reported))
    false_positives = len(reported - expected)
    true_negatives = 0 # we have no way to label "ok"
    false_negatives = len(expected - reported)

    return [true_positives, true_negatives, false_positives, false_negatives]

def _test_compute_confusion_matrix():
    tp, tn, fp, fn = compute_confusion_matrix(set([1, 2, 3, 4]), set([1]))
    assert tp == 1
    assert tn == 0
    assert fp == 3
    assert fn == 0

    tp, tn, fp, fn = compute_confusion_matrix(set([1, 2, 3, 4]), set([1, 2, 3, 4]))
    assert tp == 4
    assert tn == 0
    assert fp == 0
    assert fn == 0

    tp, tn, fp, fn = compute_confusion_matrix(set([2, 3]), set([1, 2, 3, 4]))
    assert tp == 2
    assert tn == 0
    assert fp == 0
    assert fn == 2

def score_output_json(json_out, test_files: List[str], ignore_todo: bool):
    comment_lines = collections.defaultdict(lambda: collections.defaultdict(list))
    reported_lines = collections.defaultdict(lambda: collections.defaultdict(list))
    score_by_checkid = collections.defaultdict(lambda: [0, 0, 0, 0])
    num_todo = 0

    for test_file in test_files:
        test_file = str(test_file.resolve())
        with open(test_file) as fin:            
            all_lines = fin.readlines()            
            for i, line in enumerate(all_lines):
                todo_in_line = ('#todoruleid:' in line or '# todoruleid' in line)
                if todo_in_line:
                    num_todo += 1
                if (not ignore_todo and todo_in_line) or \
                    ('#ruleid:' in line or "# ruleid:" in line):
                    # +1 because we are 0 based and sgrep output is not, plus skip the comment line
                    comment_lines[test_file][normalize_rule_id(line)].append(i + 2)
            
    for result in json_out['results']:
        reported_lines[str(Path(result['path']).resolve())][result['check_id']].append(int(result['start']['line']))
        
    def join_keys(a, b):
        return set(a.keys()).union(set(b.keys()))

    for file_path in join_keys(comment_lines, reported_lines):
        for check_id in join_keys(comment_lines[file_path], reported_lines[file_path]):
            assert len(set(reported_lines[file_path][check_id])) == len(reported_lines[file_path][check_id]), f"for testing, please don't make rules that fire multiple times on the same line ({check_id} in {file_path})"
            reported = set(reported_lines[file_path][check_id])
            expected = set(comment_lines[file_path][check_id])
            new_cm = compute_confusion_matrix(reported, expected)
            print_debug(f"reported: {reported}, expected: {expected}, confusion matrix: {new_cm}")
            old_cm = score_by_checkid[check_id]
            score_by_checkid[check_id] = [old_cm[i] + new_cm[i] for i in range(len(new_cm))]

    return (score_by_checkid, num_todo)

def generate_file_pairs(location: Path, ignore_todo: bool):
    filenames = list(location.rglob("*"))
    no_tests = []
    tested = []
    sgrep_error = []
    print('starting tests...')
    for filename in filenames:
        if filename.suffix in YML_EXTENSIONS:
            # find all filenames that have the same name but not extension
            test_files = [path for path in filenames if (path.suffix not in YML_EXTENSIONS and path.with_suffix('') == filename.with_suffix(''))]
            if not len(test_files):
                no_tests.append(filename)
                continue
            # invoke sgrep
            cmd = ['sgrep-lint', '--no-rewrite-rule-ids', '-f', str(filename)] + [str(t) for t in test_files]
            print_debug(cmd)
            try:
                output = subprocess.check_output(cmd, shell=False)
                output_json = json.loads((output.decode("utf-8")))
                print_debug(output_json)
                tested.append((filename, score_output_json(output_json, test_files, ignore_todo)))
            except subprocess.CalledProcessError as ex:
                print(f'sgrep error running {cmd}: {ex}')
                sgrep_error.append(cmd)
    
    print(f"{len(no_tests)} yaml files missing tests")
    print(f"{len(tested)} yaml files tested")
    print('check id scoring:')
    count_failures = 0
    for (filename, (output, num_todo)) in tested:
        print('='*120)
        print(filename)
        if not len(output.items()):
            print(f' no checks fired (TODOs: {num_todo})')
        for check_id, (tp, tn, fp, fn) in output.items():
            good = (fp == 0) and (fn == 0)
            if not good:
                count_failures += 1
            status = '✔' if good else '⚠'
            todo_text = f"(TODOs: {num_todo})" if num_todo > 0 else ''
            print(f'{status} - {check_id.ljust(60)}TP: {tp}\tTN:{tn}\t FP: {fp}\t FN: {fn} {todo_text}')

    if count_failures > 0:
        print(f"{count_failures} checks failed tests")
        sys.exit(1)
    else:
        print("all tests passed")
        sys.exit(0)

def main(location: Path, ignore_todo: bool, verbose: bool):
    global DEBUG
    DEBUG = verbose
    generate_file_pairs(location, ignore_todo)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="run tests for sgrep rule yaml files in specified directory",
    )

    # input
    parser.add_argument(
        "directory",
        default=["."],
        help="Folder to collect tests from (by default, entire current working directory searched).",
    )
    parser.add_argument(
        '--ignore-todo', 
        help='ignore rules marked as #todoruleid: in test files',
        action='store_true'
    )
    parser.add_argument(
        '-v', '--verbose', 
        help='debug output',
        action='store_true'
    )
    args = parser.parse_args()
    _test_compute_confusion_matrix()
    main(Path(args.directory), args.ignore_todo, args.verbose)
