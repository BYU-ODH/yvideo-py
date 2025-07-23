# electron_player
An alternative video player for BYU International Cinemas (based on Electron)


## Installation

Install node (actually nvm so you can use npm)
``` shell
wget -qO- https://raw.githubusercontent.com/creationix/nvm/v0.33.6/install.sh | bash
```

Install electron
``` shell
npm install -g electron
```

Run program
``` shell
electron app.js
```

You may also need to install libgconf using
``` shell
apt-get install libgconf-2-4
```

## Usage
You should have three files before you begin trying to watch a video using electron_player.
```
∟ video.mp4 or video.m4v
∟ video.icf
∟ video.json
```
The names of the files do not need to match. The mp4 or m4v file contains the video. The json file contains the 
annotations which will be displayed. For more information on the format of the json file, please refer
to the documentation in the `JSONFormat.md` file. The icf file contains something that looks like the following:
``` json
{
  "subtitle": null,
  "video": "KungFuPandamv_IC.m4v",
  "annotation": "KungFuPandamv_IC.json"
}
```
The `video` property contains the path of the video file. The `annotation` property contains the path of the json file.
The `subtitle` property has not been implemented yet, but would potentially contain the path to the subtitle file.
