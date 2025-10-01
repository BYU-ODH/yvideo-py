const { ipcRenderer } = window.require('electron')
import { player } from './IC-player.js'

window.toggleDevTools = () => {
  ipcRenderer.send('toggle-dev-tools', player.annotationMode)
}

ipcRenderer.on('response-cmd-argv', (event, argv) => {
  if (argv[2] == 'annotate' || argv[2] == 'a' || argv[2] == '-a') {
    player.toggleAnnotationMode()
  }
})

ipcRenderer.send('request-cmd-argv', 'request')
