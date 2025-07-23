module.exports = {
  player: {
    $videoObj: $('#player'),
    annotations: null,
    currently: null,
    jsonFilePath: null,
    currTime: 0, // The current time of the player
    reloadingJson: false, // Is JSON being reloaded
    paused: false, // Is the player paused or playing

    // Select files or begin player
    buttonPress: () => {
      const files = player.getSelectedFiles()
      if (!files) {
        $('#filePicker')[0].click()
        $('#filePicker').change(() => {
          $('#files').html(player.getSelectedFiles().videoFile.name)
          $('#playButton').addClass('ready')
          $('#filePicker').change(null)
        })
      } else {
        player.startPlayer()
      }
    },

    // Toggle annotation mode, open devtools
    toggleAnnotationMode: () => {
      annotationMode = !annotationMode
      $('#toggleAnnotationModeBtn').toggleClass('active')
      toggleDevTools()
    },

    // Start the player
    startPlayer: () => {
      const files = player.getSelectedFiles()

      if (!files) {
        alert('Error: The selected folder does not contain an *.icf file. Please try again.')
        return
      }

      // With this, the player will start playing after someone
      // returns to the menu and begins watching a video again
      player.paused = false

      // Instantiate object variable 'annotations'
      player.parseNPlay(files['jsonFile'], player.initializeCallback)
    },

    // Hide the player when user returns to menu
    hidePlayer: () => {
      // Show Splash Screen
      $('#splashScreen').css('visibility', 'visible')

      // Hide Player
      $('#playerContainer').css('visibility', 'hidden')

      // Remove 'ready' class from playButton
      $('#playButton').removeClass('ready')

      // Hide Save and Reload JSON buttons if visible
      $('#reloadJsonBtn').css('visibility', 'hidden')
      $('#saveJsonBtn').css('visibility', 'hidden')

      $('#returnBtn').css('visibility', 'hidden')
      $('#issueBtn').css('visibility', 'hidden')

      // Remove inkeyup listener
      $(document).keyup(null)
      $(document).keydown(null)

      // Pause the video
      player.pause()

      // Set background to normal
      document.body.style.background = 'linear-gradient(to right, #1e425e, #839aa8, #1e425e)'

      //Clear selected files
      $('#filePicker').val('')
      $('#files').html('Select Files')

      //Reset player variables
      player.resetAnnotations()
      player.annotations = null
      player.currently = null
      player.jsonFilePath = null
      player.currTime = 0
    },

    // Reload the json annotations and begin player with same settings
    reloadJson: () => {
      if (!player.paused) {
        player.pause()
      }

      console.log('Reloading JSON')
      player.reloadingJson = true
      let reloadJsonTime = player.$videoObj.prop('currentTime')
      player.currTime = player.$videoObj.prop('currentTime')
      player.paused = player.$videoObj.prop('paused')

      Events.removeListener(document.getElementById('player'), 'playing', (event) => {
        return
      })

      player.resetAnnotations()

      var fileData = fs.readFileSync(player.jsonFilePath)
      player.initializeCallback(fileData)
      player.currTime = reloadJsonTime
      player.$videoObj.prop('currentTime', reloadJsonTime)
      player.reloadingJson = false
    },

    // Write changes to the annotations to the json file
    saveJson: () => {
      if (!player.paused) {
        player.pause()
      }

      // Confirm the overwriting of the json file
      let continueSave = dialog.showMessageBoxSync(null, {
        type: 'question',
        buttons: ['Cancel', 'Yes, please'],
        defaultId: 0,
        title: 'Question',
        message: 'Are you sure that you want to do this?',
        detail: 'Saving JSON filters will overwrite the previous JSON filters',
      })
      if (!continueSave) return

      let formattedAnnotations = []
      let intPositionObj = {}
      for (i = 0; i < player.annotations.length; i++) {
        let annotation = {
          'options': {
            'label': player.annotations[i].label,
            'start': player.annotations[i].start,
            'end': player.annotations[i].end,
            'type': player.annotations[i].type,
            'details': player.annotations[i].details
          }
        }
        if (annotation.options.details.intPositions) {
          let intPositions = annotation.options.details.intPositions
          // Save deleted intPositions
          intPositionObj[i] = intPositions
          delete annotation.options.details.intPositions
        }
        formattedAnnotations.push(annotation)
      }
      let jsonAnnotations = JSON.stringify(formattedAnnotations, null, 2)

      // Add saved intPositions to their respective censors
      let intPositionKeys = Object.keys(intPositionObj)
      for (let j = 0; j < intPositionKeys.length; j++) {
        player.annotations[intPositionKeys[j]].details.intPositions = intPositionObj[intPositionKeys[j]]
      }

      fs.writeFile(player.jsonFilePath, jsonAnnotations, 'utf8', (err) => {
        if (err) throw err
        console.log('Wrote to JSON File')
      })
    },

    // Load the files
    getSelectedFiles: () => {
      var fileList = $('#filePicker').prop('files'),
        jsonFile = null,
        icfFile = null,
        videoFile = null,
        jsonFileExists = false,
        icfFileExists = false,
        videoFileExists = false

      if (!fileList) return null
      for (var i = 0; i < fileList.length; i++) {
        var ext = fileList[i]['name'].split('.')[1]
        if (ext === 'json') {
          jsonFileExists = true
          jsonFile = fileList[i]
        }
        else if (ext === 'icf') {
          icfFileExists = true
          icfFile = fileList[i]
        }
        else if (ext === 'mp4' || ext === 'm4v') {  /*TODO: Add all supported file types*/
          videoFileExists = true
          videoFile = fileList[i]
        }
      }

      // If the icf file is the only one selected
      if (icfFileExists && (!jsonFileExists || !videoFileExists)) {
        const icfData = fs.readFileSync(customFileHandler.showFilePath(icfFile))
        const icfObj = JSON.parse(icfData)

        const jsonPath = customFileHandler.showFilePath(icfFile).replace(/\/[^\/]*$/, '/' + icfObj['annotation'])
        const videoPath = customFileHandler.showFilePath(icfFile).replace(/\/[^\/]*$/, '/.ic/' + icfObj['video'])

        jsonFileExists = true
        jsonFile = {
          path: jsonPath
        }
        videoFileExists = true
        videoFile = {
          path: videoPath,
          name: icfObj['video']
        }
      }

      // if all the necessary files are included, return the fileList; else return FALSE
      return (jsonFile && videoFile)
        ? { 'jsonFile': jsonFile, 'icfFile': icfFile, 'videoFile': videoFile }
        : false
    },


    // Load the files
    generateICDirectory: () => {
      var HOME = process.env.HOME
      var videoFile = document.getElementById('mp4FilePicker').files[0]
      var videoFilePath = customFileHandler.showFilePath(videoFile);
      if (videoFilePath === undefined) {
        videoFilePath = '';
      }
      var jsonFile = document.getElementById('jsonFilePicker').files[0]
      var jsonFilePath = customFileHandler.showFilePath(jsonFile);
      if (jsonFilePath === undefined) {
        jsonFilePath = '';
      }
      var stem = videoFile.name.split(`.`)[0]
      var dirName = HOME + `/Desktop/` + stem
      var hiddenDirName = dirName + `/.ic`
      if (!fs.existsSync(hiddenDirName)){
        fs.mkdirSync(hiddenDirName, { recursive: true });
      }
      fs.copyFile(videoFilePath, hiddenDirName + `/` + videoFile.name, (err) => {if (err) alert(err)})

      if (jsonFile) {
        fs.copyFile(jsonFilePath, dirName + `/` + stem + `.json`, (err) => {if (err) alert(err)})
        jsonPath = stem + '.json'
      } else { jsonPath = null}

      var icfString = JSON.stringify({"subtitle": null,
                                    "video": videoFile.name,
                                    "annotation": jsonPath})
      fs.writeFile(dirName + `/` + stem + `.icf`, icfString, `utf8`, (err) => {if (err) alert(err)})
      alert(`Unless you received errors, your IC file has been created on the Desktop.`)
    },

    // Get dimensions of the video
    getVideoDimensions: () => {
      var $video = player.$videoObj

      // Ratio of the video's intrisic dimensions
      var videoRatio = $video.prop('videoWidth') / $video.prop('videoHeight')

      // The width and height of the video element
      var width = $video.prop('offsetWidth')
      var height = $video.prop('offsetHeight')

      // The ratio of the element's width to its height
      var elementRatio = width / height

      // If the video element is short and wide
      if (elementRatio > videoRatio) width = height * videoRatio;

      // It must be tall and thin, or exactly equal to the original ratio
      else height = width / videoRatio
      return {
        width: width,
        height: height
      }
    },

    //Draw the box that the annotations use for positioning
    drawBox: () => {
      var videoDimensions = player.getVideoDimensions()
      var vidHeight = videoDimensions.height
      var vidWidth = videoDimensions.width

      // Get window Height
      var winHeight = window.innerHeight
      var winWidth = window.innerWidth

      var boxTop = 0
      var boxLeft = 0
      if (winHeight > vidHeight)
        boxTop = (winHeight - vidHeight) / 2
      else
        boxLeft = (winWidth - vidWidth) / 2

      $('#box').css({
        top: `${boxTop}px`,
        left: `${boxLeft}px`,
        height: `${vidHeight}px`,
        width: `${vidWidth}px`
      })
    },

    // Parse jsonFile and initialize player
    parseNPlay: (jsonFile, initializeCallback) => {
      player.jsonFilePath = jsonFile.path
      fs.readFile(jsonFile.path, (err, fileData) => {
        if (err) {
          return err;
        }
        initializeCallback(fileData)
      })
    },

    // Callback function when loading any data
    initializeCallback: (fileData) => {
      console.log('initializing')
      player.annotations = []
      var jsonObj = JSON.parse(fileData)
      if (jsonObj['media']) {
        var jsonGuts = jsonObj['media'][0]['tracks'][0]['trackEvents']
      } else {
        var jsonGuts = jsonObj
      }
      for (var i = 0; i < jsonGuts.length; i++) {
        if (jsonObj['media']) {
          var annotation = {
            'label': jsonGuts[i].popcornOptions['label'],
            'start': jsonGuts[i].popcornOptions['start'],
            'end': jsonGuts[i].popcornOptions['end'],
            'details': jsonGuts[i].popcornOptions['details'],
            'type': jsonGuts[i]['type']
          }
        } else {
          var annotation = {
            'label': jsonGuts[i].options['label'],
            'start': jsonGuts[i].options['start'],
            'end': jsonGuts[i].options['end'],
            'type': jsonGuts[i].options['type'],
            'details': jsonGuts[i].options['details']
          }
        }
        if (annotation['type'] == 'censor' && annotation['details']['interpolate']) {
          player.interpolateCensor(annotation)
        }
        player.annotations.push(annotation)
      }
      player.annotate()

      // Hide Splash Screen
      $('#splashScreen').css('visibility', 'hidden')

      const files = player.getSelectedFiles()

      // Set video src to given file
      let videoPath = files['videoFile'].path
      player.$videoObj.prop('src', videoPath)

      // Show Player
      $('#playerContainer').css('visibility', 'visible')

      // Show Save and Reload JSON buttons if annotationMode = true
      $('#reloadJsonBtn').css('visibility', annotationMode ? 'visible' : 'hidden')
      $('#saveJsonBtn').css('visibility', annotationMode ? 'visible' : 'hidden')

      if (!window.screenTop && !window.screenY) {
        $('#returnBtn').css('visibility', 'hidden')
        $('#issueBtn').css('visibility', 'hidden')
        console.log('hidden')
      }
      else {
        $('#returnBtn').css('visibility', 'visible')
        $('#issueBtn').css('visibility', 'visible')
        console.log('visible')
      }

      // Hide Splash Screen
      $('#splashScreen').css('visibility', 'hidden')

      // Set background to black
      document.body.style.background = 'black'

      console.log(player.annotations)

      player.censors = []
      for (var i = 0; i < player.annotations.length; i++) {
        if (player.annotations[i].type == 'censor') {
          var censor = []
          censor[0] = player.annotations[i].start
          censor[1] = player.annotations[i].end
          censor[2] = []
          Object.entries(player.annotations[i].details.position).forEach(([key, val]) => {
            censor[2].push([key, val[0], val[1]])
          })
          player.censors.push(censor)
        }
      }

      player.addListenersAtStart()

      // Play the video
      if (!player.paused) {
        player.play()
      }
    },

    // Add anotations by calling onFrameAdv
    annotate: () => {
      console.log('in the annotate function')
      player.currently = { 'muting': -1, 'blanking': -1, 'blurring': -1 }
      Events.addListener(document.getElementById('player'), 'playing', (event) => {
        player.onFrameAdv()
      })
    },

    // Validate annotations to check for valid times and values
    validateAnnotations: () => {
      console.log('validating')
      if (player.$videoObj.prop('readyState') < 1) {
        return
      }
      let annotationErrors = ''
      for (var i = 0; i < player.annotations.length; i++) {
        let a = player.annotations[i]
        let label = a.label || (a.type + ' at time ' + a.start)
        if (parseFloat(a.start) < 0.0) {
          annotationErrors += 'ANNOTATION ERROR: Start time of ' + label + ' is before the video starts\n\n'
        }
        if (parseFloat(a.end) > player.$videoObj.prop('duration')) {
          annotationErrors += 'ANNOTATION ERROR: End time of ' + label + ' is after the video ends\n\n'
        }
        if (parseFloat(a.start) > parseFloat(a.end)) {
          annotationErrors += 'ANNOTATION ERROR: Start time of ' + label + ' is after the video end time\n\n'
        }

        if (a.type == 'censor') {
          let timeKeys = Object.keys(a.details.position).sort((a, b) => {
            return parseFloat(a, 10) - parseFloat(b, 10)
          })

          if (a.details.position[timeKeys[0]].length != 4) {
            annotationErrors += 'ANNOTATION ERROR: First position time for ' + label + ' does not have 4 values\n\n'
            a.details.position[timeKeys[0]].push(15, 15)
          }
          if (parseFloat(timeKeys[0]) > parseFloat(a.start)) {
            annotationErrors += 'ANNOTATION ERROR: First position time for ' + label + ' is after the start time\n\n'
            Object.defineProperty(a.details.position, a.start,
              Object.getOwnPropertyDescriptor(a.details.position, timeKeys[0]))
            delete a.details.position[timeKeys[0]]
          }
          else if (parseFloat(timeKeys[0]) < parseFloat(a.start)) {
            if (parseFloat(timeKeys[0]) < 0.0) {
              annotationErrors += 'ANNOTATION ERROR: First position time for ' + label + ' is before the video starts\n\n'
            }
            else {
              annotationErrors += 'ANNOTATION ERROR: First position time for ' + label + ' is before the start time\n\n'
            }
            Object.defineProperty(a.details.position, a.start,
              Object.getOwnPropertyDescriptor(a.details.position, timeKeys[0]))
            delete a.details.position[timeKeys[0]]
          }
        }

        if ((player.annotations[i - 1] != null &&
          parseFloat(player.annotations[i - 1].start) > parseFloat(a.start)) ||
          (player.annotations[i - 2] != null &&
            parseFloat(player.annotations[i - 2].start) > parseFloat(a.start))) {
          annotationErrors += 'ANNOTATION ERROR: Annotation ' + label + ' is out of order\n\n'
        }
      }

      if (annotationErrors.length > 0) {
        dialog.showMessageBoxSync({
          type: 'warning',
          message: annotationErrors
        })
      }
    },

    // Report an issue with the player using GitHub Issues
    reportIssue: () => {
      if (!player.paused) {
        player.pause()
      }

      $('#issueDialog').css('visibility', 'visible')

      $('#issueDialog').dialog({
        close: function (type, data) {
          $('#issueDialog').css('visibility', 'hidden')
        },
        buttons: {
          'Report Issue on GitHub': function () {
            const url = 'https://github.com/BYU-ODH/electron_player/issues/new';
            window.open(url);
            $(this).dialog('close');
          }
        }
      })
    },

    // Interpolate censor movements to smooth their animation
    interpolateCensor: (annotation) => {
      annotation.details['intPositions'] = {}
      let position = annotation.details.position
      let timeKeys = Object.keys(position).sort((a, b) => {
        return parseFloat(a, 10) - parseFloat(b, 10)
      })

      for (let i = 0; i < timeKeys.length; i++) {
        let t1 = null
        let t2 = null
        if (timeKeys[i + 1]) {
          t1 = timeKeys[i]
          t2 = timeKeys[i + 1]
          annotation.details['intPositions'][t1] = position[t1]
        }
        else {
          annotation.details['intPositions'][timeKeys[i]] = position[timeKeys[i]]
          break;
        }

        let maxTimeInterval = 1 / 30
        let tdiff = parseFloat(t2) - parseFloat(t1)
        let incr = Math.floor(tdiff / maxTimeInterval)
        if (tdiff <= maxTimeInterval) continue

        let xincr = (position[t2][0] - position[t1][0]) / incr
        let yincr = (position[t2][1] - position[t1][1]) / incr

        let wincr = null
        let hincr = null
        if (position[t1][2] && position[t1][3]
          && position[t2][2] && position[t2][3]) {
          wincr = (position[t2][2] - position[t1][2]) / incr
          hincr = (position[t2][3] - position[t1][3]) / incr
        }

        for (let i = 1; i < incr; i++) {
          let tmid = parseFloat(t1) + i * maxTimeInterval
          let xmid = position[t1][0] + i * xincr
          let ymid = position[t1][1] + i * yincr
          let wmid = null
          let hmid = null
          if (wincr && hincr) {
            wmid = position[t1][2] + i * wincr
            if (xmid + wmid > 100) {
              wmid = 100 - xmid
            }
            hmid = position[t1][3] + i * hincr
            if (ymid + hmid > 100) {
              hmid = 100 - ymid
            }
            annotation.details['intPositions'][tmid] = [xmid, ymid, wmid, hmid]
          }
          else {
            annotation.details['intPositions'][tmid] = [xmid, ymid]
          }
        }
      }
    },

    // Add all of the various listeners
    addListenersAtStart: () => {
      Events.addListener(document.getElementById('player'), 'loadedmetadata', () => {
        // Draw box initially
        player.drawBox()
        player.validateAnnotations()
        console.log('loadedmetadata during skip')
        player.$videoObj.prop('currentTime', player.currTime)
      })

      // Add listener to hide controls at the end of video
      Events.addListener(document.getElementById('player'), 'ended', () => {
        player.$videoObj.prop('controls', false)
      })

      //Add listener to reveal controls as end of video on mousemove
      Events.addListener(document.getElementById('player'), 'mousemove', () => {
        player.$videoObj.prop('controls', true)
      })

      Events.addListener(document.getElementById('box'), 'mousemove', () => {
        player.$videoObj.prop('controls', true)
      })

      Events.addListener(document.getElementById('player'), 'onclick', () => {
        player.paused ? player.play() : player.pause()
      })

      //Add listener to prevent default seeking with arrow keys
      Events.addListener(document.getElementById('player'), 'seeked', () => {
        if (player.paused && player.currTime + 1.5 < player.$videoObj.prop('currentTime')) {
          player.currTime = player.$videoObj.prop('currentTime')
          if (!player.reloadingJson) player.onFrameAdv()
        }
        else if (player.paused && player.currTime - 1.5 > player.$videoObj.prop('currentTime')) {
          player.currTime = player.$videoObj.prop('currentTime')
          if (!player.reloadingJson) player.onFrameAdv()
        }
      })

      //Add listener to update censor tooltip and update player.pause on pause
      Events.addListener(document.getElementById('player'), 'pause', () => {
        player.paused = true
        console.log('paused while skipping')
        if (!window.screenTop && !window.screenY) {
          $('#returnBtn').css('visibility', 'visible')
          $('#issueBtn').css('visibility', 'visible')
        }
        if (player.annotations) {
          for (var i = 0; i < player.annotations.length; i++) {
            if (player.annotations[i].type == 'censor') {
              let currentTime = player.currTime
              let position = player.annotations[i].details.position
              let annoTime = Object.keys(position)
                .reduce((prev, curr) => Math.abs(curr - currentTime) < Math.abs(prev - currentTime) ? curr : prev)

              let width = null
              let height = null
              if (position[annoTime][2] && position[annoTime][3]) {
                width = position[annoTime][2]
                height = position[annoTime][3]
              }
              else {
                width = Math.round(100 * $('#censor' + i).width() / $('#box').width())
                height = Math.round(100 * $('#censor' + i).height() / $('#box').width())
              }

              let $censor = $('#censor' + i)
              let tooltipContent = '[' + position[annoTime][0] + ',' + position[annoTime][1] +
                ',' + width + ',' + height + ']'
              $censor.tooltip({
                content: tooltipContent,
                items: '#censor' + i
              })
              $censor.tooltip('enable');
            }
          }
        }
      })

      //Add listener to disable censor tooltips and player.paused on play
      Events.addListener(document.getElementById('player'), 'play', () => {
        player.paused = false
        for (var i = 0; i < player.annotations.length; i++) {
          if (player.annotations[i].type == 'censor') {
            $('#censor' + i).tooltip('disable');
          }
        }
        if (!window.screenTop && !window.screenY) {
          $('#returnBtn').css('visibility', 'hidden')
          $('#issueBtn').css('visibility', 'hidden')
        }
      })

      //Add listener to toggle play on Space
      document.onkeyup = function (e) {
        e.preventDefault()
        e = e || window.event
        // Space
        if (e.keyCode == 32) {
          player.paused ? player.play() : player.pause()
          player.currTime = player.$videoObj.prop('currentTime')
        }
      }

      //Add listener to seek video with videos
      //Seek 0.1 sec when paused and 5 seconds when playing
      document.onkeydown = function (e) {
        e.preventDefault()
        e = e || window.event
        // Right arrow
        if (e.keyCode == 39) {
          if (player.$videoObj.prop('paused')) {
            player.skipTo(player.currTime + 0.1)
          }
          else {
            player.skipTo(player.currTime + 5)
          }
        }
        // Left arrow
        else if (e.keyCode == 37) {
          if (player.$videoObj.prop('paused')) {
            player.skipTo(player.currTime - 0.1)
          }
          else {
            var inSkipTime = false
            var startTime = null
            for (var i = 0; i < player.annotations.length; i++) {
              if (player.annotations[i].type == 'skip') {
                if (player.currTime - 5 >= player.annotations[i]['start']
                  && player.currTime - 5 < player.annotations[i]['end']) {
                  inSkipTime = true
                  startTime = player.annotations[i]['start']
                }
              }
            }
            if (inSkipTime) {
              player.skipTo(startTime - 5)
            }
            else {
              player.skipTo(player.currTime - 5)
            }
          }
        }
      }
    },

    //For each new frame, update the annotations
    onFrameAdv: () => {
      if (!player.annotations) return
      var time = player.$videoObj.prop('currentTime')
      player.currTime = player.$videoObj.prop('currentTime')

      var numAnnotations = player.annotations.length
      for (var i = 0; i < numAnnotations; i++) {
        var vMuted = player.$videoObj.prop('muted')
        var vBlanked = player.$videoObj.hasClass('blanked')
        var vBlurred = player.$videoObj.hasClass('blurred')

        var a = player.annotations[i]
        var aStart = a['start']
        var aEnd = a['end']
        var aType = a['type']
        var aDetails = a['details']

        switch (a['type']) {
          case 'skip':
            if (time >= aStart && time < aEnd && !player.paused) {
              console.log('skipped to ' + Number(aEnd).toFixed(3))
              player.skipTo(aEnd)
            }
            break
          case 'mute':
          case 'mutePlugin':
            if (player.currently.muting === -1 || player.currently.muting === i) { //if no annotation is currently muting or *this* current annotaiton is muting
              if (time >= aStart && time < aEnd) { //if within annotation time
                if (!vMuted) {
                  console.log('mute on')
                  player.currently.muting = i
                  player.mute()
                }
              } else {
                if (vMuted) {
                  console.log('mute off')
                  player.currently.muting = -1
                  player.unmute()
                }
              }
            }
            break
          case 'blank':
            if (player.currently.blanking === -1 || player.currently.blanking === i) {
              if (time >= aStart && time < aEnd) {
                if (!vBlanked) {
                  console.log('blank on')
                  player.currently.blanking = i
                  player.blank()
                }
              } else {
                if (vBlanked) {
                  console.log('blank off')
                  player.currently.blanking = -1
                  player.unblank()
                }
              }
            }
            break
          case 'blur':
            if (player.currently.blurring == -1 || player.currently.blurring === i) {
              if (time >= aStart && time < aEnd) {
                if (!vBlurred) {
                  console.log('blur on')
                  player.currently.blurring = i
                  player.blur()
                }
              } else {
                if (vBlurred) {
                  console.log('blur off')
                  player.currently.blurring = -1
                  player.unblur()
                }
              }
            }
            break
          case 'censor':
            if (time >= aStart && time < aEnd) {
              if (!document.getElementById('censor' + i)) {
                console.log('censor on')
                $censor = $('<div>')
                $censor.attr('id', 'censor' + i)
                $censor.addClass('censor ' + aDetails['type'])
                $censor.css({
                  position: 'absolute',
                  width: aDetails['position'][aStart][2] + '%',
                  height: aDetails['position'][aStart][3] + '%',
                  left: aDetails['position'][aStart][0] + '%',
                  top: aDetails['position'][aStart][1] + '%'
                })
                if (aDetails['type'] == 'black' || aDetails['type'] == 'red') {
                  $censor.css({ 'background-color': aDetails['type'] })
                } else if (aDetails['type'] == 'blur') {
                  $censor.css({ 'backdrop-filter': 'blur(' + aDetails['amount'] + ')' })
                }
                $censor.appendTo($('#box'))

                if (annotationMode) {
                  var $censor = $('#censor' + i)
                  $censor.addClass('censor-annotate')
                  var index = $censor.attr('id').replace('censor', '')
                  var tooltipContent = '[' + aDetails['position'][aStart][0] + ',' + aDetails['position'][aStart][1] +
                    ',' + aDetails['position'][aStart][2] + ',' + aDetails['position'][aStart][3] + ']'
                  $censor.tooltip({
                    content: tooltipContent,
                    items: '#' + $censor.attr('id')
                  })
                  $censor.tooltip('disable');

                  // Create resize listener to update size of censor
                  $censor.resizable({
                    stop: function (e, ui) {
                      let currentTime = player.currTime
                      let annoTime = Object.keys(player.annotations[index].details.position)
                        .reduce((prev, curr) => Math.abs(curr - currentTime) < Math.abs(prev - currentTime) ? curr : prev)

                      let left = Math.round(100 * ui.position.left / ui.element[0].parentElement.clientWidth)
                      let top = Math.round(100 * ui.position.top / ui.element[0].parentElement.clientHeight)
                      let width = Math.round(100 * ui.size.width / ui.element[0].parentElement.clientWidth)
                      let height = Math.round(100 * ui.size.height / ui.element[0].parentElement.clientHeight)

                      if (player.annotations[index].details.position[annoTime][2]
                        && player.annotations[index].details.position[annoTime][3]) {
                        player.annotations[index].details.position[annoTime][2] = width
                        player.annotations[index].details.position[annoTime][3] = height
                      }
                      else {
                        player.annotations[index].details.position[annoTime].push(width, height)
                      }

                      let $censor = $('#censor' + index)
                      $censor.tooltip('option', 'content', '[' + left + ',' + top + ',' + width + ',' + height + ']');
                      $censor.tooltip('option', 'items', '#censor' + index);

                      player.interpolateCensor(player.annotations[index])
                    }
                  })

                  // Create drag listener to update size of censor
                  $censor.draggable({
                    stop: function (e, ui) {
                      let currentTime = player.currTime
                      let annoTime = Object.keys(player.annotations[index].details.position)
                        .reduce((prev, curr) => Math.abs(curr - currentTime) < Math.abs(prev - currentTime) ? curr : prev)

                      let $box = $('#box')
                      let left = Math.round(100 * ui.position.left / $box.width())
                      let top = Math.round(100 * ui.position.top / $box.height())
                      let width = Math.round(100 * ui.helper[0].clientWidth / $box.width())
                      let height = Math.round(100 * ui.helper[0].clientHeight / $box.height())

                      player.annotations[index].details.position[annoTime][0] = left
                      player.annotations[index].details.position[annoTime][1] = top

                      let $censor = $('#censor' + index)
                      $censor.tooltip('option', 'content', '[' + left + ',' + top + ',' + width + ',' + height + ']');
                      $censor.tooltip('option', 'items', '#censor' + index);

                      player.interpolateCensor(player.annotations[index])
                    }
                  })
                }
              } else {
                $censor = $('#censor' + i)
                // If censor is interpolating, use intPositions, else use normal positions
                if (a.details.interpolate) {
                  annoTime = Object.keys(a.details.intPositions).reduce((prev, curr) => Math.abs(curr - time) < Math.abs(prev - time) ? curr : prev)
                  $censor.css({
                    left: aDetails['intPositions'][annoTime][0] + '%',
                    top: aDetails['intPositions'][annoTime][1] + '%'
                  })
                  if (aDetails['intPositions'][annoTime][2] && aDetails['intPositions'][annoTime][3]) {
                    $censor.css({
                      width: aDetails['intPositions'][annoTime][2] + '%',
                      height: aDetails['intPositions'][annoTime][3] + '%'
                    })
                  }
                }
                else {
                  annoTime = Object.keys(a.details.position).reduce((prev, curr) => Math.abs(curr - time) < Math.abs(prev - time) ? curr : prev) //closest to current time
                  $censor.css({
                    left: aDetails['position'][annoTime][0] + '%',
                    top: aDetails['position'][annoTime][1] + '%'
                  })
                  if (aDetails['position'][annoTime][2] && aDetails['position'][annoTime][3]) {
                    $censor.css({
                      width: aDetails['position'][annoTime][2] + '%',
                      height: aDetails['position'][annoTime][3] + '%'
                    })
                  }
                }
              }
            } else {
              if ($('#censor' + i).length) {
                console.log('censor off')
                $('#censor' + i).remove()
              }
            }
            break
        }
      }
      if (player.$videoObj.prop('paused')) {
        return
      }
      requestAnimationFrame(player.onFrameAdv)
    },

    // Annotation Handlers

    play: () => {
      player.$videoObj.trigger('play');
      player.paused = false
    },

    pause: () => {
      player.$videoObj.trigger('pause');
      player.paused = true
    },

    skipTo: (time) => {
      player.$videoObj.prop('controls', false)
      /*
      This will completely remove the controls
      $('<style>').attr('id', 'skip').html(`
        ::-webkit-media-controls-enclosure {
          display:none !important;
        }`).appendTo(document.body)*/
      player.$videoObj.prop('currentTime', time)
      player.currTime = time
      player.onFrameAdv()
    },

    blank: () => {
      player.$videoObj.addClass('blanked')
      $('<style>').attr('id', 'mask').html(`
        video.blanked::-webkit-media-controls {
          background-color: black;
        }
        video.blanked::-webkit-media-text-track-container {
          z-index: 1;
        }`).appendTo(document.body)
    },

    unblank: () => {
      player.$videoObj.removeClass('blanked')
      $('#mask').html('')
    },

    blur: () => {
      player.$videoObj.addClass('blurred')
      $('<style>').attr('id', 'mask').html(`
        video.blurred::-webkit-media-controls {
          backdrop-filter: blur(10px);
        }
        video.blurred::-webkit-media-text-track-container {
          z-index: 1;
        }`).appendTo(document.body)
    },

    unblur: () => {
      player.$videoObj.removeClass('blurred')
      $('#mask').html('')
    },

    mute: () => { player.$videoObj.prop('muted', true) },

    unmute: () => { player.$videoObj.prop('muted', false) },

    resetAnnotations: () => {
      if (player.$videoObj.hasClass('blanked')) {
        player.unblank()
      }
      if (player.$videoObj.hasClass('blurred')) {
        player.unblur()
      }
      for (var i = 0; i < player.annotations.length; i++) {
        if (player.annotations[i].type == 'censor') {
          $censor = $('#censor' + i)
          if ($censor) {
            try {
              $censor.draggable('disable');
              $censor.resizable('disable');
            }
            catch (e) {
              // do nothing, draggable and resizable weren't defined
            }
            $censor.remove()
          }
        }
      }
      player.unmute()
    }
  }
}
