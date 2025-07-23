"""Sort json file according to `start`."""

import json
from pathlib import Path
import sys

in_path = Path(sys.argv[1])
in_json = json.loads(in_path.read_text())

output = sorted(in_json, key=lambda x: float(x['options']['start']))
try:
    assert all(x['options']['start'] in x['options']['details']['position']
               for x in output if 'position' in x['options']['details'])
except AssertionError as e:
    raise e('The first value in `position` must match the `start` time.')

out_str = json.dumps(output)
out_str = out_str.replace('{"options',      '\n  {"options')
out_str = out_str.replace('"label":',       '\n      "label":')
out_str = out_str.replace(' "start":',      '\n      "start":')
# out_str = out_str.replace(' "end":',        '\n      "end":')
# out_str = out_str.replace(', "type":',     ',\n      "type":')
out_str = out_str.replace(' "details":',    '\n      "details":')
# out_str = out_str.replace('{"type":',      '{\n          "type":')
# out_str = out_str.replace(' "amount":',     '\n          "amount":')
out_str = out_str.replace(' "position":',   '\n          "position":')
out_str = out_str.replace('], ',          '],\n                       ')

try:
    json.loads(out_str)
    print('Output json is valid.')
    valid = True
except ValueError as e:
    print(f'WARNING: Invalid json: {e}')
    valid = False
if valid:
    out_path = in_path
else:
    out_path = in_path.parents[0] / f'invalid_{in_path.name}'
print(f'Writing output to {out_path}...')
out_path.write_text(out_str)
