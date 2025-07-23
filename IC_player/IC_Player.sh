#!/usr/bin/env bash

ELECTRON="/home/ic/electron_player/node_modules/.bin/electron"
APP="/home/ic/electron_player/app.js"
OUTPUT="/tmp/electron_player_output.tmp"

ls ${ELECTRON} > ${OUTPUT}
ls ${APP} >> ${OUTPUT}

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
nvm use 17
node --version >> ${OUTPUT}

${ELECTRON} ${APP} 2>> ${OUTPUT}
