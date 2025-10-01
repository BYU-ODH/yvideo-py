import { app, BrowserWindow, ipcMain } from 'electron'
import { fileURLToPath } from 'url'
import path from 'path'

const argv = process.argv;
var mainWindow = null;

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

function createWindow() {
  // Create the browser window.
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 780,
    fullscreenable: true,
    frame: true,
    icon: path.join(__dirname, '/resources/filmstrip.png'),
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    }
  })

  // and load the index.html of the app.
  mainWindow.loadURL('file://' + __dirname + '/src/IC-player.html')
}

app.on('ready', createWindow)

ipcMain.on('request-cmd-argv', (event, arg) => {
  event.reply('response-cmd-argv', argv)
})

ipcMain.on('toggle-dev-tools', (event, annotationMode) => {
  if (annotationMode && !mainWindow.webContents.isDevToolsOpened()) {
    mainWindow.webContents.openDevTools()
  }
  else if (!annotationMode && mainWindow.webContents.isDevToolsOpened()) {
    mainWindow.webContents.closeDevTools()
  }
})
