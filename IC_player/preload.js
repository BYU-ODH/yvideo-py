// In version 32, getting a file's path using the 'path' attribute was deprecated.
// This code will create a method that enables getting a filepath from the file object -- BDR 11/13/2024
const {webUtils } = require('electron')

window.customFileHandler = {
  showFilePath (file) {
    // It's best not to expose the full file path to the web content if
    // possible.
    const path = webUtils.getPathForFile(file)
    return path
  }
}