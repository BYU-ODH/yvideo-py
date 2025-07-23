from pathlib import Path
from shutil import move
import sys

mp4 = Path(sys.argv[1])
assert mp4.suffix == ".mp4", f"Given file does not have mp4 extension: {mp4}"
stem = mp4.stem

local_dir = Path(__file__).parent
desktop = local_dir / "Desktop"
target_dir = desktop / stem
(target_dir / ".ic").mkdir(parents=True, exist_ok=True)

move(str(mp4), str(target_dir / ".ic" / f"{stem}.mp4"))

with (local_dir / "template.icf").open() as f:
    icf = f.read()
icf = icf.replace("filename", stem)
with (target_dir / f"{stem}.icf").open("w") as f:
    f.write(icf)

with (target_dir / f"{stem}.json").open("w") as f:
    f.write("[]\n")
