// This file is not intended to be imported into an html page,
// use this as a module to extend functionality of other js scripts
export function formatSecondsToString(timeInSeconds, shouldGiveTimestamp=false) {
  let time;
  if (shouldGiveTimestamp) {
    time = Number(timeInSeconds).toFixed(2);
  } else {
    time = Math.round(timeInSeconds);
  }
  const hours = Math.floor(time / 3600);
  const minutes = Math.floor((time % 3600) / 60);
  const seconds = (time % 60).toFixed(0);
  let decimal = "";

  if (shouldGiveTimestamp) {
    decimal = '.' + Math.round(((time * 100) % 100)).toString().padStart(2, '0');
  }

  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}${decimal}`;
}

export function createElementFromHTML(html, nodeIndex=0) {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content.children[nodeIndex];
}
