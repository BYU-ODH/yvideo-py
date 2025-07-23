import json
from pathlib import Path
import sys

from hms2s import s2hms

in_path = Path(sys.argv[1])
out_path = Path(f'/tmp/{in_path.name}')
print(f'Writing output file to {out_path}', file=sys.stderr)

hum_json = json.loads(in_path.read_text())
ic_json = []

type_Xlation = {'blank': 'blank',
                'mutePlugin': 'mute',
                'skip': 'skip'}

for track in hum_json['media'][0]['tracks']:
    for e in track['trackEvents']:
        options = {}
        start = e['popcornOptions']['start']
        end = e['popcornOptions']['end']
        options['label'] = f'{s2hms(start)} - {s2hms(end)}'
        try:
            options['type'] = type_Xlation[e['type']]
        except KeyError:
            raise NotImplementedError(f'Event "{e["type"]}" not implemented.')
        options['start'] = str(start)
        options['end'] = str(end)
        options['details'] = {}
        ic_json.append({'options': options})

ic_json = sorted(ic_json, key=lambda x: float(x['options']['start']))
out_path.write_text(json.dumps(ic_json).replace('}, {', '},\n {') + '\n')
