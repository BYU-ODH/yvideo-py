import isObject from './isObject.js'
import root from './.internal/root.js'

/*							******** The MIT License *********
*
* Copyright JS Foundation and other contributors <https://js.foundation/>
*
* Based on Underscore.js, copyright Jeremy Ashkenas,
* DocumentCloud and Investigative Reporters & Editors <http://underscorejs.org/>
*
* This software consists of voluntary contributions made by many
* individuals. For exact contribution history, see the revision history
* available at https://github.com/lodash/lodash
*
* The following license applies to all parts of this software except as
* documented below:
*
* ====
*
* Permission is hereby granted, free of charge, to any person obtaining
* a copy of this software and associated documentation files (the
* "Software"), to deal in the Software without restriction, including
* without limitation the rights to use, copy, modify, merge, publish,
* distribute, sublicense, and/or sell copies of the Software, and to
* permit persons to whom the Software is furnished to do so, subject to
* the following conditions:
*
* The above copyright notice and this permission notice shall be
* included in all copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
* NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
* LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
* OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
* WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
*
* ====
*
* Copyright and related rights for sample code are waived via CC0. Sample
* code is defined as all source code displayed within the prose of the
* documentation.
*
* CC0: http://creativecommons.org/publicdomain/zero/1.0/
*
* ====
*
* Files located in the node_modules and vendor directories are externally
* maintained libraries used by this software which have their own
* licenses; we recommend you read them, as their terms may differ from the
* terms above. */

// PULLED FROM LODASH: https://github.com/lodash/lodash/blob/master/debounce.js

/**
 * Creates a debounced function that delays invoking `func` until after `wait`
 * milliseconds have elapsed since the last time the debounced function was
 * invoked, or until the next browser frame is drawn. The debounced function
 * comes with a `cancel` method to cancel delayed `func` invocations and a
 * `flush` method to immediately invoke them. Provide `options` to indicate
 * whether `func` should be invoked on the leading and/or trailing edge of the
 * `wait` timeout. The `func` is invoked with the last arguments provided to the
 * debounced function. Subsequent calls to the debounced function return the
 * result of the last `func` invocation.
 *
 * **Note:** If `leading` and `trailing` options are `true`, `func` is
 * invoked on the trailing edge of the timeout only if the debounced function
 * is invoked more than once during the `wait` timeout.
 *
 * If `wait` is `0` and `leading` is `false`, `func` invocation is deferred
 * until the next tick, similar to `setTimeout` with a timeout of `0`.
 *
 * If `wait` is omitted in an environment with `requestAnimationFrame`, `func`
 * invocation will be deferred until the next frame is drawn (typically about
 * 16ms).
 *
 * See [David Corbacho's article](https://css-tricks.com/debouncing-throttling-explained-examples/)
 * for details over the differences between `debounce` and `throttle`.
 *
 * @since 0.1.0
 * @category Function
 * @param {Function} func The function to debounce.
 * @param {number} [wait=0]
 *  The number of milliseconds to delay; if omitted, `requestAnimationFrame` is
 *  used (if available).
 * @param {Object} [options={}] The options object.
 * @param {boolean} [options.leading=false]
 *  Specify invoking on the leading edge of the timeout.
 * @param {number} [options.maxWait]
 *  The maximum time `func` is allowed to be delayed before it's invoked.
 * @param {boolean} [options.trailing=true]
 *  Specify invoking on the trailing edge of the timeout.
 * @returns {Function} Returns the new debounced function.
 * @example
 *
 * // Avoid costly calculations while the window size is in flux.
 * jQuery(window).on('resize', debounce(calculateLayout, 150))
 *
 * // Invoke `sendMail` when clicked, debouncing subsequent calls.
 * jQuery(element).on('click', debounce(sendMail, 300, {
 *   'leading': true,
 *   'trailing': false
 * }))
 *
 * // Ensure `batchLog` is invoked once after 1 second of debounced calls.
 * const debounced = debounce(batchLog, 250, { 'maxWait': 1000 })
 * const source = new EventSource('/stream')
 * jQuery(source).on('message', debounced)
 *
 * // Cancel the trailing debounced invocation.
 * jQuery(window).on('popstate', debounced.cancel)
 *
 * // Check for pending invocations.
 * const status = debounced.pending() ? "Pending..." : "Ready"
 */

function debounce(func, wait, options) {
	let lastArgs,
		lastThis,
		maxWait,
		result,
		timerId,
		lastCallTime

	let lastInvokeTime = 0
	let leading = false
	let maxing = false
	let trailing = true

	// Bypass `requestAnimationFrame` by explicitly setting `wait=0`.
	const useRAF = !wait && wait !== 0 && typeof root.requestAnimationFrame === `function`

	if (typeof func !== `function`)
		throw new TypeError(`Expected a function`)
	wait = +wait || 0
	if (isObject(options)) {
		leading = !!options.leading
		maxing = `maxWait` in options
		maxWait = maxing ? Math.max(+options.maxWait || 0, wait) : maxWait
		trailing = `trailing` in options ? !!options.trailing : trailing
	}

	function invokeFunc(time) {
		const args = lastArgs
		const thisArg = lastThis

		lastArgs = lastThis = undefined
		lastInvokeTime = time
		result = func.apply(thisArg, args)
		return result
	}

	function startTimer(pendingFunc, wait) {
		if (useRAF) {
			root.cancelAnimationFrame(timerId)
			return root.requestAnimationFrame(pendingFunc)
		}
		return setTimeout(pendingFunc, wait)
	}

	function cancelTimer(id) {
		if (useRAF)
			return root.cancelAnimationFrame(id)
		clearTimeout(id)
	}

	function leadingEdge(time) {
		// Reset any `maxWait` timer.
		lastInvokeTime = time
		// Start the timer for the trailing edge.
		timerId = startTimer(timerExpired, wait)
		// Invoke the leading edge.
		return leading ? invokeFunc(time) : result
	}

	function remainingWait(time) {
		const timeSinceLastCall = time - lastCallTime
		const timeSinceLastInvoke = time - lastInvokeTime
		const timeWaiting = wait - timeSinceLastCall

		return maxing
			? Math.min(timeWaiting, maxWait - timeSinceLastInvoke)
			: timeWaiting
	}

	function shouldInvoke(time) {
		const timeSinceLastCall = time - lastCallTime
		const timeSinceLastInvoke = time - lastInvokeTime

		// Either this is the first call, activity has stopped and we're at the
		// trailing edge, the system time has gone backwards and we're treating
		// it as the trailing edge, or we've hit the `maxWait` limit.
		return (lastCallTime === undefined || (timeSinceLastCall >= wait) || (timeSinceLastCall < 0) || (maxing && timeSinceLastInvoke >= maxWait)) // eslint-disable-line no-extra-parens
	}

	function timerExpired() {
		const time = Date.now()
		if (shouldInvoke(time))
			return trailingEdge(time)
		// Restart the timer.
		timerId = startTimer(timerExpired, remainingWait(time))
	}

	function trailingEdge(time) {
		timerId = undefined

		// Only invoke if we have `lastArgs` which means `func` has been
		// debounced at least once.
		if (trailing && lastArgs)
			return invokeFunc(time)
		lastArgs = lastThis = undefined
		return result
	}

	function cancel() {
		if (timerId !== undefined)
			cancelTimer(timerId)
		lastInvokeTime = 0
		lastArgs = lastCallTime = lastThis = timerId = undefined
	}

	function flush() {
		return timerId === undefined ? result : trailingEdge(Date.now())
	}

	function pending() {
		return timerId !== undefined
	}

	function debounced(...args) {
		const time = Date.now()
		const isInvoking = shouldInvoke(time)

		lastArgs = args
		lastThis = this
		lastCallTime = time

		if (isInvoking) {
			if (timerId === undefined)
				return leadingEdge(lastCallTime)
			if (maxing) {
				// Handle invocations in a tight loop.
				timerId = startTimer(timerExpired, wait)
				return invokeFunc(lastCallTime)
			}
		}
		if (timerId === undefined)
			timerId = startTimer(timerExpired, wait)
		return result
	}
	debounced.cancel = cancel
	debounced.flush = flush
	debounced.pending = pending
	return debounced
}

export default debounce
