"""Paid diagnostic of outcome assessment on recorded real CU states. No GUI actions."""

import argparse
import copy
import json
import os
from pathlib import Path

from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json, validate_choice
from jev_ultrafast.tracing import CURRENT_TRACE, Trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    load_environment()
    choices = [e for e in map(json.loads, args.trace.read_text().splitlines()) if e['type'] == 'choice']
    # Preserve actual observations and histories, including the wrong final result.
    cases = [("partial-clear", choices[3]['request']['state'], "UNSATISFIED"),
             ("wrong-final", choices[-1]['request']['state'], "UNSATISFIED")]
    correct = copy.deepcopy(cases[-1][1])
    text = correct['observations'][0]['text']
    correct['observations'][0]['text'] = text.replace('19‎×‎12‎+‎3', '2‎+‎3').replace('231', '5')
    cases.append(("synthetic-correct-final", correct, "SATISFIED"))
    question = {"type": "choice", "instructions": {
        "question": "Does the CURRENT observed application state satisfy the user's requested outcome?",
        "focus": "Compare the requested outcome with current visible values. Returned tool calls prove only attempts. "
                 "Distinguish old content from the requested result. Do not infer success from action history."},
        "criteria": {"SATISFIED": "The requested outcome is visibly present.",
                     "UNSATISFIED": "A request exists and the visible outcome does not satisfy it.",
                     "UNKNOWN": "No request is clear or observation is insufficient to judge."}}
    trace = Trace(args.output)
    token = CURRENT_TRACE.set(trace)
    (args.output / 'cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    results = []
    try:
        for name, state, expected in cases:
            body = {"model": choices[-1]['request']['model'], "state": state, "questions": {"outcome": question}}
            response = post_json('https://api.typesafe.ai/v1/systemone', os.environ['TYPESAFE_API_KEY'], body)
            answer = validate_choice(response['answers'].get('outcome', {}), question['criteria'])
            record = {"case": name, "expected": expected, "answer": answer, "correct": answer['choice'] == expected}
            trace.emit('evaluation', **record)
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        CURRENT_TRACE.reset(token)


if __name__ == '__main__':
    main()
