# JSON Format Documentation for Video Annotations

There are five types of video annotations:
1. skip
2. mute
3. blank
4. blur
5. and censors

Each one does something a bit different. When defining the annotations, create
a json file. Within that json file have an array of annotations. Overall the
code should look something like this:

``` json
[
  {
    "options": {
      "label": "Logo 0:00:21 - 0:00:35",
      "start": "21",
      "end": "35",
      "type": "skip",
      "details": {}
    }
  },
  {
    "options": {
      "label": "Words 0:00:37 - 0:00:48",
      "start": "37",
      "end": "48",
      "type": "mute",
      "details": {}
    }
  }
]
```

We now introduce the types of annotations.

## Skip

The skip annotation skips the video forward to a specific time, from the 'start'
time to the 'end' time. Nothing in between the two times will be seen.

### Skip Example
``` json
{
  "options": {
    "label": "Logo 0:00:21 - 0:00:35",
    "start": "21",
    "end": "35",
    "type": "skip",
    "details": {}
  }
}
```

## Mute

The mute annotation will mute a section of the video, from the 'start' time to
the 'end' time.

### Mute Example
``` json
{
  "options": {
    "label": "Words 48:37",
    "start": "2917",
    "end": "2922",
    "type": "mute",
    "details": {}
  }
}
```

## Blank

The blank annotation will black out the entire screen for a set period of time,
as denoted by the start and end times.

### Blank Example
``` json
{
  "options": {
    "label": "Bridge 0:00:40 - 0:00:45",
    "start": "40",
    "end": "45",
    "type": "blank",
    "details": {}
  }
}
```

## Blur

The blur annotation will blur the entire screen for a set period of time,
as denoted by the start and end times.

### Blur Example
``` json
{
  "options": {
    "label": "Panda 0:00:02 - 0:00:10",
    "start": "2",
    "end": "10",
    "type": "blur",
    "details": {}
  }
}
```

## Censor

The censor annotation can be used to cover up some sections of the screen. The
position of the censor bar depends on

Each censor has an interpolate boolean, which determines if the program will interpolate
between the censors, which would make the annimation from each position of the
censor a smoother.

There are two types of censors: blur and black. The "blur" censor blurs the image
of a rectangle defined by the `position` value. It is blurred according to the
`amount` value, which ranges from `0px` to infinity, with higher numbers blurring more.
`30px` is a standard amount . The "black" type blacks out a rectangle the as defined by the `position`.

### Censor Examples
#### Solid Color Censor
``` json
{
  "options": {
    "label": "Sky 0:00:00 - 0:00:13",
    "start": "0",
    "end": "13",
    "type": "censor",
    "details": {
      "type": "black",
      "interpolate": true,
      "position": {
        "4":     [18, 18, 15, 15],
        "4.25":  [12, 18],
        "4.50":  [14, 18],
        "4.75":  [16, 18],
        "5.00":  [18, 18],
        "5.25":  [18, 18, 14, 16],
        "5.50":  [18, 18, 12, 18],
        "5.75":  [18, 18, 10, 20],
        "6.00":  [18, 18, 14, 20]
      }
    }
  }
}
```

#### Blur Censor
``` json
{
  "options": {
    "label": "Sky 0:00:06 - 0:00:19",
    "start": "6",
    "end": "19",
    "type": "censor",
    "details": {
      "type": "blur",
      "interpolate": true,
      "amount": "30px",
      "position": {
        "6":     [0, 0, 15, 15],
        "8":     [10, 10],
        "9":     [15, 15],
        "10.25":  [16.00, 16.00],
        "10.50":  [16.25, 16.25],
        "10.75":  [16.50, 16.50],
        "11.00":  [16.75, 16.75],
        "11.25":  [17.00, 17.00],
        "11.50":  [17.25, 17.25],
        "11.75":  [17.50, 17.50],
        "12.00":  [17.75, 17.75],
        "12.25":  [18.00, 18.00, 17.1, 17.1],
        "12.50":  [18.00, 18.00, 17.4, 17.4],
        "12.75":  [18.00, 18.00, 17.7, 17.7],
        "13.00":  [18.00, 18.00, 18, 18],
      }
    }
  }
}
```
