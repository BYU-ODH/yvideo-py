const { ipcRenderer } = require('electron')
const { dialog } = require('electron')
const fs = require('fs')
const { player } = require('./player.js')

let annotationMode = false;

Events.addListener(window, 'resize', () => {
  player.drawBox()
})

const toggleDevTools = () => {
  ipcRenderer.send('toggle-dev-tools', annotationMode)
}

ipcRenderer.on('response-cmd-argv', (event, argv) => {
  if (argv[2] == 'annotate' || argv[2] == 'a' || argv[2] == '-a') {
    player.toggleAnnotationMode()
  }
})
ipcRenderer.send('request-cmd-argv', 'request')
