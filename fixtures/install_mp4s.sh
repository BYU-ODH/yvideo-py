#!/bin/bash

# Ensure this script is run from the project root
if [ ! -f "manage.py" ]; then
  echo "This script must be run from the project root directory (where manage.py is located)."
  echo "e.g., cd /path/to/project && ./fixtures/install_mp4s.sh"
  exit 1
fi

cp -p fixtures/media/birds.mp4 media/Birds/original_no_audio.mp4
cp -p fixtures/media/color_grid.mp4 media/Grid/original.mp4
cp -p fixtures/media/color_grid_trans_border.mp4 media/Grid/transparent_border.mp4
