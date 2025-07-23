const Events = (function () {
    var listeners = []

    const matcher = (element, eventname) => {
        return (evl) => evl.element === element && evl.eventname === eventname
    }

    var exists = (element, eventname) => listeners.some(matcher(element, eventname))

    function addListener(element, eventname, callback) {
        if (exists(element, eventname)) {
            return false
        }
        element.addEventListener(eventname, callback)
        listeners.push({ element: element, eventname: eventname, callback: callback })
        return true
    }

    function removeListener(element, eventname, callback) {
        if (!exists(element, eventname)) {
            return false
        }
        const isMatch = matcher(element, eventname)

        for (var i; i < listeners.length; i++) {
            if (isMatch(listeners[i])) {
                listeners[i].element.removeEventListener(listeners[i].eventname, listeners[i].callback)
                listeners.splice(i, 1)
                return true
            }
        }
        return false
    }

    return {
        addListener: addListener,
        removeListener: removeListener
    }
}())
