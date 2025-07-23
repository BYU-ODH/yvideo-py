import json
from pathlib import Path
import sys

from hms2s import s2hms

in_path = Path(sys.argv[1])
out_path = Path(f"/tmp/{in_path.name}")
print(f"Writing output file to {out_path}", file=sys.stderr)

hum_json = json.loads(in_path.read_text())
ic_json = []

type_Xlation = {
    "blank": "blank",
    "darken": "blank",
    "mutePlugin": "mute",
    "skip": "skip",
}

for each in hum_json:
    for track in each["media"][0]["tracks"]:
        for e in track["trackEvents"]:
            options = {}
            start = e["popcornOptions"]["start"]
            end = e["popcornOptions"]["end"]
            options["label"] = f"{s2hms(start)} - {s2hms(end)}"
            try:
                options["type"] = type_Xlation[e["type"]]
            except KeyError:
                raise NotImplementedError(f'Event "{e["type"]}" not implemented.')
            options["start"] = str(start)
            options["end"] = str(end)
            options["details"] = {}
            ic_json.append({"options": options})

ic_json = sorted(ic_json, key=lambda x: float(x["options"]["start"]))
out_path.write_text(json.dumps(ic_json).replace("}, {", "},\n {") + "\n")

# [
#   {
#     "media": [
#       {
#         "target": "player",
#         "url": [
#           "https://hummedia.byu.edu/video/4135844.mp4"
#         ],
#         "tracks": [
#           {
#             "id": "58d2d1de04743b376db2475d",
#             "required": true,
#             "name": "Layer 0",
#             "trackEvents": [],
#             "settings": null
#           }
#         ],
#         "duration": 91,
#         "id": "58867a1704743bcf5b1783da",
#         "name": "Ixcanul (Volcano)"
#       }
#     ],
#     "targets": [],
#     "creator": "mj32"
#   },
#   {
#     "media": [
#       {
#         "target": "player",
#         "url": [
#           "https://hummedia.byu.edu/video/4135844.mp4"
#         ],
#         "tracks": [
#           {
#             "id": "58d2d1de04743b3fd8aaeb6b",
#             "required": false,
#             "name": "Layer 0",
#             "trackEvents": [
#               {
#                 "popcornOptions": {
#                   "start": "948.57723",
#                   "end": "983.13405",
#                   "target": "target-0"
#                 },
#                 "type": "skip"
#               },
#               {
#                 "popcornOptions": {
#                   "start": "1273.77606",
#                   "end": "1317.41257",
#                   "target": "target-0"
#                 },
#                 "type": "skip"
#               },
#               {
#                 "popcornOptions": {
#                   "start": "1613.93318",
#                   "end": "1651.09033",
#                   "target": "target-0"
#                 },
#                 "type": "skip"
#               },
#               {
#                 "popcornOptions": {
#                   "start": "2729.01373",
#                   "end": "2799.18995",
#                   "ratio": "0.8",
#                   "target": "target-0"
#                 },
#                 "type": "darken"
#               },
#               {
#                 "popcornOptions": {
#                   "start": "3569.1",
#                   "end": "3582.19967",
#                   "ratio": "0.8",
#                   "target": "target-0"
#                 },
#                 "type": "darken"
#               },
#               {
#                 "popcornOptions": {
#                   "start": "3613.1",
#                   "end": "3629.99798",
#                   "ratio": "0.9",
#                   "target": "target-0"
#                 },
#                 "type": "darken"
#               }
#             ],
#             "settings": null
#           }
#         ],
#         "duration": 91,
#         "id": "58867a1704743bcf5b1783da",
#         "name": "Ixcanul (Volcano)"
#       }
#     ],
#     "targets": [],
#     "creator": "mj32"
#   }
# ]
